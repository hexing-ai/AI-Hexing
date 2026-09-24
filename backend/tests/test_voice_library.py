import time

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.services.assets import save_asset
from backend.app.services.providers import wav_tone


def voice(env, **extra):
    with env.sf() as db:
        asset = save_asset(db, env.settings, env.owner_id, "mock", wav_tone(6), "voice_sample")
        values = dict(
            owner_id=env.owner_id,
            data_mode="mock",
            sample_asset_id=asset.id,
            provider_scope=env.worker.providers.scope(),
            provider_voice_id="private-provider-id",
            status="ready",
            preview_confirmed_at=time.time(),
        )
        values.update(extra)
        row = m.ProbeVoice(**values)
        db.add(row)
        db.commit()
        return row.id


def listing(env):
    return env.client.get("/api/v1/voices").json()["data"]


def test_names_defaults_and_conflicts_do_not_change_voice_identity_or_call_model(env):
    first, second = voice(env), voice(env)
    renamed = env.post(
        f"/voices/{first}/metadata",
        {"display_name": "日常口播", "expected_revision": 0},
        key="same-name-operation",
    )
    assert renamed.status_code == 200
    assert (
        env.post(
            f"/voices/{first}/metadata",
            {"display_name": "日常口播", "expected_revision": 0},
            key="same-name-operation",
        ).json()["data"]
        == renamed.json()["data"]
    )
    assert (
        env.post(f"/voices/{first}/metadata", {"display_name": "旧写入", "expected_revision": 0}).status_code
        == 409
    )
    assert (
        env.post("/voice-preferences", {"default_voice_id": first, "expected_revision": 0}).status_code == 200
    )
    assert (
        env.post("/voice-preferences", {"default_voice_id": second, "expected_revision": 0}).status_code
        == 409
    )
    assert (
        env.post("/voice-preferences", {"default_voice_id": second, "expected_revision": 1}).status_code
        == 200
    )
    data = listing(env)
    assert data["default_voice_id"] == second
    assert sum(v["is_default"] for v in data["items"]) == 1
    assert next(v for v in data["items"] if v["id"] == first)["display_name"] == "日常口播"
    assert "private-provider-id" not in str(data)
    with env.sf() as db:
        assert db.get(m.ProbeVoice, first).revision == 1
        assert db.get(m.ProbeVoice, first).provider_voice_id == "private-provider-id"
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0
    assert env.post("/voice-preferences", {"default_voice_id": "", "expected_revision": 2}).status_code == 422
    assert env.post(f"/probe-voices/{second}/disable", {"expected_revision": 1}).status_code == 200
    assert listing(env)["default_voice_id"] is None
    assert listing(env)["settings_revision"] == 3


def test_eligibility_owner_mode_and_auth_are_enforced(env):
    ids = [
        voice(env, status="awaiting_preview"),
        voice(env, preview_confirmed_at=None),
        voice(env, provider_scope={"region": "different"}),
    ]
    for id in ids:
        assert (
            env.post("/voice-preferences", {"default_voice_id": id, "expected_revision": 0}).status_code
            == 422
        )
    othermode = voice(env, data_mode="live")
    with env.sf() as db:
        other = m.Creator(login_name="other", password_hash="unused")
        db.add(other)
        db.commit()
        owner = other.id
    otherowner = voice(env, owner_id=owner)
    for id in [othermode, otherowner]:
        assert (
            env.post(
                f"/voices/{id}/metadata", {"display_name": "not-mine", "expected_revision": 0}
            ).status_code
            == 404
        )
        assert (
            env.post("/voice-preferences", {"default_voice_id": id, "expected_revision": 0}).status_code
            == 404
        )
    assert {v["id"] for v in listing(env)["items"]} == set(ids)
    with TestClient(env.client.app) as anonymous:
        assert anonymous.get("/api/v1/voices").status_code == 401
        assert (
            anonymous.post(
                "/api/v1/voice-preferences", json={"default_voice_id": ids[0], "expected_revision": 0}
            ).status_code
            == 401
        )
    assert (
        env.client.post(
            f"/api/v1/voices/{ids[0]}/metadata",
            json={"display_name": "x", "expected_revision": 0},
            headers={"Origin": env.settings.web_origin},
        ).status_code
        == 403
    )
    assert (
        env.post(f"/voices/{ids[0]}/metadata", {"display_name": "  ", "expected_revision": 0}).status_code
        == 422
    )


