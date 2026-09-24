import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app import models as m
from backend.app.core.db import transaction
from backend.app.main import create_app
from backend.app.schemas import RankingCreateIn
from backend.app.services import ranking
from backend.app.services.providers import Providers


@pytest.fixture
def catalog(env, task_data, monkeypatch):
    # Live-mode fixtures exist only in tmp_path; no live provider is called.
    with env.sf() as db:
        for cls in (m.Tool, m.ToolAlias, m.Snapshot, m.Analysis):
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
        db.commit()
        tool = db.get(m.Snapshot, task_data["snapshot"]).tool_id
    env.settings.provider_mode = "live"
    previous = env.client
    with TestClient(create_app(env.settings, env.sf), cookies=previous.cookies) as client:
        env.client = client
        monkeypatch.setattr(Providers, "check", lambda *a, **k: pytest.fail("ranking called provider"))
        yield (
            env,
            {
                "tool_id": tool,
                "source_snapshot_id": task_data["snapshot"],
                "analysis_id": task_data["analysis"],
            },
        )
    env.client = previous


def create(catalog):
    env, body = catalog
    response = env.post("/ranking/entries", body)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]["entry"]


def preview(env, entry):
    response = env.client.get(f"/api/v1/ranking/entries/{entry['id']}/preview")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def publish(env, entry, key=None):
    p = preview(env, entry)
    body = {
        "expected_revision": p["entry_revision"],
        "draft_revision_id": p["draft_revision_id"],
        "content_hash": p["content_hash"],
        "content_reviewed": True,
    }
    response = env.post(f"/ranking/entries/{entry['id']}/publish", body, key=key)
    assert response.status_code == 200, response.text
    return response.json()["data"], body


def public(env):
    return env.client.get("/api/v1/public/tools").json()["data"]["items"]


def test_draft_publish_update_withdraw_restart_and_no_model(catalog):
    env, _ = catalog
    with env.sf() as db:
        before = db.scalar(select(func.count()).select_from(m.Job))
    entry = create(catalog)
    assert public(env) == []
    assert entry["state"] == "draft"
    entry, _ = publish(env, entry)
    old = public(env)[0]
    content = {**entry["content"], "name": "Updated public name"}
    edit = env.post(
        f"/ranking/entries/{entry['id']}",
        {"expected_revision": entry["revision"], "content": content},
        method="PATCH",
    )
    assert edit.status_code == 200, edit.text
    entry = edit.json()["data"]
    assert entry["has_unpublished_changes"] and public(env) == [old]
    entry, _ = publish(env, entry)
    assert public(env)[0]["name"] == content["name"]
    with TestClient(create_app(env.settings, env.sf)) as anonymous:
        assert anonymous.get("/api/v1/public/tools").json()["data"]["items"] == public(env)
        assert anonymous.get("/api/v1/public/tools").headers["cache-control"] == "no-store"
    result = env.post(f"/ranking/entries/{entry['id']}/withdraw", {"expected_revision": entry["revision"]})
    assert result.status_code == 200
    entry = result.json()["data"]
    assert public(env) == []
    assert env.client.get("/api/v1/public/tools/" + entry["public_id"]).status_code == 404
    entry, _ = publish(env, entry)
    assert public(env)[0]["id"] == old["id"]
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Job)) == before
        assert db.scalar(select(func.count()).select_from(m.PublicRevision)) == 2
        assert [
            e.kind for e in db.scalars(select(m.PublicEntryEvent).order_by(m.PublicEntryEvent.created_at))
        ] == ["publish", "update", "withdraw", "update"]


def test_duplicate_concurrent_creation_and_foreign_owner(catalog):
    env, body = catalog

    def run():
        with transaction(env.sf) as db:
            return ranking.create_entry(db, env.owner_id, "live", RankingCreateIn(**body))[0]["entry"]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: run(), range(8)))
    assert len(set(ids)) == 1
    assert create(catalog)["id"] == ids[0]
    with env.sf() as db:
        other = m.Creator(login_name="other", password_hash="not-a-login")
        db.add(other)
        db.flush()
        original = db.get(m.Tool, body["tool_id"])
        tool = m.Tool(
            owner_id=other.id,
            data_mode="live",
            github_repo_id=original.github_repo_id,
            canonical_url=original.canonical_url,
            full_name=original.full_name,
            metadata_json={},
        )
        db.add(tool)
        db.flush()
        with pytest.raises(Exception) as caught:
            ranking.create_entry(db, other.id, "live", RankingCreateIn(**{**body, "tool_id": tool.id}))
        assert caught.value.code == "RANKING_REPOSITORY_CONFLICT"


