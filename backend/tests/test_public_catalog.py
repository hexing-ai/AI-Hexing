import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.main import create_app
from backend.app.services.public_events import EventLimiter
from backend.tests.test_ranking import catalog as catalog
from backend.tests.test_ranking import create, publish


def send(env, entry, body=None, **kwargs):
    headers = {
        "Origin": env.settings.web_origin,
        "Content-Type": "application/json",
        **kwargs.pop("headers", {}),
    }
    return env.client.post(
        "/api/v1/public/tools/" + entry["public_id"] + "/events",
        content=json.dumps(
            body or {"event_id": str(uuid4()), "version": entry["published"]["version"], "kind": "intro_open"}
        ),
        headers=headers,
        **kwargs,
    )


def test_anonymous_events_separate_dedupe_stats_and_restart(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    body = {"event_id": str(uuid4()), "version": entry["published"]["version"], "kind": "intro_open"}
    env.client.cookies.clear()
    assert send(env, entry, body).status_code == 202
    assert send(env, entry, body).status_code == 202
    assert send(env, entry, {**body, "kind": "github_click"}).status_code == 409
    assert send(env, entry, {**body, "event_id": str(uuid4()), "kind": "github_click"}).status_code == 202
    with env.sf() as db:
        rows = list(db.scalars(select(m.PublicInteractionEvent)))
        assert len(rows) == 2 and {e.kind for e in rows} == {"intro_open", "github_click"}
        assert set(m.PublicInteractionEvent.__table__.columns.keys()) == {
            "id",
            "created_at",
            "event_id",
            "entry_id",
            "revision_id",
            "kind",
        }
    assert env.client.get("/api/v1/ranking/entries/" + entry["id"] + "/stats").status_code == 401
    with TestClient(create_app(env.settings, env.sf)) as restarted:
        response = restarted.post(
            "/api/v1/auth/login",
            json={"login_name": "tester", "password": "test-password-only"},
            headers={"Origin": env.settings.web_origin},
        )
        assert response.status_code == 200
        assert restarted.get("/api/v1/ranking/entries/" + entry["id"] + "/stats").json()["data"] == {
            "intro_open": 1,
            "github_click": 1,
            "retention_days": 30,
        }


@pytest.mark.parametrize(
    "body",
    [
        {"event_id": "bad", "version": 1, "kind": "intro_open"},
        {"event_id": str(uuid4()), "version": True, "kind": "intro_open"},
        {"event_id": str(uuid4()), "version": 1, "kind": "publish"},
        {"event_id": str(uuid4()), "version": 1, "kind": "intro_open", "private_note": "unwanted"},
    ],
)
def test_event_validation(catalog, body):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    assert send(env, entry, body).status_code == 422
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.PublicInteractionEvent)) == 0


def test_event_bounds_origin_and_body(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    assert send(env, entry, headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert send(env, entry, headers={"Content-Type": "text/plain"}).status_code == 415
    path = "/api/v1/public/tools/" + entry["public_id"] + "/events"
    assert (
        env.client.post(
            path,
            content="bad",
            headers={"Origin": env.settings.web_origin, "Content-Type": "application/json"},
        ).status_code
        == 422
    )
    assert (
        env.client.post(
            path,
            content=" " * 1025,
            headers={
                "Origin": env.settings.web_origin,
                "Content-Type": "application/json",
                "Content-Length": "1",
            },
        ).status_code
        == 413
    )
    assert env.client.post(path, json={}).status_code == 403


def test_event_version_withdraw_and_modes(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    assert (
        send(env, entry, {"event_id": str(uuid4()), "version": 999, "kind": "intro_open"}).status_code == 409
    )
    env.post("/ranking/entries/" + entry["id"] + "/withdraw", {"expected_revision": entry["revision"]})
    assert send(env, entry).status_code == 404
    with env.sf() as db:
        obj = db.get(m.PublicEntry, entry["id"])
        obj.state = "published"
        obj.data_mode = "mock"
        db.commit()
    assert send(env, entry).status_code == 404


def test_rate_limits_bounded_memory_and_forwarded_spoof(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    env.settings.public_event_source_per_minute = 2
    for value in ("1.2.3.4", "2.3.4.5"):
        assert send(env, entry, headers={"X-Forwarded-For": value}).status_code == 202
    assert send(env, entry, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 429
    assert env.client.get("/api/v1/public/tools").status_code == 200
    env.settings.public_event_total_per_minute = 3
    now = [0]
    limiter = EventLimiter(env.settings, clock=lambda: now[0])
    for i in range(3):
        limiter.admit(str(i))
    for i in range(50):
        with pytest.raises(AppError):
            limiter.admit("blocked-" + str(i))
    assert len(limiter.sources) == 3
    now[0] = 60
    limiter.admit("new")
    assert len(limiter.sources) == 1


def test_event_cleanup_capacity_and_stats_window(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    with env.sf() as db:
        old = m.PublicInteractionEvent(
            event_id=str(uuid4()),
            entry_id=entry["id"],
            revision_id=entry["draft_revision_id"],
            kind="intro_open",
            created_at=time.time() - 31 * 86400,
        )
        db.add(old)
        db.commit()
    assert (
        env.client.get("/api/v1/ranking/entries/" + entry["id"] + "/stats").json()["data"]["intro_open"] == 0
    )
    env.settings.public_event_max_rows = 1
    assert send(env, entry).status_code == 202
    assert send(env, entry).status_code == 503
    assert env.client.get("/api/v1/public/tools/" + entry["public_id"]).status_code == 200
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.PublicInteractionEvent)) == 1


def test_event_parallel_idempotency(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    body = {"event_id": str(uuid4()), "version": entry["published"]["version"], "kind": "intro_open"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: send(env, entry, body).status_code, range(8)))
    assert codes == [202] * 8
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.PublicInteractionEvent)) == 1


def test_public_pagination_can_reach_all_and_remove_withdrawn(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    # An independent directory fixture, not model-generated data or the real catalog.
    with env.sf() as db:
        source = db.get(m.PublicEntry, entry["id"])
        rev = db.get(m.PublicRevision, source.published_revision_id)
        for n in range(119):
            tool = m.Tool(
                owner_id=env.owner_id,
                data_mode="live",
                github_repo_id=900000 + n,
                canonical_url=f"https://github.com/test/tool-{n}",
                full_name=f"Test {n}",
                metadata_json={},
            )
            db.add(tool)
            db.flush()
            item = m.PublicEntry(
                owner_id=env.owner_id,
                data_mode="live",
                tool_id=tool.id,
                github_repo_id=tool.github_repo_id,
                first_published_at=source.first_published_at + n + 1,
            )
            db.add(item)
            db.flush()
            version = m.PublicRevision(
                entry_id=item.id,
                version=1,
                content_json=rev.content_json,
                content_hash=rev.content_hash,
                source_snapshot_id=rev.source_snapshot_id,
                analysis_id=rev.analysis_id,
            )
            db.add(version)
            db.flush()
            item.draft_revision_id = version.id
            item.published_revision_id = version.id
            item.state = "published"
        db.commit()
    ids = []
    cursor = None
    for _ in range(3):
        data = env.client.get(
            "/api/v1/public/tools", params={"limit": 50, **({"cursor": cursor} if cursor else {})}
        ).json()["data"]
        ids.extend(t["id"] for t in data["items"])
        cursor = data["next_cursor"]
    assert cursor is None and len(set(ids)) == 120
    env.post("/ranking/entries/" + entry["id"] + "/withdraw", {"expected_revision": entry["revision"]})
    assert env.client.get("/api/v1/public/tools/" + entry["public_id"]).status_code == 404
