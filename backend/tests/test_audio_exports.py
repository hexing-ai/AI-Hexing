import io
import json
import time
import zipfile

import pytest
from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import ProviderError
from backend.app.services.assets import asset_path, save_asset
from backend.app.services.providers import wav_tone
from backend.tests.test_creation import generate, move, texts
from backend.tests.test_text_history import edit, preview


@pytest.fixture
def ready(env, task_data):
    task = task_data["task"]
    generate(env, task)
    for kind in ("script", "copy"):
        v = next(v for v in texts(env, task)["versions"] if v["kind"] == kind)
        assert move(env, task, v["id"], "adopt").status_code == 200
        assert move(env, task, v["id"], "confirm").status_code == 200
    with env.sf() as db:
        a = save_asset(db, env.settings, env.owner_id, "mock", wav_tone(6), "voice_sample")
        v = m.ProbeVoice(
            owner_id=env.owner_id,
            data_mode="mock",
            sample_asset_id=a.id,
            provider_voice_id="private-fixture-voice",
            provider_scope=env.worker.providers.scope(),
            status="ready",
            preview_confirmed_at=time.time(),
        )
        db.add(v)
        db.commit()
        voice = v.id
    return task, voice


def inputs(env, ready, **extra):
    task, voice = ready
    t = texts(env, task)["task"]
    return {
        "task_id": task,
        "script_id": t["current_script"]["id"],
        "probe_voice_id": voice,
        "voice_revision": 1,
        "expected_revision": t["revision"],
        **extra,
    }


def submit(env, ready, **extra):
    task, _ = ready
    r = env.post(f"/tasks/{task}/audio-generations", inputs(env, ready, **extra))
    assert r.status_code == 202, r.text
    return r.json()["data"]


def audio_data(env, ready):
    return env.client.get(f"/api/v1/tasks/{ready[0]}/audio").json()["data"]


def confirm(env, ready, id):
    return env.post(
        f"/tasks/{ready[0]}/audio/{id}/confirm",
        {"expected_revision": texts(env, ready[0])["task"]["revision"], "accepted": True},
    )


def export(env, ready, complete=True):
    r = env.post(
        f"/tasks/{ready[0]}/exports",
        {"expected_revision": texts(env, ready[0])["task"]["revision"], "complete": complete},
    )
    assert r.status_code == 202, r.text
    return r.json()["data"]


@pytest.mark.parametrize(
    "duration,valid", [(29.9, False), (30, True), (35, True), (35.1, False), (29.99996, False)]
)
def test_actual_duration_confirmation_boundaries(env, ready, monkeypatch, duration, valid):
    monkeypatch.setattr(
        env.worker.providers, "synthesize", lambda *a, **k: (wav_tone(duration), {"model": "mock-tone"})
    )
    result = submit(env, ready)
    env.drain()
    v = audio_data(env, ready)["versions"][0]
    assert v["duration_valid"] is valid and v["asset_id"]
    assert confirm(env, ready, result["audio_id"]).status_code == (200 if valid else 409)
    assert audio_data(env, ready)["materials"]["ready"] is valid
    attention = env.client.get("/api/v1/jobs?bucket=attention").json()["data"]["items"]
    assert any(j["id"] == result["job_id"] for j in attention) is (not valid)


def test_preview_locked_text_duplicate_and_unknown_no_resend(env, ready, monkeypatch):
    body = inputs(env, ready, pronunciation="马克当工具", pronunciation_reviewed=True)
    r = env.post(f"/tasks/{ready[0]}/audio-preview", body)
    assert r.status_code == 200
    data = r.json()["data"]
    assert "markdown-tool" in data["original_text"] and "markdown-tool" not in data["synthesis_text"]
    assert "马克当工具" in data["synthesis_text"]
    assert data["synthesis_text"].count("\n") == 3 and "evidence" not in data["synthesis_text"]
    assert (
        env.post(f"/tasks/{ready[0]}/audio-preview", {**body, "pronunciation_reviewed": False}).status_code
        == 422
    )
    r = env.post(f"/tasks/{ready[0]}/audio-generations", body, key="audio-same-click")
    again = env.post(f"/tasks/{ready[0]}/audio-generations", body, key="audio-same-click")
    assert r.json()["data"] == again.json()["data"]
    assert env.post(f"/tasks/{ready[0]}/audio-generations", body).status_code == 409
    calls = []

    def timeout(*a, **kw):
        calls.append(a)
        raise ProviderError("SYNTHESIS_RESULT_UNKNOWN", "fixture", uncertain=True)

    monkeypatch.setattr(env.worker.providers, "synthesize", timeout)
    env.drain()
    env.drain()
    assert len(calls) == 1 and calls[0][1] == data["synthesis_text"]
    assert env.post(f"/tasks/{ready[0]}/audio-generations", body).status_code == 409
    response = env.client.get(f"/api/v1/tasks/{ready[0]}/audio")
    assert "private-fixture-voice" not in response.text