def test_idempotency_preview_staleness_and_revision_conflict(catalog):
    env, _ = catalog
    entry = create(catalog)
    p = preview(env, entry)
    content = {**entry["content"], "name": "A new name"}
    body = {"expected_revision": entry["revision"], "content": content}
    path = "/ranking/entries/" + entry["id"]
    first = env.post(path, body, method="PATCH", key="same-edit-123")
    again = env.post(path, body, method="PATCH", key="same-edit-123")
    assert first.json()["data"] == again.json()["data"]
    assert (
        env.post(
            path, {**body, "content": {**content, "name": "Other"}}, method="PATCH", key="same-edit-123"
        ).status_code
        == 409
    )
    latest = first.json()["data"]
    stale = {
        "expected_revision": latest["revision"],
        "draft_revision_id": p["draft_revision_id"],
        "content_hash": p["content_hash"],
        "content_reviewed": True,
    }
    assert env.post(path + "/publish", stale).json()["error"]["code"] == "RANKING_PREVIEW_STALE"
    assert env.post(path, body, method="PATCH").status_code == 409
    final, publish_body = publish(env, latest, "same-publication-123")
    assert env.post(path + "/publish", publish_body, key="same-publication-123").json()["data"] == final
    assert env.post(path + "/publish", publish_body).status_code == 409
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.PublicEntryEvent)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"github_url": "javascript:alert(1)"},
        {"github_url": "https://github.com.evil.test/a/b"},
        {"github_url": "https://user:pass@github.com/a/b"},
        {"github_url": "https://github.com/other/repo"},
        {"bot_avatar": {"preset": "../../assets/secret", "palette": "mint"}},
        {"last_verified_at": "2099-01-01"},
        {"highlights": [{"text": "Unsupported", "evidence_refs": ["not-a-fragment"]}]},
    ],
)
def test_invalid_inputs_preserve_draft(catalog, change):
    env, _ = catalog
    entry = create(catalog)
    response = env.post(
        "/ranking/entries/" + entry["id"],
        {"expected_revision": entry["revision"], "content": {**entry["content"], **change}},
        method="PATCH",
    )
    assert response.status_code == 422, response.text
    assert (
        env.client.get("/api/v1/ranking/entries/" + entry["id"]).json()["data"]["revision"]
        == entry["revision"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"name": "   "},
        {"highlights": []},
        {"github_url": ""},
        {"highlights": [{"text": "Claim", "evidence_refs": []}]},
    ],
)
def test_incomplete_drafts_save_but_cannot_publish(catalog, change):
    env, _ = catalog
    entry = create(catalog)
    response = env.post(
        "/ranking/entries/" + entry["id"],
        {"expected_revision": entry["revision"], "content": {**entry["content"], **change}},
        method="PATCH",
    )
    assert response.status_code == 200, response.text
    p = preview(env, response.json()["data"])
    assert p["issues"]
    response = env.post(
        "/ranking/entries/" + entry["id"] + "/publish",
        {
            "expected_revision": p["entry_revision"],
            "draft_revision_id": p["draft_revision_id"],
            "content_hash": p["content_hash"],
            "content_reviewed": True,
        },
    )
    assert response.status_code == 422
    assert public(env) == []


def test_white_list_private_paths_and_mock_isolation(catalog):
    env, body = catalog
    entry = create(catalog)
    entry, _ = publish(env, entry)
    with TestClient(create_app(env.settings, env.sf)) as anon:
        data = anon.get("/api/v1/public/tools/" + entry["public_id"]).json()["data"]
        assert set(data) == {
            "id",
            "version",
            "first_published_at",
            "name",
            "bot_avatar",
            "highlights",
            "github_url",
            "last_verified_at",
            "verification_status",
            "category",
            "one_line_use",
        }
        assert set(data["bot_avatar"]) == {"preset", "palette", "path"}
        serialized = json.dumps(data)
        for secret in [
            entry["id"],
            body["tool_id"],
            body["source_snapshot_id"],
            body["analysis_id"],
            env.owner_id,
        ]:
            assert secret not in serialized
        for path in [
            "/ranking/entries",
            "/ranking/entries/" + entry["id"],
            "/ranking/entries/" + entry["id"] + "/preview",
            "/tasks",
            "/voices",
        ]:
            assert anon.get("/api/v1" + path).status_code in (401, 404)
        assert anon.post("/api/v1/ranking/entries", json=body).status_code == 401
    assert env.client.post("/api/v1/ranking/entries", json=body).status_code == 403
    with env.sf() as db:
        db.get(m.PublicEntry, entry["id"]).data_mode = "mock"
        db.commit()
    assert public(env) == []
    assert env.client.get("/api/v1/public/tools?mode=mock").json()["data"]["items"] == []


