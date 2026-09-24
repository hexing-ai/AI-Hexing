import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.services import task_context as context
from backend.app.services.creation import context_for
from backend.app.services.jobs import text_inputs
from backend.app.services.providers import wav_tone
from backend.app.services.quality import inspect_writing
from backend.tests.test_audio_exports import audio_data, confirm, export, submit
from backend.tests.test_audio_exports import ready as ready
from backend.tests.test_creation import generate, move, texts
from backend.tests.test_task_lifecycle import change as lifecycle_change
from backend.tests.test_task_lifecycle import expire


def current(env, task):
    r = env.client.get(f"/api/v1/tasks/{task}/context")
    assert r.status_code == 200, r.text
    return r.json()["data"]["current"]


def body_for(env, task, **changes):
    c = current(env, task)
    notes = [{k: v for k, v in n.items() if k not in ("id", "source_type")} for n in c["creator_tests"]]
    return {
        "expected_revision": c["revision"],
        "analysis_id": c["analysis_id"],
        "primary_highlight_id": c["primary_highlight_id"],
        "secondary_highlight_id": c["secondary_highlight_id"],
        "audience": c["audience"],
        "creator_tests": notes,
        "facts_reviewed": True,
        **changes,
    }


def save(env, task, **changes):
    r = env.post(f"/tasks/{task}/context", body_for(env, task, **changes), method="PATCH")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def note(snapshot):
    return {
        "snapshot_id": snapshot,
        "tested_on": datetime.now(timezone.utc).date().isoformat(),
        "environment": "隔离测试 / 工具 v1",
        "procedure": "打开示例并运行导出",
        "observation": "成功获得文件",
        "limitations": "仅一次桌面测试，手机未验证",
    }


def test_change_history_noop_and_reconfirmation_reuses_same_audio(env, ready, monkeypatch):
    task, _ = ready
    monkeypatch.setattr(
        env.worker.providers, "synthesize", lambda *a, **k: (wav_tone(31), {"model": "mock-tone"})
    )
    audio = submit(env, ready)
    env.drain()
    assert confirm(env, ready, audio["audio_id"]).status_code == 200
    old = texts(env, task)["task"]
    package = export(env, ready)
    env.drain()
    with env.sf() as db:
        frozen = copy.deepcopy(db.get(m.ExportManifest, package["export_id"]).payload)
        jobs = db.scalar(select(func.count()).select_from(m.Job))
    assert not save(env, task)["changed"]
    assert current(env, task)["id"] is None
    updated = save(env, task, audience="希望了解桌面导出的创作者")
    assert updated["changed"] and not updated["task"]["materials_ready"]
    for kind in ("script", "copy"):
        v = updated["task"]["current_" + kind]
        assert v["content"] == old["current_" + kind]["content"]
        assert not v["confirmed_at"] and v["context_stale"]
        assert move(env, task, v["id"], "adopt").status_code == 200
        assert not texts(env, task)["task"]["current_" + kind]["confirmed_at"]
    assert confirm(env, ready, audio["audio_id"]).status_code == 409
    for kind in ("script", "copy"):
        assert move(env, task, old["current_" + kind]["id"], "confirm").status_code == 200
    view = audio_data(env, ready)["versions"][0]
    assert view["can_confirm"] and view["context_stale"]
    assert confirm(env, ready, audio["audio_id"]).status_code == 200
    assert audio_data(env, ready)["materials"]["ready"]
    assert not save(env, task)["changed"]
    assert audio_data(env, ready)["materials"]["ready"]
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Job)) == jobs
        assert db.get(m.ExportManifest, package["export_id"]).payload == frozen
    assert env.post(f"/exports/{package['export_id']}/rebuild", {"confirm": True}).status_code == 202
    env.drain()
    with env.sf() as db:
        assert db.get(m.ExportManifest, package["export_id"]).payload == frozen
    history = env.client.get(f"/api/v1/tasks/{task}/context/history?limit=1").json()["data"]
    assert len(history["items"]) == 1 and history["next_before"] is not None
    earlier = env.client.get(f"/api/v1/tasks/{task}/context/history?before={history['next_before']}").json()[
        "data"
    ]
    assert earlier["items"][0]["revision"] == 0
    assert earlier["items"][0]["audience"] == old["audience"]


