from sqlalchemy import select

from backend.app import models as m
from backend.app.services.discovery import rank, search
from backend.app.services.providers import Providers


def generate(env, task, mode="joint", base=None, drain=True):
    revision = env.client.get("/api/v1/tasks/" + task).json()["data"]["revision"]
    response = env.post(
        "/tasks/" + task + "/generations",
        {"task_id": task, "mode": mode, "expected_revision": revision, "base_script_id": base},
    )
    assert response.status_code == 202, response.text
    if drain:
        env.drain()
    return response.json()["data"]["job_id"]


def texts(env, task):
    return env.client.get("/api/v1/tasks/" + task + "/texts").json()["data"]


def move(env, task, version, action):
    body = {"expected_revision": texts(env, task)["task"]["revision"]}
    if action == "confirm":
        body["facts_reviewed"] = True
    return env.post(f"/tasks/{task}/texts/{version}/{action}", body)


def test_candidates_edits_stale_copy_conflicts_and_idempotency(env, task_data):
    task = task_data["task"]
    generate(env, task)
    data = texts(env, task)
    assert data["task"]["current_script"] is None
    script = next(v for v in data["versions"] if v["kind"] == "script")
    copy = next(v for v in data["versions"] if v["kind"] == "copy")
    assert copy["script_id"] == script["id"]
    assert move(env, task, copy["id"], "adopt").status_code == 200
    assert move(env, task, copy["id"], "confirm").status_code == 409
    assert move(env, task, script["id"], "adopt").status_code == 200
    assert move(env, task, script["id"], "confirm").status_code == 200
    assert move(env, task, copy["id"], "confirm").status_code == 200
    generate(env, task, drain=False)
    revision = texts(env, task)["task"]["revision"]
    body = {
        "kind": "script",
        "parent_id": script["id"],
        "expected_revision": revision,
        "content": {**script["content"], "pain": "手工改稿，想先看看效果？"},
        "evidence_map": script["evidence_map"],
    }
    first = env.post("/tasks/" + task + "/texts", body, key="same-edit-operation")
    assert first.status_code == 201, first.text
    repeat = env.post("/tasks/" + task + "/texts", body, key="same-edit-operation")
    assert repeat.json()["data"] == first.json()["data"]
    assert env.post("/tasks/" + task + "/texts", body).status_code == 409
    env.drain()
    current = texts(env, task)["task"]
    assert current["current_script"]["content"]["pain"].startswith("手工改稿")
    assert not current["current_script"]["confirmed_at"]
    assert current["copy_stale"] and current["current_copy"]["id"] == copy["id"]
    assert current["status"] != "ready"


def test_partial_failure_copy_retained_but_cannot_confirm(env, task_data, monkeypatch):
    original = env.worker.providers.text

    def malformed(kind, inputs):
        raw, metrics = original(kind, inputs)
        raw["script"] = {"intro": 3}
        return raw, metrics

    monkeypatch.setattr(env.worker.providers, "text", malformed)
    task = task_data["task"]
    generate(env, task)
    data = texts(env, task)
    assert data["groups"][0]["output"]["raw_output"]["script"]["intro"] == 3
    copy = next(v for v in data["versions"] if v["kind"] == "copy")
    script = next(v for v in data["versions"] if v["kind"] == "script")
    assert copy["valid"] and not script["valid"]
    assert move(env, task, script["id"], "adopt").status_code == 409
    assert move(env, task, copy["id"], "adopt").status_code == 200
    assert move(env, task, copy["id"], "confirm").status_code == 409


def test_copy_only_and_cross_task_isolation(env, task_data):
    task = task_data["task"]
    generate(env, task, mode="script")
    data = texts(env, task)
    assert len(data["versions"]) == 1
    script = data["versions"][0]
    assert move(env, task, script["id"], "adopt").status_code == 200
    generate(env, task, mode="copy", base=script["id"])
    assert len(texts(env, task)["versions"]) == 2
    assert texts(env, task)["task"]["current_script"]["id"] == script["id"]
    other = env.post("/tasks", {"review_id": task_data["review"], "audience": "测试观众"}).json()["data"][
        "id"
    ]
    assert (
        env.post(
            "/tasks/" + other + "/generations",
            {"task_id": other, "expected_revision": 1, "mode": "copy", "base_script_id": script["id"]},
        ).status_code
        == 422
    )
    with env.sf() as db:
        original_owner = db.get(m.Task, other)
        stranger = m.Creator(login_name="stranger", password_hash="unused")
        db.add(stranger)
        db.flush()
        original_owner.owner_id = stranger.id
        db.commit()
    assert env.client.get("/api/v1/tasks/" + other + "/texts").status_code == 404