def test_unconfirmed_voice_scope_and_late_results(env, ready):
    task, voice = ready
    body = inputs(env, ready)
    with env.sf() as db:
        db.get(m.ProbeVoice, voice).provider_scope = {"model": "other"}
        db.commit()
    assert env.post(f"/tasks/{task}/audio-generations", body).status_code == 409
    with env.sf() as db:
        v = db.get(m.ProbeVoice, voice)
        v.provider_scope = env.worker.providers.scope()
        db.commit()
    receipt = submit(env, ready)
    id = env.worker.claim()
    job, prepared = env.worker.prepare(id)
    result = env.worker.perform(job, prepared)
    env.worker.checkpoint(job, result)
    env.post(f"/probe-voices/{voice}/disable", {"expected_revision": 1})
    env.worker.finish(id, result)
    assert audio_data(env, ready)["versions"][0]["job_status"] == "cancelled"
    assert audio_data(env, ready)["versions"][0]["asset_id"] is None
    assert confirm(env, ready, receipt["audio_id"]).status_code == 404
    assert env.post(f"/tasks/{task}/audio-generations", body).status_code == 409
    edit(env, task, "已修改，先别合成？")
    assert (
        env.post(f"/tasks/{task}/audio-generations", inputs(env, ready, voice_revision=2)).status_code == 409
    )


def test_script_changes_stale_audio_copy_change_keeps_audio(env, ready):
    task, _ = ready
    receipt = submit(env, ready)
    env.drain()
    assert confirm(env, ready, receipt["audio_id"]).status_code == 200
    before = texts(env, task)["task"]
    copy = before["current_copy"]
    r = env.post(
        f"/tasks/{task}/texts",
        {
            "kind": "copy",
            "expected_revision": before["revision"],
            "content": {**copy["content"], "title": "新的文案标题"},
            "evidence_map": copy["evidence_map"],
            "parent_id": copy["id"],
            "script_id": before["current_script"]["id"],
        },
    )
    assert r.status_code == 201
    assert not r.json()["data"]["task"]["audio_stale"]
    edit(env, task, "改完稿件，想再核对内容？")
    state = audio_data(env, ready)
    assert state["materials"]["audio"]["stale"] and not state["materials"]["ready"]
    assert confirm(env, ready, receipt["audio_id"]).status_code == 409
    protected = preview(env, task)["protected"]
    assert any("正式音频关联" in p["reasons"] for p in protected)
    asset = state["versions"][0]["asset_id"]
    assert env.post(f"/assets/{asset}", {"confirm": True}, method="DELETE").status_code == 409
    assert env.client.get(f"/api/v1/assets/{asset}/content").status_code == 200


def test_checkpoint_recovery_has_no_second_synthesis(env, ready, monkeypatch):
    receipt = submit(env, ready)
    id = env.worker.claim()
    job, prepared = env.worker.prepare(id)
    result = env.worker.perform(job, prepared)
    env.worker.checkpoint(job, result)
    with env.sf() as db:
        db.get(m.Job, id).lease_expires_at = 0
        db.commit()
    monkeypatch.setattr(
        env.worker.providers, "synthesize", lambda *a, **k: pytest.fail("duplicate provider call")
    )
    env.worker.recover()
    env.worker.recover()
    assert audio_data(env, ready)["versions"][0]["asset_id"]
    assert env.client.get("/api/v1/jobs/" + receipt["job_id"]).json()["data"]["status"] == "succeeded"


