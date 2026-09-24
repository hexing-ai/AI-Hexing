"""Short-lived, explicitly approved window pairing for the local single-process app."""

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request
from pydantic import BaseModel, Field

from backend.app.core.auth import authenticate
from backend.app.core.db import transaction
from backend.app.core.errors import AppError
from backend.app.models import Creator, Session
from backend.app.services.common import digest

COOKIE = "hexing_window_request"
COOKIE_PATH = "/api/v1/auth/window"
TTL = 300
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class PendingWindow:
    code: str
    expires_at: float
    approver_session_id: str | None = None


class Approval(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    accepted: bool


def register_window_login(app, settings, sf, ok):
    # Intentionally ephemeral: restart invalidates pending requests, not existing logins.
    pending: dict[str, PendingWindow] = {}
    lock = threading.Lock()
    starts, approvals = deque(), deque()

    def boundary(request):
        if settings.app_env not in ("development", "test"):
            raise AppError("WINDOW_LOGIN_DISABLED", "窗口连接仅在本地工作台开放。", 403)
        if request.headers.get("origin") != settings.web_origin:
            raise AppError("ORIGIN_INVALID", "登录来源不受信任。", 403)

    def prune(now):
        for key in list(pending):
            if pending[key].expires_at <= now:
                del pending[key]

    def limited(events, now):
        while events and events[0] <= now - 60:
            events.popleft()
        if len(events) >= 10:
            raise AppError("WINDOW_RATE_LIMIT", "操作过于频繁，请一分钟后再试。", 429)
        events.append(now)

    @app.post("/api/v1/auth/window/request")
    def start(request: Request):
        boundary(request)
        with lock:
            now = time.time()
            prune(now)
            limited(starts, now)
            old = request.cookies.get(COOKIE)
            if old:
                pending.pop(digest(old), None)
            proof = secrets.token_urlsafe(32)
            code = "".join(secrets.choice(ALPHABET) for _ in range(8))
            while any(row.code == code for row in pending.values()):
                code = "".join(secrets.choice(ALPHABET) for _ in range(8))
            pending[digest(proof)] = PendingWindow(code, now + TTL)
        response = ok(request, {"code": code[:4] + "-" + code[4:], "expires_in": TTL})
        response.set_cookie(COOKIE, proof, httponly=True, samesite="strict", max_age=TTL, path=COOKIE_PATH)
        return response

    @app.post("/api/v1/auth/window/approve")
    def approve(request: Request, body: Approval):
        boundary(request)
        with lock, transaction(sf) as db:
            _, session = authenticate(db, request, settings, write=True)
            now = time.time()
            prune(now)
            limited(approvals, now)
            code = body.code.replace("-", "").replace(" ", "").upper()
            row = next((row for row in pending.values() if row.code == code), None)
            if not body.accepted or row is None or row.approver_session_id is not None:
                raise AppError("WINDOW_CODE_INVALID", "配对码无效、已使用或已过期，请核对目标窗口。", 409)
            row.approver_session_id = session.id
        return ok(request, {"status": "approved"})

    @app.post("/api/v1/auth/window/complete")
    def complete(request: Request):
        boundary(request)
        key = digest(request.cookies.get(COOKIE, ""))
        with lock:
            now = time.time()
            prune(now)
            row = pending.get(key)
            if row is None:
                raise AppError(
                    "WINDOW_REQUEST_EXPIRED", "窗口请求已过期或浏览器未保存凭据，请重新生成配对码。", 401
                )
            if row.approver_session_id is None:
                return ok(request, {"status": "pending"})
            with transaction(sf) as db:
                approver = db.get(Session, row.approver_session_id)
                creator = db.get(Creator, approver.owner_id) if approver else None
                if (
                    not approver
                    or approver.revoked
                    or approver.expires_at <= now
                    or not creator
                    or not creator.active
                ):
                    pending.pop(key)
                    raise AppError("WINDOW_APPROVAL_EXPIRED", "授权窗口的登录已失效，请重新授权。", 401)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                expires_at = min(approver.expires_at, now + settings.session_ttl_seconds)
                db.add(
                    Session(
                        owner_id=creator.id,
                        token_hash=digest(token),
                        csrf_hash=digest(csrf),
                        expires_at=expires_at,
                    )
                )
            pending.pop(key)
        response = ok(request, {"status": "connected"})
        for name, value in (("hexing_session", token), ("hexing_csrf", csrf)):
            response.set_cookie(
                name, value, httponly=True, samesite="strict", max_age=max(1, int(expires_at - now)), path="/"
            )
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return response

    @app.post("/api/v1/auth/window/cancel")
    def cancel(request: Request):
        boundary(request)
        with lock:
            pending.pop(digest(request.cookies.get(COOKIE, "")), None)
        response = ok(request, {"status": "cancelled"})
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return response
