import hmac
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select

from backend.app.core.errors import AppError
from backend.app.models import Creator, Invite, LoginAttempt, Session
from backend.app.services.common import digest

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("constant-dummy-password-never-valid")

ATTEMPT_WINDOW_SECONDS = 600
ACCOUNT_LIMIT = 10
CLIENT_LIMIT = 20
GLOBAL_LIMIT = 200
GLOBAL_KEY = "global"

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_DAYS = 30

# Guests may only read the owner's content. Processing records, costs, voices and
# provider settings stay owner-only; new endpoints are denied until listed here.
GUEST_READABLE = re.compile(
    r"^/api/v1/("
    r"auth/me|capabilities"
    r"|tasks(/[^/]+(/(texts|texts/[^/]+/download|audio|lifecycle|context|context/sources|context/history))?)?"
    r"|snapshots/[^/]+|analyses/[^/]+|platform-rules/[^/]+"
    r"|discoveries(/[^/]+)?"
    r"|ranking/entries(/[^/]+(/(stats|preview))?)?"
    r"|assets/[^/]+(/content)?|exports/[^/]+/content"
    r")$"
)


def client_address(request, settings):
    peer = request.client.host if request.client else "local"
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in settings.trusted_proxy_hosts and forwarded:
        # The nearest trusted proxy appends the real peer last; earlier entries are client-supplied.
        return forwarded.split(",")[-1].strip() or peer
    return peer


def check_attempts(db, limits):
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < time.time() - ATTEMPT_WINDOW_SECONDS))
    for key, limit in limits:
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key == key))
        if count >= limit:
            raise AppError("LOGIN_RATE_LIMIT", "登录尝试过多，请十分钟后再试。", 429)


def record_failure(db, keys):
    db.add_all(LoginAttempt(key=key) for key in keys)
    db.commit()  # Preserve rate-limit evidence even though the request fails.


def open_session(db, owner_id, expires_at, role="owner", invite_id=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        Session(
            owner_id=owner_id,
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            expires_at=expires_at,
            role=role,
            invite_id=invite_id,
        )
    )
    db.flush()
    return token, csrf


def login(db, settings, login_name, password, client):
    account_key = digest("account:" + login_name.strip().lower())
    client_key = digest("client:" + client)
    check_attempts(db, ((account_key, ACCOUNT_LIMIT), (client_key, CLIENT_LIMIT), (GLOBAL_KEY, GLOBAL_LIMIT)))
    creator = db.scalar(select(Creator).where(Creator.login_name == login_name, Creator.active.is_(True)))
    try:
        hasher.verify(creator.password_hash if creator else DUMMY_HASH, password)
        valid = creator is not None
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        record_failure(db, (account_key, client_key, GLOBAL_KEY))
        raise AppError("INVALID_CREDENTIALS", "账户或密码不正确。", 401)
    db.execute(delete(LoginAttempt).where(LoginAttempt.key == account_key))
    token, csrf = open_session(db, creator.id, time.time() + settings.session_ttl_seconds)
    return creator, token, csrf


def invite_hash(code):
    return digest("invite:" + code.replace("-", "").replace(" ", "").upper())


def create_invite(db, owner_id, days=INVITE_DAYS, label=""):
    raw = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(16))
    code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
    invite = Invite(
        owner_id=owner_id, code_hash=invite_hash(code), label=label, expires_at=time.time() + days * 86400
    )
    db.add(invite)
    db.flush()
    return invite, code


def usable_invite(db, invite_id, now):
    invite = db.get(Invite, invite_id) if invite_id else None
    if not invite or invite.revoked or invite.expires_at <= now:
        return None
    return invite


def invite_login(db, settings, code, client):
    client_key = digest("client:" + client)
    check_attempts(db, ((client_key, CLIENT_LIMIT), (GLOBAL_KEY, GLOBAL_LIMIT)))
    now = time.time()
    found = db.scalar(select(Invite).where(Invite.code_hash == invite_hash(code)))
    invite = usable_invite(db, found.id if found else None, now)
    creator = db.get(Creator, invite.owner_id) if invite else None
    if not invite or not creator or not creator.active:
        record_failure(db, (client_key, GLOBAL_KEY))
        raise AppError("INVITE_INVALID", "邀请码无效或已过期。", 401)
    invite.use_count += 1
    invite.last_used_at = now
    expires_at = min(now + settings.session_ttl_seconds, invite.expires_at)
    token, csrf = open_session(db, creator.id, expires_at, role="guest", invite_id=invite.id)
    return creator, token, csrf


def authenticate(db, request, settings, *, write=False, guest_write=False):
    token = request.cookies.get("hexing_session", "")
    now = time.time()
    row = db.scalar(
        select(Session).where(
            Session.token_hash == digest(token), Session.revoked.is_(False), Session.expires_at > now
        )
    )
    if not row:
        raise AppError("UNAUTHENTICATED", "请先登录。", 401)
    creator = db.get(Creator, row.owner_id)
    if not creator or not creator.active:
        raise AppError("UNAUTHENTICATED", "账户已停用。", 401)
    if row.role == "guest":
        if not usable_invite(db, row.invite_id, now):
            raise AppError("UNAUTHENTICATED", "邀请码已失效，请重新获取。", 401)
        if write and not guest_write:
            raise AppError("GUEST_READ_ONLY", "访客模式只能查看，不能修改或生成。", 403)
        if not write and (request.method != "GET" or not GUEST_READABLE.match(request.url.path)):
            raise AppError("GUEST_FORBIDDEN", "访客模式不能查看这部分内容。", 403)
    if write:
        csrf = request.headers.get("x-csrf-token", "")
        if request.headers.get("origin") != settings.web_origin or not hmac.compare_digest(
            row.csrf_hash, digest(csrf)
        ):
            raise AppError("CSRF_INVALID", "会话校验失败，请刷新页面后重试。", 403)
    return creator, row
