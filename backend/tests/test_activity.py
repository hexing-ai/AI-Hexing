from sqlalchemy import select

from backend.app import models as m
from backend.app.services.common import digest
from backend.tests.test_creation import generate


def test_scoped_records_quality_and_exact_result_link(env, task_data):
    task = task_data["task"]
    jid = generate(env, task)
    scoped = env.client.get("/api/v1/jobs", params={"task_id": task}).json()["data"]["items"]
    assert {j["kind"] for j in scoped} == {"import", "analysis", "generation"}
    result = next(j for j in scoped if j["id"] == jid)
    assert result["task_id"] == task and result["title"] == "demo/markdown-tool"
    assert result["status_label"] == "已完成 · 候选待采用"
    detail = env.client.get("/api/v1/jobs/" + jid).json()["data"]
    assert detail["actions"][0]["task_id"] == task
    assert detail["actions"][0]["generation_id"] == detail["result_ref"]["generation_id"]
    assert detail["result"]["content"]["script"]
    assert "raw_text" not in str(result)
    # Execution succeeded but one field failed quality: it must be in attention, not history.
    with env.sf() as db:
        version = db.scalar(
            select(m.TextVersion).where(m.TextVersion.generation_id == detail["result_ref"]["generation_id"])
        )
        version.payload = {**version.payload, "valid": False}
        db.commit()
    attention = env.client.get("/api/v1/jobs", params={"task_id": task, "bucket": "attention"}).json()[
        "data"
    ]["items"]
    assert len(attention) == 1 and attention[0]["id"] == jid
    assert attention[0]["status_label"] == "已完成 · 有内容需修正"
    assert not any(
        j["id"] == jid
        for j in env.client.get("/api/v1/jobs", params={"bucket": "history"}).json()["data"]["items"]
    )


def test_older_unknown_is_visible_and_billing_has_no_secrets(env, task_data):
    with env.sf() as db:
        unknown = m.Job(
            owner_id=env.owner_id,
            data_mode="mock",
            kind="voice_preview",
            created_at=1,
            input_json={"probe_voice_id": "test-voice", "signed_url": "private-signature-must-not-appear"},
            input_hash=digest("old"),
            status="needs_reconciliation",
        )
        db.add(unknown)
        db.flush()
        id = unknown.id
        db.add(
            m.Attempt(
                job_id=id,
                attempt_no=1,
                settings_summary={"model": "fixture", "secret": "never-output"},
                usage={"total_tokens": 10},
            )
        )
        for i in range(55):
            db.add(
                m.Job(
                    owner_id=env.owner_id,
                    data_mode="mock",
                    kind="import",
                    input_json={},
                    input_hash=digest(i),
                    status="succeeded",
                )
            )
        db.commit()
    result = env.client.get("/api/v1/jobs?bucket=attention").json()["data"]
    assert any(j["id"] == id for j in result["items"])
    response = env.client.get("/api/v1/jobs/" + id)
    detail = response.json()["data"]
    assert "不会自动重复调用" in detail["guidance"]
    assert detail["attempts"][0]["usage"] == {"total_tokens": 10}
    assert "private-signature" not in response.text and "never-output" not in response.text
    scoped = env.client.get("/api/v1/jobs", params={"task_id": task_data["task"]}).json()["data"]
    assert not any(j["id"] == id for j in scoped["items"])
    # Pagination reaches every older completed row with a stable timestamp/id ordering.
    ids = []
    cursor = None
    while True:
        params = {"bucket": "history", "limit": 10}
        if cursor:
            params["cursor"] = cursor
        data = env.client.get("/api/v1/jobs", params=params).json()["data"]
        ids.extend(j["id"] for j in data["items"])
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 57


def test_detail_and_filter_ownership(env, task_data):
    id = generate(env, task_data["task"])
    with env.sf() as db:
        stranger = m.Creator(login_name="activity-stranger", password_hash="unused")
        db.add(stranger)
        db.flush()
        db.get(m.Job, id).owner_id = stranger.id
        db.get(m.Task, task_data["task"]).owner_id = stranger.id
        db.commit()
    assert env.client.get("/api/v1/jobs/" + id).status_code == 404
    assert env.client.get("/api/v1/jobs", params={"task_id": task_data["task"]}).status_code == 404
    assert env.client.get("/api/v1/jobs", params={"cursor": id}).status_code == 404


def test_audio_result_readable_and_missing_asset_marked(env):
    from backend.app.services.assets import save_asset
    from backend.app.services.providers import wav_tone

    with env.sf() as db:
        asset = save_asset(db, env.settings, env.owner_id, "mock", wav_tone(), "validation_audio")
        job = m.Job(
            owner_id=env.owner_id,
            data_mode="mock",
            kind="voice_preview",
            input_json={},
            input_hash=digest("audio"),
            status="succeeded",
        )
        db.add(job)
        db.flush()
        run = m.ValidationRun(
            owner_id=env.owner_id,
            data_mode="mock",
            job_id=job.id,
            kind="voice_preview",
            contract_version="S1-2026-09-08",
            input_ref={},
            verdict="passed",
            output={
                "asset_id": asset.id,
                "duration_ms": 2000,
                "is_test_tone": True,
                "provider_voice_id": "never-show-remote-id",
            },
        )
        db.add(run)
        db.commit()
        id = job.id
        asset_id = asset.id
    response = env.client.get("/api/v1/jobs/" + id)
    assert response.json()["data"]["result"]["asset_id"] == asset_id
    assert "never-show" not in response.text
    with env.sf() as db:
        db.get(m.Asset, asset_id).state = "deleted"
        db.commit()
    detail = env.client.get("/api/v1/jobs/" + id).json()["data"]
    assert detail["status_label"] == "已完成 · 音频不可用"
    assert "asset_id" not in detail["result"]