def test_cannot_publish_mock_draft(env, task_data):
    with env.sf() as db:
        tool = db.get(m.Snapshot, task_data["snapshot"]).tool_id
    entry = create(
        (
            env,
            {
                "tool_id": tool,
                "source_snapshot_id": task_data["snapshot"],
                "analysis_id": task_data["analysis"],
            },
        )
    )
    p = preview(env, entry)
    assert any("模拟" in issue for issue in p["issues"])
    result = env.post(
        "/ranking/entries/" + entry["id"] + "/publish",
        {
            "expected_revision": p["entry_revision"],
            "draft_revision_id": p["draft_revision_id"],
            "content_hash": p["content_hash"],
            "content_reviewed": True,
        },
    )
    assert result.status_code == 422


def test_source_identity_constraints_and_unavailable_status(catalog):
    env, body = catalog
    entry = create(catalog)
    with env.sf() as db:
        db.add(
            m.PublicRevision(
                entry_id=entry["id"],
                version=100,
                content_json={},
                content_hash="a" * 64,
                source_snapshot_id=body["source_snapshot_id"],
                analysis_id=body["analysis_id"],
            )
        )
        db.flush()
        tool = m.Tool(
            owner_id=env.owner_id,
            data_mode="live",
            github_repo_id=999999,
            canonical_url="https://github.com/demo/other",
            full_name="demo/other",
            metadata_json={},
        )
        db.add(tool)
        db.flush()
        other = m.PublicEntry(
            owner_id=env.owner_id, data_mode="live", tool_id=tool.id, github_repo_id=tool.github_repo_id
        )
        db.add(other)
        db.flush()
        other.draft_revision_id = entry["draft_revision_id"]
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with env.sf() as db:
        snap = db.get(m.Snapshot, body["source_snapshot_id"])
        original_time = snap.created_at
        snap.payload = {**snap.payload, "missing_fields": ["UPSTREAM_404"]}
        db.commit()
    entry = env.post(
        "/ranking/entries/" + entry["id"],
        {"expected_revision": entry["revision"], "content": {**entry["content"], "name": "Rechecked source"}},
        method="PATCH",
    ).json()["data"]
    p = preview(env, entry)
    assert p["tool"]["verification_status"] == "unavailable"
    from backend.app.services.common import iso

    assert p["tool"]["last_verified_at"] == iso(original_time)


def test_pagination_bad_cursor_and_published_only(catalog):
    env, _ = catalog
    entry = create(catalog)
    publish(env, entry)
    assert env.client.get("/api/v1/public/tools?cursor=invalid").status_code == 422
    invalid = base64.urlsafe_b64encode(json.dumps([1, {}]).encode()).decode()
    assert env.client.get("/api/v1/public/tools?cursor=" + invalid).status_code == 422
    assert env.client.get("/api/v1/public/tools?limit=101").status_code == 422
    assert env.client.get("/api/v1/ranking/entries?state=bogus").status_code == 422


def test_bloub_appearance_saves_without_publishing_or_calling_model(catalog):
    from pathlib import Path

    env, _ = catalog
    entry = create(catalog)
    content = {**entry["content"], "bot_avatar": {"preset": "cloud", "palette": "pink"}}
    response = env.post(
        f"/ranking/entries/{entry['id']}",
        {"expected_revision": entry["revision"], "content": content},
        method="PATCH",
    )
    assert response.status_code == 200
    saved = response.json()["data"]
    assert saved["content"]["bot_avatar"] == content["bot_avatar"]
    assert public(env) == []
    view = preview(env, saved)["tool"]
    assert view["bot_avatar"]["path"] == "/bots/cloud-pink.svg"
    asset = Path(__file__).resolve().parents[2] / "frontend/public/bots/cloud-pink.svg"
    assert asset.is_file() and 'fill="#e152b0"' in asset.read_text()