def test_exports_frozen_versions_rebuild_and_private_files(env, ready, monkeypatch):
    task, _ = ready
    receipt = submit(env, ready)
    env.drain()
    confirm(env, ready, receipt["audio_id"])
    before = texts(env, task)["task"]
    manifest = export(env, ready)
    edit(env, task, "这份新稿不应混入旧素材包？")
    monkeypatch.setattr(
        env.worker.providers, "synthesize", lambda *a, **k: pytest.fail("export called model")
    )
    env.drain()
    response = env.client.get(f"/api/v1/exports/{manifest['export_id']}/content")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 4
        payload = json.loads(archive.read("版本说明.json"))
        assert payload["script"]["id"] == before["current_script"]["id"]
        assert payload["script"]["id"] == payload["copy"]["script_id"] == payload["audio"]["script_id"]
        assert "这份新稿" not in str(payload)
        assert "private-fixture-voice" not in str(payload) and "source_url" not in str(payload)
    with env.sf() as db:
        e = db.get(m.ExportManifest, manifest["export_id"])
        asset_path(env.settings, db.get(m.Asset, e.asset_id).storage_key).unlink()
    assert env.client.get(f"/api/v1/exports/{manifest['export_id']}/content").status_code == 404
    assert env.post(f"/exports/{manifest['export_id']}/rebuild", {"confirm": True}).status_code == 202
    env.drain()
    rebuilt = env.client.get(f"/api/v1/exports/{manifest['export_id']}/content")
    with (
        zipfile.ZipFile(io.BytesIO(response.content)) as old,
        zipfile.ZipFile(io.BytesIO(rebuilt.content)) as new,
    ):
        assert {name: old.read(name) for name in old.namelist()} == {
            name: new.read(name) for name in new.namelist()
        }
    current = texts(env, task)["task"]
    assert (
        env.post(
            f"/tasks/{task}/exports", {"expected_revision": current["revision"], "complete": True}
        ).status_code
        == 409
    )
    partial = export(env, ready, False)
    env.drain()
    assert env.client.get(f"/api/v1/exports/{partial['export_id']}/content").status_code == 200
    assert (
        env.client.get(
            f"/api/v1/tasks/{task}/texts/{current['current_script']['id']}/download?format=md"
        ).status_code
        == 200
    )
    env.client.cookies.clear()
    for path in [
        f"/tasks/{task}/audio",
        f"/exports/{manifest['export_id']}/content",
        f"/tasks/{task}/texts/{current['current_script']['id']}/download",
    ]:
        assert env.client.get("/api/v1" + path).status_code == 401


def test_cancellation_and_cross_task_audio_confirmation(env, ready, task_data):
    r = submit(env, ready)
    env.post(f"/jobs/{r['job_id']}/cancel")
    env.drain()
    assert not audio_data(env, ready)["versions"][0]["asset_id"]
    second = env.post("/tasks", {"review_id": task_data["review"], "audience": "测试观众"}).json()["data"]
    response = env.post(
        f"/tasks/{second['id']}/audio/{r['audio_id']}/confirm", {"expected_revision": 1, "accepted": True}
    )
    assert response.status_code == 422
    with env.sf() as db:
        stranger = m.Creator(login_name="stranger-audio", password_hash="unused")
        db.add(stranger)
        db.flush()
        db.get(m.Task, ready[0]).owner_id = stranger.id
        db.commit()
    assert env.client.get(f"/api/v1/tasks/{ready[0]}/audio").status_code == 404


def test_formal_reuse_two_tasks_without_sample_or_enrollment(env, ready, task_data, monkeypatch):
    task, voice = ready
    second = env.post("/tasks", {"review_id": task_data["review"], "audience": "测试观众"}).json()["data"][
        "id"
    ]
    generate(env, second)
    script = next(v for v in texts(env, second)["versions"] if v["kind"] == "script")
    move(env, second, script["id"], "adopt")
    move(env, second, script["id"], "confirm")
    with env.sf() as db:
        sample = db.get(m.Asset, db.get(m.ProbeVoice, voice).sample_asset_id)
        sample.state = "deleted"
        asset_path(env.settings, sample.storage_key).unlink()
        db.commit()
    monkeypatch.setattr(env.worker.providers, "enroll", lambda *a: pytest.fail("must reuse existing voice"))
    first = submit(env, ready)
    other = submit(env, (second, voice))
    env.drain()
    assert audio_data(env, ready)["versions"][0]["available"]
    assert audio_data(env, (second, voice))["versions"][0]["available"]
    assert first["audio_id"] != other["audio_id"]


