import hmac
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select

from backend.app.core.errors import AppError
from backend.app.models import Creator, LoginAttempt, Session
from backend.app.services.common import digest

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("constant-dummy-password-never-valid")

ATTEMPT_WINDOW_SECONDS = 600
ACCOUNT_LIMIT = 10
CLIENT_LIMIT = 20
GLOBAL_LIMIT = 200
GLOBAL_KEY = "global"


def client_address(request, settings):
    peer = request.client.host if request.client else "local"
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in settings.trusted_proxy_hosts and forwarded:
        # The nearest trusted proxy appends the real peer last; earlier entries are client-supplied.
        return forwarded.split(",")[-1].strip() or peer
    return peer


def login(db, settings, login_name, password, client):
    account_key = digest("account:" + login_name.strip().lower())
    client_key = digest("client:" + client)
    now = time.time()
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now - ATTEMPT_WINDOW_SECONDS))
    for key, limit in ((account_key, ACCOUNT_LIMIT), (client_key, CLIENT_LIMIT), (GLOBAL_KEY, GLOBAL_LIMIT)):
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key == key))
        if count >= limit:
            raise AppError("LOGIN_RATE_LIMIT", "登录尝试过多，请十分钟后再试。", 429)
    creator = db.scalar(select(Creator).where(Creator.login_name == login_name, Creator.active.is_(True)))
    try:
        hasher.verify(creator.password_hash if creator else DUMMY_HASH, password)
        valid = creator is not None
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        db.add_all(LoginAttempt(key=key) for key in (account_key, client_key, GLOBAL_KEY))
        db.commit()  # Preserve rate-limit evidence even though the request fails.
        raise AppError("INVALID_CREDENTIALS", "账户或密码不正确。", 401)
    db.execute(delete(LoginAttempt).where(LoginAttempt.key == account_key))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = Session(
        owner_id=creator.id,
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        expires_at=now + settings.session_ttl_seconds,
    )
    db.add(row)
    db.flush()
    return creator, token, csrf


def authenticate(db, request, settings, *, write=False):
    token = request.cookies.get("hexing_session", "")
    row = db.scalar(
        select(Session).where(
            Session.token_hash == digest(token), Session.revoked.is_(False), Session.expires_at > time.time()
        )
    )
    if not row:
        raise AppError("UNAUTHENTICATED", "请先登录。", 401)
    creator = db.get(Creator, row.owner_id)
    if not creator or not creator.active:
        raise AppError("UNAUTHENTICATED", "账户已停用。", 401)
    if write:
        csrf = request.headers.get("x-csrf-token", "")
        if request.headers.get("origin") != settings.web_origin or not hmac.compare_digest(
            row.csrf_hash, digest(csrf)
        ):
            raise AppError("CSRF_INVALID", "会话校验失败，请刷新页面后重试。", 403)
    return creator, row