def test_notes_context_and_section_specific_claims(env, task_data):
    task = task_data["task"]
    c = save(env, task, creator_tests=[note(task_data["snapshot"])])["current"]
    n = c["creator_tests"][0]
    assert n["source_type"] == "creator_test" and n["id"].startswith("test-")
    assert not save(env, task)["changed"]
    with env.sf() as db:
        t = db.get(m.Task, task)
        ctx = context_for(db, t)
        for kind in ("writing", "generation"):
            inputs = text_inputs(db, env.settings, env.owner_id, kind, {"task_id": task})
            assert inputs["creator_tests"] == [n] and inputs["context_id"] == c["id"]
    raw, _ = env.worker.providers.text("writing", ctx)
    raw["script"]["feature"] = "我测试了示例，成功导出一个文件，仅在桌面验证。"
    assert any(
        i["code"] == "UNVERIFIED_TEST"
        for i in inspect_writing(raw, ctx["tool_name"], ctx["fragments"], ctx)["issues"]
    )
    raw["evidence_map"]["feature"] = [n["id"]]
    assert not any(
        i["code"] == "UNVERIFIED_TEST"
        for i in inspect_writing(raw, ctx["tool_name"], ctx["fragments"], ctx)["issues"]
    )
    raw["script"]["example"] = "我测试了手机也可用。"
    assert any(
        i["path"] == "script.example" and i["code"] == "UNVERIFIED_TEST"
        for i in inspect_writing(raw, ctx["tool_name"], ctx["fragments"], ctx)["issues"]
    )
    raw["script"]["pain"] = "亲测有效？"
    assert any(
        i["path"] == "script.pain" and i["code"] == "UNVERIFIED_TEST"
        for i in inspect_writing(raw, ctx["tool_name"], ctx["fragments"], ctx)["issues"]
    )
    assert not inspect_writing(raw, ctx["tool_name"], ctx["fragments"], {**ctx, "creator_tests": []})[
        "checks"
    ]["evidence"]


@pytest.mark.parametrize("invalid", ["blank", "future", "snapshot", "duplicate", "too_many"])
def test_invalid_notes_rejected_without_mutation(env, task_data, invalid):
    task = task_data["task"]
    n = note(task_data["snapshot"])
    notes = [n]
    if invalid == "blank":
        n["observation"] = "   "
    if invalid == "future":
        n["tested_on"] = "2999-01-01"
    if invalid == "snapshot":
        n["snapshot_id"] = "wrong-snapshot"
    if invalid == "duplicate":
        notes = [n, n]
    if invalid == "too_many":
        notes = [n] * 6
    r = env.post(f"/tasks/{task}/context", body_for(env, task, creator_tests=notes), method="PATCH")
    assert r.status_code == 422
    assert current(env, task)["id"] is None


def test_idempotency_conflicts_and_access_guards(env, task_data):
    task = task_data["task"]
    body = body_for(env, task, audience="另一类受众")
    first = env.post(f"/tasks/{task}/context", body, key="repeat-context-save", method="PATCH")
    assert first.status_code == 200
    assert (
        env.post(f"/tasks/{task}/context", body, key="repeat-context-save", method="PATCH").json()["data"]
        == first.json()["data"]
    )
    assert env.post(f"/tasks/{task}/context", body, method="PATCH").status_code == 409
    assert (
        env.post(
            f"/tasks/{task}/context", body_for(env, task, secondary_highlight_id="h1"), method="PATCH"
        ).status_code
        == 422
    )
    assert env.client.patch(f"/api/v1/tasks/{task}/context", json=body).status_code == 403
    with env.sf() as db:
        a = db.get(m.Analysis, task_data["analysis"])
        a.data_mode = "live"
        db.commit()
    assert env.post(f"/tasks/{task}/context", body_for(env, task), method="PATCH").status_code == 404
    with env.sf() as db:
        db.get(m.Analysis, task_data["analysis"]).data_mode = "mock"
        owner = m.Creator(login_name="other-b02", password_hash="unused")
        db.add(owner)
        db.flush()
        db.get(m.Analysis, task_data["analysis"]).owner_id = owner.id
        db.commit()
    assert env.post(f"/tasks/{task}/context", body_for(env, task), method="PATCH").status_code == 404
    assert lifecycle_change(env, task, "archive").status_code == 200
    assert env.post(f"/tasks/{task}/context", body_for(env, task), method="PATCH").status_code == 409
    assert lifecycle_change(env, task, "trash").status_code == 200
    for suffix in ("context", "context/history", "context/sources"):
        assert env.client.get(f"/api/v1/tasks/{task}/{suffix}").status_code == 404