def test_preview_is_private_and_formal_audio_listing_uses_names(env, task_data):
    id = voice(env)
    with env.sf() as db:
        a = save_asset(db, env.settings, env.owner_id, "mock", wav_tone(6), "voice_preview")
        j = m.Job(
            owner_id=env.owner_id,
            data_mode="mock",
            kind="voice_preview",
            input_json={},
            input_hash="fixture",
            status="succeeded",
        )
        db.add(j)
        db.flush()
        db.add(
            m.ValidationRun(
                owner_id=env.owner_id,
                data_mode="mock",
                kind="voice_preview",
                job_id=j.id,
                contract_version="S1-2026-09-08",
                input_ref={"probe_voice_id": id},
                output={"asset_id": a.id},
            )
        )
        db.commit()
        asset_id = a.id
    assert (
        env.post(f"/voices/{id}/metadata", {"display_name": "我的旁白", "expected_revision": 0}).status_code
        == 200
    )
    assert env.post("/voice-preferences", {"default_voice_id": id, "expected_revision": 0}).status_code == 200
    v = listing(env)["items"][0]
    assert v["preview"]["download_path"] == f"/api/v1/assets/{asset_id}/content"
    assert env.client.get(v["preview"]["download_path"]).status_code == 200
    with TestClient(env.client.app) as anonymous:
        assert anonymous.get(v["preview"]["download_path"]).status_code == 401
    audio = env.client.get(f"/api/v1/tasks/{task_data['task']}/audio").json()["data"]
    assert audio["voices"][0]["display_name"] == "我的旁白"
    assert audio["voices"][0]["is_default"]


def test_populated_s3_migration_preserves_all_existing_data(env, pre_ranking_schema):
    import os
    import subprocess
    import sys

    from sqlalchemy import inspect, text

    from backend.app.core.config import ROOT

    voice(env)
    engine = env.sf.kw["bind"]
    m.VoicePreference.__table__.drop(engine)
    m.VoiceMetadata.__table__.drop(engine)
    names = inspect(engine).get_table_names()
    with engine.connect() as db:
        before = {
            name: sorted([tuple(r) for r in db.execute(text('SELECT * FROM "' + name + '"'))], key=repr)
            for name in names
        }
    environment = {
        **os.environ,
        "DATABASE_URL": env.settings.database_url,
        "DATA_DIR": str(env.settings.data_dir),
    }
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini"]
    for arguments in [["stamp", "0003"], ["upgrade", "head"], ["upgrade", "head"]]:
        subprocess.run([*command, *arguments], cwd=ROOT, env=environment, capture_output=True, check=True)
    with engine.connect() as db:
        after = {
            name: sorted([tuple(r) for r in db.execute(text('SELECT * FROM "' + name + '"'))], key=repr)
            for name in names
        }
        assert before == after
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    assert {"voice_preferences", "voice_metadata"} <= set(inspect(engine).get_table_names())


def test_remove_from_library_only_is_atomic_private_and_keeps_voice_and_sample(env):
    from backend.app.services.assets import asset_path

    first, pending = voice(env), voice(env, status="remote_deletion_pending")
    foreign = voice(env, data_mode="live")
    env.post(f"/voices/{first}/metadata", {"display_name": "保留声音", "expected_revision": 0})
    env.post("/voice-preferences", {"default_voice_id": first, "expected_revision": 0})
    with env.sf() as db:
        baseline = {
            id: (
                db.get(m.ProbeVoice, id).status,
                db.get(m.ProbeVoice, id).revision,
                db.get(m.ProbeVoice, id).provider_voice_id,
            )
            for id in (first, pending)
        }
        assets = [db.get(m.Asset, db.get(m.ProbeVoice, id).sample_asset_id) for id in (first, pending)]
        files = {a.id: (a.state, asset_path(env.settings, a.storage_key).read_bytes()) for a in assets}
    path = "/voices/list-removals"
    assert env.post(path, {"voice_ids": [first, foreign], "confirm": True}).status_code == 404
    assert {v["id"] for v in listing(env)["items"]} == {first, pending}
    response = env.post(path, {"voice_ids": [first, pending, first], "confirm": True})
    assert response.status_code == 200
    assert response.json()["data"]["removed_ids"] == [first, pending]
    assert listing(env)["items"] == []
    assert env.post(path, {"voice_ids": [first, pending], "confirm": True}).status_code == 200
    assert env.post(path, {"voice_ids": [], "confirm": True}).status_code == 422
    assert env.post(path, {"voice_ids": [first], "confirm": False}).status_code == 422
    with env.sf() as db:
        for id in (first, pending):
            v = db.get(m.ProbeVoice, id)
            assert (v.status, v.revision, v.provider_voice_id) == baseline[id]
            a = db.get(m.Asset, v.sample_asset_id)
            assert (a.state, asset_path(env.settings, a.storage_key).read_bytes()) == files[a.id]
        assert db.scalar(select(m.VoicePreference)).default_voice_id == first
        meta = db.scalar(select(m.VoiceMetadata).where(m.VoiceMetadata.voice_id == first))
        assert meta.display_name == "保留声音" and meta.revision == 1
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0
