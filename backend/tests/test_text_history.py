from sqlalchemy import func, select, text

from backend.app import models as m
from backend.tests.test_creation import generate, move, texts


def edit(env, task, pain, *, version=None, evidence=None):
    data = texts(env, task)
    version = version or data["task"]["current_script"]
    content = (
        dict(version["content"])
        if version
        else {
            "pain": "",
            "intro": "每天分享好用的工具，今天讲markdown-tool。",
            "feature": "支持 Markdown 页面预览，需要 Node.js。",
            "example": "导入 Markdown 后预览。",
        }
    )
    content["pain"] = pain
    response = env.post(
        f"/tasks/{task}/texts",
        {
            "kind": "script",
            "content": content,
            "evidence_map": evidence
            or (
                version["evidence_map"] if version else {"feature": ["f1"], "example": ["f1"], "copy": ["f1"]}
            ),
            "parent_id": version["id"] if version else None,
            "expected_revision": data["task"]["revision"],
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def preview(env, task):
    return env.client.get(f"/api/v1/tasks/{task}/texts/cleanup-preview").json()["data"]


def clean(env, task, p, key=None):
    return env.post(
        f"/tasks/{task}/texts/cleanup",
        {"expected_revision": p["expected_revision"], "preview_token": p["preview_token"], "confirm": True},
        key=key,
    )


def test_no_change_save_keeps_revision_confirmation_and_copy(env, task_data):
    task = task_data["task"]
    generate(env, task)
    versions = texts(env, task)["versions"]
    script = next(v for v in versions if v["kind"] == "script")
    copy = next(v for v in versions if v["kind"] == "copy")
    move(env, task, script["id"], "adopt")
    move(env, task, script["id"], "confirm")
    move(env, task, copy["id"], "adopt")
    move(env, task, copy["id"], "confirm")
    before = texts(env, task)
    for _ in range(2):
        result = edit(env, task, script["content"]["pain"])
        assert result["changed"] is False
    after = texts(env, task)
    assert after == before
    # Same body but different references is a meaningful change.
    result = edit(
        env, task, script["content"]["pain"], evidence={"feature": ["f1"], "example": ["f1"], "copy": []}
    )
    assert result["changed"] and result["task"]["copy_stale"]
    assert not result["version"]["confirmed_at"]


def test_prune_chain_keeps_current_linked_failed_and_audit(env, task_data):
    task = task_data["task"]
    generate(env, task)
    versions = texts(env, task)["versions"]
    script = next(v for v in versions if v["kind"] == "script")
    copy = next(v for v in versions if v["kind"] == "copy")
    move(env, task, script["id"], "adopt")
    move(env, task, copy["id"], "adopt")
    old = edit(env, task, "第一处修改？")["version"]
    latest = edit(env, task, "第二处修改？")["version"]
    # Keep a failure even though no current pointer references it.
    with env.sf() as db:
        bad = m.TextVersion(
            owner_id=env.owner_id,
            data_mode="mock",
            task_id=task,
            kind="script",
            payload={"valid": False, "content": {"pain": "原失败字段"}},
        )
        db.add(bad)
        db.commit()
        bad_id = bad.id
    p = preview(env, task)
    assert p["removable_ids"] == [old["id"]]
    assert p["protected_count"] == 4
    response = clean(env, task, p, key="cleanup-same-request")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted_count"] == 1
    assert clean(env, task, p, key="cleanup-same-request").json()["data"] == response.json()["data"]
    after = texts(env, task)
    assert {v["id"] for v in after["versions"]} == {script["id"], copy["id"], latest["id"], bad_id}
    assert after["task"]["current_script"]["content"] == latest["content"]
    assert after["task"]["current_copy"]["script_id"] == script["id"]
    assert after["groups"][0]["output"]
    with env.sf() as db:
        assert db.get(m.TextVersion, latest["id"]).parent_id is None
        event = db.scalar(select(m.EvidenceEvent).where(m.EvidenceEvent.event_type == "text_history_cleaned"))
        assert event.details["pruned_parents"] == [[latest["id"], old["id"]]]
        assert list(db.execute(text("PRAGMA foreign_key_check"))) == []
    assert edit(env, task, "清理后继续修改？")["changed"]


def test_unused_joint_pair_deletes_together_but_original_response_survives(env, task_data):
    task = task_data["task"]
    generate(env, task)
    p = preview(env, task)
    assert p["removable_count"] == 2
    assert clean(env, task, p).status_code == 200
    data = texts(env, task)
    assert data["versions"] == [] and data["groups"][0]["output"]["raw_text"]
    assert preview(env, task)["removable_count"] == 0


def test_cleanup_rechecks_worker_candidates_and_active_references(env, task_data):
    task = task_data["task"]
    first = edit(env, task, "最初的稿件？")["version"]
    edit(env, task, "新的稿件？")
    p = preview(env, task)
    assert p["removable_ids"] == [first["id"]]
    # New queued work pins an old script without modifying task.revision.
    r = env.post(
        f"/tasks/{task}/generations",
        {
            "task_id": task,
            "expected_revision": p["expected_revision"],
            "mode": "copy",
            "base_script_id": first["id"],
        },
    )
    assert r.status_code == 202
    assert clean(env, task, p).status_code == 409
    assert preview(env, task)["removable_count"] == 0
    env.drain()
    # A newly returned candidate also invalidates an existing preview.
    p = preview(env, task)
    generate(env, task, mode="script")
    assert clean(env, task, p).status_code == 409


def test_confirmed_history_and_ownership_are_protected(env, task_data):
    task = task_data["task"]
    first = edit(env, task, "准备确认？")["version"]
    assert move(env, task, first["id"], "confirm").status_code == 200
    edit(env, task, "确认后另写新稿？")
    p = preview(env, task)
    assert p["removable_count"] == 0
    assert clean(env, task, p).json()["data"]["deleted_count"] == 0
    before = texts(env, task)["task"]["revision"]
    assert before == p["expected_revision"]
    with env.sf() as db:
        other = m.Creator(login_name="history-stranger", password_hash="unused")
        db.add(other)
        db.flush()
        db.get(m.Task, task).owner_id = other.id
        db.commit()
        count = db.scalar(select(func.count()).select_from(m.TextVersion))
    assert env.client.get(f"/api/v1/tasks/{task}/texts/cleanup-preview").status_code == 404
    assert clean(env, task, p).status_code == 404
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.TextVersion)) == count
