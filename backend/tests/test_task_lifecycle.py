import json
import time

from sqlalchemy import select

from backend.app import models as m
from backend.app.services import task_lifecycle as life
from backend.app.services.assets import asset_path
from backend.tests.test_audio_exports import audio_data, confirm, export, submit
from backend.tests.test_audio_exports import ready as ready
from backend.tests.test_creation import generate, texts
from backend.tests.test_ranking import catalog as catalog
from backend.tests.test_ranking import create, public, publish


def status(env, task):
    r = env.client.get(f"/api/v1/tasks/{task}/lifecycle")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def change(env, task, action, key=None):
    body = {"expected_revision": status(env, task)["revision"]}
    if action == "trash":
        p = env.client.get(f"/api/v1/tasks/{task}/delete-preview")
        assert p.status_code == 200, p.text
        body.update(confirm=True, preview_token=p.json()["data"]["preview_token"])
    return env.post(f"/tasks/{task}/{action}", body, key=key)


def expire(env, task):
    with env.sf() as db:
        row = db.get(m.TaskLifecycle, task)
        row.purge_after = time.time() - 1
        db.commit()


def test_archive_restores_confirmed_versions_and_blocks_new_writes(env, ready):
    task, _ = ready
    before = texts(env, task)
    assert change(env, task, "archive").status_code == 200
    assert texts(env, task)["versions"] == before["versions"]
    assert not env.client.get("/api/v1/tasks").json()["data"]["items"]
    assert env.client.get("/api/v1/tasks?lifecycle=archived").json()["data"]["items"][0]["id"] == task
    r = env.post(
        f"/tasks/{task}/generations",
        {"task_id": task, "mode": "joint", "expected_revision": status(env, task)["revision"]},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TASK_ARCHIVED"
    assert change(env, task, "restore").status_code == 200
    assert texts(env, task)["versions"] == before["versions"]
    assert status(env, task)["revision"] == before["task"]["revision"] + 2


def test_trash_preview_idempotency_and_changed_scope(env, ready):
    task, _ = ready
    preview = env.client.get(f"/api/v1/tasks/{task}/delete-preview").json()["data"]
    generate(env, task)
    r = env.post(
        f"/tasks/{task}/trash",
        {
            "expected_revision": preview["expected_revision"],
            "confirm": True,
            "preview_token": preview["preview_token"],
        },
    )
    assert r.status_code == 409
    assert status(env, task)["state"] == "active"
    fresh = env.client.get(f"/api/v1/tasks/{task}/delete-preview").json()["data"]
    body = {
        "expected_revision": fresh["expected_revision"],
        "confirm": True,
        "preview_token": fresh["preview_token"],
    }
    first = env.post(f"/tasks/{task}/trash", body, key="repeat-trash-operation")
    second = env.post(f"/tasks/{task}/trash", body, key="repeat-trash-operation")
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert env.client.get(f"/api/v1/tasks/{task}/texts").status_code == 404
    assert env.client.get(f"/api/v1/tasks/{task}").status_code == 404
    trash = env.client.get("/api/v1/tasks?lifecycle=trashed").json()["data"]["items"][0]
    assert trash["current_script"] is None and trash["audience"] == ""
    assert change(env, task, "restore").status_code == 200
    assert len(texts(env, task)["versions"]) == fresh["text_versions"]


def test_queued_and_late_results_do_not_revive_after_restore(env, task_data):
    task = task_data["task"]
    queued = env.post(
        f"/tasks/{task}/generations", {"task_id": task, "mode": "joint", "expected_revision": 1}
    ).json()["data"]["job_id"]
    assert change(env, task, "trash").status_code == 200
    assert change(env, task, "restore").status_code == 200
    env.drain()
    assert env.client.get(f"/api/v1/jobs/{queued}").json()["data"]["status"] == "cancelled"
    assert texts(env, task)["versions"] == []
    r = env.post(
        f"/tasks/{task}/generations",
        {"task_id": task, "mode": "joint", "expected_revision": status(env, task)["revision"]},
    )
    job_id = r.json()["data"]["job_id"]
    assert env.worker.claim() == job_id
    job, inputs = env.worker.prepare(job_id)
    result = env.worker.perform(job, inputs)
    assert change(env, task, "trash").status_code == 200
    assert change(env, task, "restore").status_code == 200
    env.worker.checkpoint(job, result)
    env.worker.recover()
    assert texts(env, task)["versions"] == []
    assert not env.worker.checkpoint_path(job_id).exists()
    with env.sf() as db:
        assert db.get(m.Job, job_id).status == "cancelled"
        assert list(db.scalars(select(m.Attempt).where(m.Attempt.job_id == queued))) == []
        assert db.scalar(select(m.Attempt).where(m.Attempt.job_id == job_id)).dispatch_state == "finished"


def test_all_packages_private_until_restored_and_purge_content(env, ready):
    task, voice = ready
    receipt = submit(env, ready)
    env.drain()
    assert confirm(env, ready, receipt["audio_id"]).status_code == 200
    exp = export(env, ready)
    env.drain()
    old_package = audio_data(env, ready)["exports"][0]["id"]
    with env.sf() as db:
        first_asset = db.get(m.ExportManifest, old_package).asset_id
    env.post(f"/exports/{old_package}/rebuild", {"confirm": True})
    env.drain()
    data = audio_data(env, ready)
    audio_id = data["versions"][0]["asset_id"]
    current_package = data["exports"][0]["download_path"]
    # Simulate an existing pre-0007 installation: old ZIP ownership must be recovered safely.
    with env.sf() as db:
        for link in db.scalars(select(m.TaskAssetLink)):
            db.delete(link)
        files = [
            (a.id, asset_path(env.settings, a.storage_key))
            for a in db.scalars(select(m.Asset).where(m.Asset.kind.in_(["formal_audio", "export_package"])))
        ]
        db.commit()
    preview = env.client.get(f"/api/v1/tasks/{task}/delete-preview").json()["data"]
    assert preview["files"] == 3
    assert change(env, task, "trash").status_code == 200
    for id, _ in files:
        assert env.client.get(f"/api/v1/assets/{id}/content").status_code == 404
    assert env.client.get(current_package).status_code == 404
    detail = env.client.get("/api/v1/jobs/" + exp["job_id"]).json()["data"]
    assert detail["actions"] == [] and "result" not in detail
    assert change(env, task, "restore").status_code == 200
    for id in [audio_id, first_asset]:
        assert env.client.get(f"/api/v1/assets/{id}/content").status_code == 200
    assert change(env, task, "trash").status_code == 200
    expire(env, task)
    assert change(env, task, "restore").status_code == 409
    env.worker.cleanup()
    assert status(env, task)["state"] == "purged"
    for _, path in files:
        assert not path.exists()
    with env.sf() as db:
        assert db.get(m.ProbeVoice, voice).status == "ready"
        assert all(
            v.payload == {"purged": True}
            for v in db.scalars(select(m.TextVersion).where(m.TextVersion.task_id == task))
        )
        assert all(
            g.output is None for g in db.scalars(select(m.Generation).where(m.Generation.task_id == task))
        )
        assert db.get(m.TaskTextState, task) is None
        assert all(
            j.input_json == {"task_id": task, "purged": True}
            for j in life.task_jobs(db, db.get(m.Task, task))
        )
        assert not any(
            "每天分享" in json.dumps(o.response, ensure_ascii=False) for o in db.scalars(select(m.Operation))
        )
    env.worker.cleanup()
    assert status(env, task)["state"] == "purged"


def test_purge_waits_for_unknown_result_then_cleans_and_file_failure_retries(env, task_data, monkeypatch):
    task = task_data["task"]
    r = env.post(f"/tasks/{task}/generations", {"task_id": task, "mode": "joint", "expected_revision": 1})
    job_id = r.json()["data"]["job_id"]
    env.worker.claim()
    job, inputs = env.worker.prepare(job_id)
    result = env.worker.perform(job, inputs)
    assert change(env, task, "trash").status_code == 200
    expire(env, task)
    env.worker.cleanup()
    assert status(env, task)["cleanup_status"] == "waiting_for_result_reconciliation"
    env.worker.checkpoint(job, result)
    env.worker.recover()
    env.worker.cleanup()
    assert status(env, task)["state"] == "purged"


def test_permissions_and_publish_independence(catalog):
    env, _ = catalog
    entry, _ = publish(env, create(catalog))
    with env.sf() as db:
        snap = db.get(m.Snapshot, entry["content"]["source_snapshot_id"])
        original = db.scalar(select(m.Task))
        # Task created in the same isolated live scope; shared sources are unchanged.
        original.data_mode = "live"
        task = original.id
        db.commit()
    before = public(env)
    assert change(env, task, "trash").status_code == 200
    expire(env, task)
    env.worker.cleanup()
    assert public(env) == before
    with env.sf() as db:
        assert db.get(m.Snapshot, snap.id) is not None
        assert db.get(m.PublicEntry, entry["id"]).state == "published"
    env.client.cookies.clear()
    assert env.client.get(f"/api/v1/tasks/{task}/lifecycle").status_code == 401
    assert env.post(f"/tasks/{task}/restore", {"expected_revision": 3}).status_code in (401, 403)


def test_file_failure_and_shared_asset_keep_retryable_state(env, ready, monkeypatch):
    task, _ = ready
    submit(env, ready)
    env.drain()
    data = audio_data(env, ready)
    asset_id = data["versions"][0]["asset_id"]
    with env.sf() as db:
        original = db.get(m.Task, task)
        other = m.Task(
            owner_id=original.owner_id,
            data_mode=original.data_mode,
            tool_id=original.tool_id,
            snapshot_id=original.snapshot_id,
            review_id=original.review_id,
            audience=original.audience,
        )
        db.add(other)
        db.flush()
        other_id = other.id
        life.link_asset(db, other_id, asset_id)
        path = asset_path(env.settings, db.get(m.Asset, asset_id).storage_key)
        db.commit()
    assert change(env, task, "trash").status_code == 200
    expire(env, task)
    env.worker.cleanup()
    assert status(env, task)["cleanup_status"] == "shared_asset_reference"
    assert path.exists() and status(env, other_id)["state"] == "active"
    with env.sf() as db:
        db.delete(db.get(m.TaskAssetLink, (other_id, asset_id)))
        db.commit()
    with monkeypatch.context() as patch:

        def deny(*args):
            raise PermissionError("test file locked")

        patch.setattr(life, "delete_asset_file", deny)
        env.worker.cleanup()
    assert status(env, task)["cleanup_status"] == "file_cleanup_failed"
    assert path.exists()
    env.worker.cleanup()
    assert status(env, task)["state"] == "purged" and not path.exists()


def test_cached_writes_denied_after_trash_and_cross_scope(env, task_data):
    task = task_data["task"]
    body = {"task_id": task, "mode": "joint", "expected_revision": 1}
    r = env.post(f"/tasks/{task}/generations", body, key="cached-task-write")
    assert r.status_code == 202
    env.drain()
    assert change(env, task, "trash").status_code == 200
    assert env.post(f"/tasks/{task}/generations", body, key="cached-task-write").status_code == 404
    with env.sf() as db:
        original = db.get(m.Task, task)
        foreign = m.Creator(login_name="unrelated", password_hash="not-a-login")
        db.add(foreign)
        db.flush()
        other = m.Task(
            owner_id=foreign.id,
            data_mode="mock",
            tool_id=original.tool_id,
            snapshot_id=original.snapshot_id,
            review_id=original.review_id,
            audience="not-visible",
        )
        hidden = m.Task(
            owner_id=env.owner_id,
            data_mode="live",
            tool_id=original.tool_id,
            snapshot_id=original.snapshot_id,
            review_id=original.review_id,
            audience="not-visible",
        )
        db.add_all([other, hidden])
        db.flush()
        ids = [other.id, hidden.id]
        db.commit()
    for id in ids:
        for endpoint in ["lifecycle", "delete-preview"]:
            assert env.client.get(f"/api/v1/tasks/{id}/{endpoint}").status_code == 404
        assert env.post(f"/tasks/{id}/archive", {"expected_revision": 1}).status_code == 404
    response = env.client.post(
        f"/api/v1/tasks/{task}/restore",
        json={"expected_revision": 2},
        headers={"Origin": env.settings.web_origin, "Idempotency-Key": "forged-lifecycle"},
    )
    assert response.status_code == 403


def test_pagination_archive_recovery_and_exact_deadline(env, task_data, monkeypatch):
    task = task_data["task"]
    with env.sf() as db:
        base = db.get(m.Task, task)
        for _ in range(12):
            db.add(
                m.Task(
                    owner_id=base.owner_id,
                    data_mode=base.data_mode,
                    tool_id=base.tool_id,
                    snapshot_id=base.snapshot_id,
                    review_id=base.review_id,
                    audience=base.audience,
                )
            )
        db.commit()
    ids = []
    cursor = None
    for _ in range(3):
        data = env.client.get(
            "/api/v1/tasks", params={"limit": 5, **({"cursor": cursor} if cursor else {})}
        ).json()["data"]
        ids.extend(t["id"] for t in data["items"])
        cursor = data["next_cursor"]
    assert len(set(ids)) == 13 and cursor is None
    assert change(env, task, "archive").status_code == 200
    assert change(env, task, "trash").status_code == 200
    assert change(env, task, "restore").json()["data"]["state"] == "archived"
    assert change(env, task, "trash").status_code == 200
    with env.sf() as db:
        deadline = time.time()
        db.get(m.TaskLifecycle, task).purge_after = deadline
        db.commit()
    monkeypatch.setattr(life.time, "time", lambda: deadline)
    assert change(env, task, "restore").status_code == 409
    env.worker.cleanup()
    assert status(env, task)["state"] == "purged"


def test_0006_upgrade_retains_all_old_rows(env, task_data):
    import os
    import subprocess
    import sys

    from sqlalchemy import inspect, text

    from backend.app.core.config import ROOT

    engine = env.sf.kw["bind"]
    for cls in (m.CostReconciliation, m.TaskContextVersion, m.TaskAssetLink, m.TaskLifecycle):
        cls.__table__.drop(engine)
    tables = inspect(engine).get_table_names()

    def snapshot():
        with engine.connect() as conn:
            return {
                t: sorted([tuple(r) for r in conn.execute(text('SELECT * FROM "' + t + '"'))], key=repr)
                for t in tables
            }

    before = snapshot()
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini"]
    environment = {
        **os.environ,
        "DATABASE_URL": env.settings.database_url,
        "DATA_DIR": str(env.settings.data_dir),
    }
    for args in [["stamp", "0006"], ["upgrade", "head"], ["upgrade", "head"]]:
        result = subprocess.run([*command, *args], cwd=ROOT, env=environment, capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
    assert snapshot() == before
    assert status(env, task_data["task"])["state"] == "active"


def test_cancelled_task_budget_releases_unsent_and_holds_sent(env, task_data):
    from backend.app.core.db import transaction
    from backend.app.schemas import TaskTrashIn
    from backend.tests.test_cost_budget import authorize
    from backend.tests.test_cost_budget import submit as cost_submit

    with transaction(env.sf) as db:
        for cls in (m.Tool, m.Snapshot, m.Analysis, m.Review, m.Task):
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
    settings = env.settings.model_copy(update={"provider_mode": "live"})
    inputs = {"kind": "writing", "contract_version": "S1-2026-09-08", "task_id": task_data["task"]}
    budget = (settings, inputs)
    first, amount = authorize(env, budget)
    second, _ = authorize(env, budget)
    unsent = cost_submit(env, budget, first)
    sent = cost_submit(env, budget, second)
    with transaction(env.sf) as db:
        db.add(m.Attempt(job_id=sent, attempt_no=1, dispatch_state="dispatching"))
        db.flush()
        task = db.get(m.Task, task_data["task"])
        p = life.delete_preview(db, settings, task)
        life.change(
            db,
            settings,
            task,
            "trash",
            TaskTrashIn(expected_revision=task.revision, confirm=True, preview_token=p["preview_token"]),
        )
    with env.sf() as db:
        rows = {r.job_id: r for r in db.scalars(select(m.CostLedger))}
        assert rows[unsent].status == "released"
        assert rows[sent].status == "unknown" and rows[sent].reserved_micros == amount
        assert db.get(m.Job, sent).status == db.get(m.Job, unsent).status == "cancelled"
