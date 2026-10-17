import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import models as m
from backend.app.core.auth import create_invite
from backend.app.main import create_app


def make_invite(env, days=30):
    with env.sf() as db:
        invite, code = create_invite(db, env.owner_id, days, "test")
        db.commit()
        return invite.id, code


@pytest.fixture
def guest(env):
    with TestClient(create_app(env.settings, env.sf)) as client:
        yield client


def enter(env, client, code):
    return client.post("/api/v1/auth/invite", json={"code": code}, headers={"Origin": env.settings.web_origin})


def write(env, client, csrf, path, body=None):
    headers = {
        "Origin": env.settings.web_origin,
        "X-CSRF-Token": csrf,
        "Idempotency-Key": str(time.time_ns()),
    }
    return client.post("/api/v1" + path, json=body or {}, headers=headers)


def test_guest_reads_owner_workspace_but_not_private_areas(env, guest, task_data):
    _, code = make_invite(env)
    r = enter(env, guest, code.lower().replace("-", " "))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["role"] == "guest"
    assert guest.get("/api/v1/auth/me").json()["data"]["role"] == "guest"
    task_id = task_data["task"]
    for path in ["/tasks", f"/tasks/{task_id}", f"/tasks/{task_id}/texts", "/discoveries", "/ranking/entries"]:
        assert guest.get("/api/v1" + path).status_code == 200, path
    for path in [
        "/jobs",
        "/cost-budget",
        "/cost-records",
        "/probe-voices",
        "/voices",
        "/validation-runs",
        "/openapi.json",
        f"/tasks/{task_id}/delete-preview",
    ]:
        r = guest.get("/api/v1" + path)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "GUEST_FORBIDDEN"
    caps = guest.get("/api/v1/capabilities").json()["data"]
    assert caps["contracts"] == [] and caps["validation_tools_enabled"] is False


def test_guest_cannot_write_but_can_log_out(env, guest):
    _, code = make_invite(env)
    csrf = enter(env, guest, code).json()["data"]["csrf_token"]
    r = write(env, guest, csrf, "/imports", {"repository_url": "https://github.com/a/b"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "GUEST_READ_ONLY"
    with env.sf() as db:
        assert db.scalar(select(m.Job)) is None
    assert write(env, guest, csrf, "/auth/logout").status_code == 204
    assert guest.get("/api/v1/tasks").status_code == 401


def test_guest_only_downloads_formal_audio(env, guest):
    with env.sf() as db:
        for kind in ("voice_sample", "formal_audio"):
            db.add(
                m.Asset(
                    id=kind,
                    owner_id=env.owner_id,
                    data_mode="mock",
                    kind=kind,
                    storage_key=f"private/{kind}.wav",
                    sha256="0" * 64,
                    mime="audio/wav",
                    byte_size=0,
                )
            )
        db.commit()
    _, code = make_invite(env)
    enter(env, guest, code)
    assert guest.get("/api/v1/assets/voice_sample").status_code == 403
    assert guest.get("/api/v1/assets/voice_sample/content").status_code == 403
    assert guest.get("/api/v1/assets/formal_audio").status_code == 200


def test_invite_is_multi_use_and_session_ends_with_invite(env, guest):
    invite_id, code = make_invite(env, days=1)
    assert enter(env, guest, code).status_code == 200
    with TestClient(create_app(env.settings, env.sf)) as second:
        assert enter(env, second, code).status_code == 200
    with env.sf() as db:
        invite = db.get(m.Invite, invite_id)
        assert invite.use_count == 2
        sessions = db.scalars(select(m.Session).where(m.Session.invite_id == invite_id)).all()
        assert all(s.role == "guest" and s.expires_at <= invite.expires_at for s in sessions)
        invite.revoked = True
        db.commit()
    assert guest.get("/api/v1/tasks").status_code == 401
    assert enter(env, guest, code).status_code == 401


def test_expired_or_unknown_invite_rejected(env, guest):
    invite_id, code = make_invite(env)
    with env.sf() as db:
        db.get(m.Invite, invite_id).expires_at = time.time() - 1
        db.commit()
    for attempt in (code, "AAAA-BBBB-CCCC-DDDD"):
        r = enter(env, guest, attempt)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVITE_INVALID"


def test_owner_session_unaffected(env):
    assert env.client.get("/api/v1/auth/me").json()["data"]["role"] == "owner"
    assert env.client.get("/api/v1/jobs").status_code == 200