def test_formal_live_cost_permission_binds_pronunciation_without_network(env, ready):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from backend.app.main import create_app
    from backend.app.services.providers import Providers

    body = inputs(env, ready)
    live = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "provider_contract_version": "S1-2026-09-08",
            "voice_api_key": SecretStr("fixture-not-a-real-key"),
        }
    )
    with env.sf() as db:
        for cls in [m.Task, m.TextVersion, m.Tool, m.Snapshot, m.Analysis, m.Review, m.ProbeVoice, m.Asset]:
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
        db.get(m.ProbeVoice, ready[1]).provider_scope = Providers(live).scope()
        db.commit()
    with TestClient(create_app(live, env.sf)) as client:
        client.cookies.update(env.client.cookies)

        def post(path, data):
            return client.post(
                "/api/v1" + path,
                json=data,
                headers={
                    "Origin": live.web_origin,
                    "X-CSRF-Token": env.csrf,
                    "Idempotency-Key": str(time.time_ns()),
                },
            )

        path = f"/tasks/{ready[0]}/audio-generations"
        assert post(path, body).json()["error"]["code"] == "COST_AUTHORIZATION_REQUIRED"
        p = post("/cost-authorizations/preview", {"action": "audio_generation", "input": body}).json()["data"]
        permission = post(
            "/cost-authorizations",
            {
                "preview_id": p["preview_id"],
                "action": "audio_generation",
                "input_hash": p["input_hash"],
                "max_calls": 1,
                "accept_unknown_cost": False,
                "max_cost": float(p["maximum_cost"]),
            },
        ).json()["data"]["id"]
        assert (
            post(
                path,
                {
                    **body,
                    "pronunciation": "马克当",
                    "pronunciation_reviewed": True,
                    "cost_authorization_id": permission,
                },
            ).status_code
            == 409
        )
        assert post(path, {**body, "cost_authorization_id": permission}).status_code == 202
        assert post(path, {**body, "cost_authorization_id": permission}).status_code == 409
        assert "fixture-not-a-real-key" not in client.get(f"/api/v1/tasks/{ready[0]}/audio").text
        from backend.app.core.db import transaction
        from backend.app.services import costs
        from backend.app.worker import Worker

        worker = Worker(live, env.sf)
        job_id = worker.claim()
        job, actual = worker.prepare(job_id)
        with transaction(env.sf) as db:
            ledger = costs.ledger_for(db, job)
            assert ledger.reserved_micros == len(actual["text"].encode("utf-8")) * 80
            costs.settle(
                db, job, {"model": live.voice_target_model, "usage": {"characters": len(actual["text"])}}
            )
            assert ledger.status == "priced"


def test_missing_audio_blocks_materials_and_package_recovery(env, ready):
    r = submit(env, ready)
    env.drain()
    confirm(env, ready, r["audio_id"])
    package = export(env, ready)
    with env.sf() as db:
        v = db.get(m.AudioVersion, r["audio_id"])
        asset_path(env.settings, db.get(m.Asset, v.asset_id).storage_key).unlink()
    env.drain()
    assert not audio_data(env, ready)["materials"]["ready"]
    assert not texts(env, ready[0])["task"]["materials_ready"]
    j = env.client.get("/api/v1/jobs/" + package["job_id"]).json()["data"]
    assert j["status"] == "failed"


def test_s2_to_s3_migration_preserves_populated_text_and_voice_tables(env, ready, pre_ranking_schema):
    import os
    import subprocess
    import sys

    from sqlalchemy import inspect, text

    from backend.app.core.config import ROOT

    engine = env.sf.kw["bind"]
    # Build a genuine populated S2 fixture by removing only the empty S3 tables.
    for table in (
        m.VoiceMetadata.__table__,
        m.VoicePreference.__table__,
        m.ExportManifest.__table__,
        m.TaskAudioState.__table__,
        m.AudioVersion.__table__,
    ):
        table.drop(engine)
    names = inspect(engine).get_table_names()
    with engine.connect() as db:
        before = {
            name: sorted([tuple(row) for row in db.execute(text('SELECT * FROM "' + name + '"'))], key=repr)
            for name in names
        }
    environment = {
        **os.environ,
        "DATABASE_URL": env.settings.database_url,
        "DATA_DIR": str(env.settings.data_dir),
    }
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini"]
    for arguments in [["stamp", "0002"], ["upgrade", "head"], ["upgrade", "head"]]:
        result = subprocess.run([*command, *arguments], cwd=ROOT, env=environment, capture_output=True)
        assert result.returncode == 0
    with engine.connect() as db:
        after = {
            name: sorted([tuple(row) for row in db.execute(text('SELECT * FROM "' + name + '"'))], key=repr)
            for name in names
        }
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    assert before == after
    assert {"audio_versions", "task_audio_states", "export_manifests"} <= set(
        inspect(engine).get_table_names()
    )


