import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Creator, Session


def window(env):
    return TestClient(env.client.app, headers={"Origin": env.settings.web_origin})


def start(client):
    response = client.post("/api/v1/auth/window/request")
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    return response.json()["data"]["code"]


def test_two_cookie_jars_require_approval_then_login_with_original_expiry(env):
    with window(env) as target, window(env) as stranger:
        code = start(target)
        assert target.get("/api/v1/auth/me").status_code == 401
        assert target.post("/api/v1/auth/window/complete").json()["data"]["status"] == "pending"
        assert (
            stranger.post("/api/v1/auth/window/approve", json={"code": code, "accepted": True}).status_code
            == 401
        )
        assert env.post("/auth/window/approve", {"code": code, "accepted": True}).status_code == 200
        assert stranger.post("/api/v1/auth/window/complete").status_code == 401
        result = target.post("/api/v1/auth/window/complete")
        assert result.json()["data"]["status"] == "connected"
        assert "csrf_token" not in result.json()["data"]
        assert target.get("/api/v1/auth/me").json()["data"]["creator_id"] == env.owner_id
        assert target.post("/api/v1/auth/window/complete").status_code == 401
        assert env.post("/auth/window/approve", {"code": code, "accepted": True}).status_code == 409
        assert env.client.get("/api/v1/auth/me").status_code == 200
        with env.sf() as db:
            sessions = db.scalars(select(Session).order_by(Session.created_at)).all()
            assert len(sessions) == 2
            assert sessions[1].expires_at <= sessions[0].expires_at


@pytest.mark.parametrize("action", ["revoked", "expired", "inactive"])
def test_approval_cannot_outlive_approver_authority(env, action):
    with window(env) as target:
        code = start(target)
        assert env.post("/auth/window/approve", {"code": code, "accepted": True}).status_code == 200
        with env.sf() as db:
            session = db.scalar(select(Session))
            if action == "revoked":
                session.revoked = True
            elif action == "expired":
                session.expires_at = time.time() - 1
            else:
                db.get(Creator, env.owner_id).active = False
            db.commit()
        assert target.post("/api/v1/auth/window/complete").status_code == 401
        assert target.get("/api/v1/auth/me").status_code == 401


def test_origin_csrf_cancellation_and_replacement(env):
    with window(env) as target:
        assert (
            target.post(
                "/api/v1/auth/window/request", headers={"Origin": "https://other.invalid"}
            ).status_code
            == 403
        )
        old = start(target)
        code = start(target)
        assert env.post("/auth/window/approve", {"code": old, "accepted": True}).status_code == 409
        assert (
            env.client.post(
                "/api/v1/auth/window/approve",
                json={"code": code, "accepted": True},
                headers={"Origin": env.settings.web_origin},
            ).status_code
            == 403
        )
        assert env.post("/auth/window/approve", {"code": code, "accepted": False}).status_code == 409
        assert target.post("/api/v1/auth/window/cancel").status_code == 200
        assert env.post("/auth/window/approve", {"code": code, "accepted": True}).status_code == 409
        assert target.post("/api/v1/auth/window/complete").status_code == 401


def test_expired_pending_request_and_rate_limit(env, monkeypatch):
    from backend.app.api import window_login

    with window(env) as target:
        code = start(target)
        now = time.time()
        monkeypatch.setattr(window_login.time, "time", lambda: now + 301)
        assert target.post("/api/v1/auth/window/complete").status_code == 401
        assert env.post("/auth/window/approve", {"code": code, "accepted": True}).status_code == 409
        for _ in range(10):
            start(target)
        assert target.post("/api/v1/auth/window/request").status_code == 429


def test_production_does_not_expose_local_pairing(env):
    env.settings.app_env = "production"
    with window(env) as target:
        assert target.post("/api/v1/auth/window/request").status_code == 403