def test_new_snapshot_invalidates_old_refs_and_preserves_sources(env, ready):
    task, _ = ready
    old = current(env, task)
    save(env, task, creator_tests=[note(old["snapshot_id"])])
    with env.sf() as db:
        original = db.get(m.Snapshot, old["snapshot_id"])
        snap = m.Snapshot(
            owner_id=env.owner_id,
            data_mode="mock",
            tool_id=original.tool_id,
            source_sha="b" * 64,
            payload={
                **original.payload,
                "fragments": [
                    {
                        "id": "new-ref",
                        "text": "新版本资料",
                        "source_url": "https://github.com/demo/markdown-tool",
                    }
                ],
            },
        )
        db.add(snap)
        db.flush()
        original_a = db.get(m.Analysis, old["analysis_id"])
        a = m.Analysis(
            owner_id=env.owner_id,
            data_mode="mock",
            snapshot_id=snap.id,
            context_hash="new-analysis",
            prompt_version="analysis-v1",
            payload={
                **original_a.payload,
                "highlights": [{**original_a.payload["highlights"][0], "evidence_refs": ["new-ref"]}],
            },
        )
        db.add(a)
        db.commit()
        aid = a.id
    r = env.post(f"/tasks/{task}/context", body_for(env, task, analysis_id=aid), method="PATCH")
    assert r.status_code == 422 and r.json()["error"]["code"] == "TEST_SNAPSHOT_MISMATCH"
    changed = save(env, task, analysis_id=aid, creator_tests=[])
    assert not changed["task"]["current_script"]["valid"]
    assert move(env, task, changed["task"]["current_script"]["id"], "confirm").status_code == 409
    source_list = env.client.get(f"/api/v1/tasks/{task}/context/sources?limit=1").json()["data"]
    assert len(source_list["items"]) == 1 and source_list["next_cursor"]
    with env.sf() as db:
        tool = m.Tool(
            owner_id=env.owner_id,
            data_mode="mock",
            github_repo_id=999111,
            metadata_json={},
            canonical_url="https://github.com/demo/other",
            full_name="demo/other",
        )
        db.add(tool)
        db.flush()
        db.get(m.Snapshot, changed["current"]["snapshot_id"]).tool_id = tool.id
        db.commit()
    assert env.post(f"/tasks/{task}/context", body_for(env, task), method="PATCH").status_code == 422


def test_queued_cancel_and_late_checkpoint_keeps_original_context(env, task_data):
    task = task_data["task"]
    queued = generate(env, task, drain=False)
    save(env, task, audience="新的受众")
    env.drain()
    assert env.client.get(f"/api/v1/jobs/{queued}").json()["data"]["status"] == "cancelled"
    assert texts(env, task)["versions"] == []
    origin = current(env, task)["id"]
    job_id = generate(env, task, drain=False)
    assert env.worker.claim() == job_id
    job, inputs = env.worker.prepare(job_id)
    assert inputs["context_id"] == origin
    result = env.worker.perform(job, inputs)
    save(env, task, audience="再次调整受众")
    env.worker.checkpoint(job, result)
    with env.sf() as db:
        db.get(m.Job, job_id).lease_expires_at = 0
        db.commit()
    env.worker.recover()
    data = texts(env, task)
    assert len(data["versions"]) == 2 and data["task"]["current_script"] is None
    assert all(v["context_stale"] and v["context_id"] == origin for v in data["versions"])


def test_purge_clears_context_notes_and_cached_responses(env, task_data):
    task = task_data["task"]
    save(env, task, creator_tests=[note(task_data["snapshot"])])
    assert lifecycle_change(env, task, "trash").status_code == 200
    expire(env, task)
    env.worker.cleanup()
    with env.sf() as db:
        assert all(r.payload == {"purged": True} for r in db.scalars(select(m.TaskContextVersion)))
        assert all("成功获得文件" not in str(r.response) for r in db.scalars(select(m.Operation)))
        assert db.get(m.Snapshot, task_data["snapshot"])


def test_cost_previews_change_and_unknown_dispatch_stays_reserved(env, task_data):
    from backend.app.core.db import transaction
    from backend.app.schemas import TaskContextIn
    from backend.app.services.costs import quote
    from backend.tests.test_cost_budget import authorize
    from backend.tests.test_cost_budget import submit as cost_submit

    task = task_data["task"]
    body = body_for(env, task, audience="费用核对测试受众")
    with transaction(env.sf) as db:
        for cls in (m.Tool, m.Snapshot, m.Analysis, m.Review, m.Task):
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
    settings = env.settings.model_copy(update={"provider_mode": "live"})
    inputs = {"kind": "writing", "contract_version": "S1-2026-09-08", "task_id": task}
    budget = (settings, inputs)
    first, amount = authorize(env, budget)
    second, _ = authorize(env, budget)
    unsent = cost_submit(env, budget, first)
    sent = cost_submit(env, budget, second)
    with transaction(env.sf) as db:
        original_quote = quote(db, settings, env.owner_id, "writing", inputs)
        j = db.get(m.Job, sent)
        j.status = "running"
        db.add(m.Attempt(job_id=sent, attempt_no=1, dispatch_state="dispatching"))
        db.flush()
        context.change(db, db.get(m.Task, task), TaskContextIn(**body))
        assert (
            quote(db, settings, env.owner_id, "writing", inputs)["request_hash"]
            != original_quote["request_hash"]
        )
    with env.sf() as db:
        rows = {r.job_id: r for r in db.scalars(select(m.CostLedger))}
        assert rows[unsent].status == "released"
        assert rows[sent].reserved_micros == amount and rows[sent].status != "released"
        assert db.get(m.Job, sent).status == "running"