def test_explicit_rebuild_of_available_package_is_local_and_idempotent(env, ready, monkeypatch):
    from sqlalchemy import func

    r = submit(env, ready)
    env.drain()
    confirm(env, ready, r["audio_id"])
    package = export(env, ready)
    env.drain()
    export_id = package["export_id"]
    before = env.client.get(f"/api/v1/exports/{export_id}/content").content
    with env.sf() as db:
        old_asset_id = db.get(m.ExportManifest, export_id).asset_id
        paid_before = db.scalar(
            select(func.count()).select_from(m.Attempt).join(m.Job).where(m.Job.kind != "export")
        )
    monkeypatch.setattr(
        env.worker.providers, "synthesize", lambda *a, **k: pytest.fail("rebuild called model")
    )
    monkeypatch.setattr(env.worker.providers, "text", lambda *a, **k: pytest.fail("rebuild called model"))
    first = env.post(f"/exports/{export_id}/rebuild", {"confirm": True}, key="explicit-rebuild-available")
    assert first.status_code == 202
    duplicate = env.post(f"/exports/{export_id}/rebuild", {"confirm": True}, key="explicit-rebuild-available")
    concurrent = env.post(f"/exports/{export_id}/rebuild", {"confirm": True})
    assert (
        first.json()["data"]["job_id"]
        == duplicate.json()["data"]["job_id"]
        == concurrent.json()["data"]["job_id"]
    )
    env.drain()
    after = env.client.get(f"/api/v1/exports/{export_id}/content").content
    with zipfile.ZipFile(io.BytesIO(before)) as old, zipfile.ZipFile(io.BytesIO(after)) as new:
        assert {n: old.read(n) for n in old.namelist()} == {n: new.read(n) for n in new.namelist()}
    with env.sf() as db:
        assert db.get(m.ExportManifest, export_id).asset_id != old_asset_id
        assert (
            db.scalar(select(func.count()).select_from(m.Attempt).join(m.Job).where(m.Job.kind != "export"))
            == paid_before
        )
        assert asset_path(env.settings, db.get(m.Asset, old_asset_id).storage_key).is_file()


def test_voice_deletion_keeps_history_and_reports_distinct_task_references(env, ready):
    from backend.tests.test_voice_library import listing
    from backend.tests.test_voice_library import voice as make_voice

    task, voice_id = ready
    result = submit(env, ready)
    env.drain()
    before = audio_data(env, ready)["versions"][0]
    asset_id = before["asset_id"]
    audio_bytes = env.client.get(f"/api/v1/assets/{asset_id}/content").content
    script_before = texts(env, task)
    extra = make_voice(env, status="failed")
    env.post("/voice-preferences", {"default_voice_id": voice_id, "expected_revision": 0})
    entries = {v["id"]: v for v in listing(env)["items"]}
    assert entries[voice_id]["referenced_task_count"] == 1
    assert entries[extra]["referenced_task_count"] == 0
    for id in (voice_id, extra):
        body = {"expected_revision": 1, "confirm": True}
        response = env.post(f"/probe-voices/{id}", body, method="DELETE", key="delete-" + id)
        assert response.status_code == 202
        assert (
            env.post(f"/probe-voices/{id}", body, method="DELETE", key="delete-" + id).json()["data"]
            == response.json()["data"]
        )
    assert listing(env)["default_voice_id"] is None
    env.drain()
    entries = {v["id"]: v for v in listing(env)["items"]}
    assert all(entries[id]["status"] == "deleted" for id in (voice_id, extra))
    assert entries[voice_id]["referenced_task_count"] == 1
    assert texts(env, task) == script_before
    assert env.client.get(f"/api/v1/assets/{asset_id}/content").content == audio_bytes
    assert audio_data(env, ready)["versions"][0]["id"] == result["audio_id"]


def test_remove_voice_card_keeps_generated_audio_task_and_voice_picker(env, ready):
    task, voice_id = ready
    submit(env, ready)
    env.drain()
    before = audio_data(env, ready)
    texts_before = texts(env, task)
    asset_id = before["versions"][0]["asset_id"]
    content = env.client.get(f"/api/v1/assets/{asset_id}/content").content
    response = env.post("/voices/list-removals", {"voice_ids": [voice_id], "confirm": True})
    assert response.status_code == 200
    assert env.client.get("/api/v1/voices").json()["data"]["items"] == []
    assert audio_data(env, ready) == before
    assert texts(env, task) == texts_before
    assert env.client.get(f"/api/v1/assets/{asset_id}/content").content == content