def test_discovery_ranking_and_history(env):
    response = env.post("/discoveries", {"topic": "markdown"})
    assert response.status_code == 202
    id = response.json()["data"]["discovery_id"]
    env.drain()
    batch = env.client.get("/api/v1/discoveries/" + id).json()["data"]
    assert len(batch["candidates"]) == 8
    response = env.post("/discoveries/" + id + "/score", {"discovery_id": id})
    assert response.status_code == 202
    env.drain()
    batch = env.client.get("/api/v1/discoveries/" + id).json()["data"]
    score = batch["score_runs"][0]
    assert len(score["recommendations"]) == 5
    assert score["metrics"]["enable_thinking"] is False
    assert env.post("/discoveries/" + id + "/score", {"discovery_id": id}).status_code == 409
    providers = Providers(env.settings)
    candidates = search(providers, {"topic": "markdown"})["candidates"]
    raw, _ = providers.text("discovery_score", {"candidates": candidates})
    raw["scores"] += raw["scores"][:1]
    assert len(rank(raw, candidates)["recommendations"]) == 5
    raw["scores"] = raw["scores"][:8]
    for item in raw["scores"][2:]:
        item["relevance"] = 0
    assert len(rank(raw, candidates)["recommendations"]) == 2
    raw["scores"][0]["evidence"]["completeness"] = ["unknown"]
    assert len(rank(raw, candidates)["recommendations"]) == 1


def test_cancelled_generation_does_not_add_versions(env, task_data):
    id = generate(env, task_data["task"], drain=False)
    claimed = env.worker.claim()
    job, inputs = env.worker.prepare(claimed)
    result = env.worker.perform(job, inputs)
    assert env.post("/jobs/" + id + "/cancel").status_code == 200
    env.worker.checkpoint(job, result)
    env.worker.finish(id, result)
    assert texts(env, task_data["task"])["versions"] == []
    with env.sf() as db:
        assert db.scalar(select(m.Job).where(m.Job.id == id)).status == "cancelled"


def test_single_section_rewrite_locks_other_paragraphs(env, task_data, monkeypatch):
    task = task_data["task"]
    generate(env, task, mode="script")
    base = texts(env, task)["versions"][0]
    original = env.worker.providers.text

    def rewrite(kind, inputs):
        raw, metrics = original(kind, inputs)
        raw["script"]["pain"] = "只改这一段？"
        raw["script"]["intro"] = "模型误改固定句"
        raw["script"]["feature"] = "模型误改其他段"
        return raw, metrics

    monkeypatch.setattr(env.worker.providers, "text", rewrite)
    r = env.post(
        "/tasks/" + task + "/generations",
        {
            "task_id": task,
            "expected_revision": 1,
            "mode": "script",
            "base_script_id": base["id"],
            "rewrite_section": "pain",
        },
    )
    assert r.status_code == 202
    env.drain()
    result = texts(env, task)
    version = result["versions"][0]
    assert version["content"]["pain"] == "只改这一段？"
    assert version["content"]["feature"] == base["content"]["feature"]
    assert version["content"]["intro"] == base["content"]["intro"]
    assert result["groups"][0]["output"]["raw_output"]["script"]["intro"] == "模型误改固定句"


def test_scoring_double_submit_and_invalid_manual_draft(env, task_data):
    search_response = env.post("/discoveries", {"topic": "markdown"})
    id = search_response.json()["data"]["discovery_id"]
    env.drain()
    assert env.post("/discoveries/" + id + "/score", {"discovery_id": id}).status_code == 202
    assert env.post("/discoveries/" + id + "/score", {"discovery_id": id}).status_code == 409
    task = task_data["task"]
    response = env.post(
        "/tasks/" + task + "/texts",
        {
            "kind": "script",
            "expected_revision": 1,
            "content": {"pain": "<script>alert(1)</script>"},
            "evidence_map": {},
        },
    )
    assert response.status_code == 201
    version = response.json()["data"]["version"]
    assert not version["valid"]
    assert version["content"]["pain"] == "<script>alert(1)</script>"
    assert move(env, task, version["id"], "confirm").status_code == 409
