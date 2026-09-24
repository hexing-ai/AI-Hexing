from copy import deepcopy

import pytest

from backend.app.services.discovery_briefs import brief_for, source_hash, validate_batch, validate_brief


def candidate():
    return {
        "metadata": {
            "id": 1,
            "full_name": "test/new-tool",
            "description": "一个根据摄像头图像检测姿态的健身工具。",
        },
        "fragments": [
            {
                "id": "r1-f1",
                "text": "Uses a local webcam to count squats. Requires Python 3.10. Runs locally.",
            }
        ],
    }


def valid_fields():
    return {
        "purpose": {
            "text": "通过摄像头识别深蹲并计数。",
            "evidence": [{"ref": "r1-f1", "quote": "Uses a local webcam to count squats."}],
        }
    }


def test_brief_available_without_score_and_preserves_provenance():
    c = candidate()
    c["brief"] = validate_brief(valid_fields(), c)
    assert "score" not in c
    assert brief_for(c)["fields"]["purpose"]["text"] == "通过摄像头识别深蹲并计数。"
    assert brief_for(c)["fields"]["requirements"]["text"] == "资料不足"
    c["metadata"]["description"] = "Changed purpose"
    assert brief_for(c)["fields"]["purpose"]["text"] == "资料不足"


@pytest.mark.parametrize("change", ["cross_ref", "false_quote", "english", "command", "too_long", "blank"])
def test_unusable_field_not_presented_as_a_chinese_brief(change):
    value = valid_fields()
    field = value["purpose"]
    if change == "cross_ref":
        field["evidence"][0]["ref"] = "r2-f1"
    if change == "false_quote":
        field["evidence"][0]["quote"] = "Completely free online app."
    if change == "english":
        field["text"] = "This is an amazing tool."
    if change == "command":
        field["text"] = "使用 pip install python 安装。"
    if change == "too_long":
        field["text"] = "功能说明" * 30
    if change == "blank":
        field["text"] = "  "
    result = validate_brief(value, candidate())
    assert result["fields"]["purpose"] == {"text": "资料不足", "evidence": []}


def test_bad_or_duplicate_model_rows_retain_all_candidates():
    c = candidate()
    before = deepcopy(c)
    row = {"repo_id": 1, "fields": valid_fields()}
    assert validate_batch({"briefs": [row]}, [c])[0]["brief"]["fields"]["purpose"]["evidence"]
    assert validate_batch({"briefs": [row, row]}, [c])[0]["brief"]["fields"]["purpose"]["text"] == "资料不足"
    assert len(validate_batch({"briefs": "invalid"}, [c])) == 1
    assert c == before
    assert source_hash(c) == source_hash({**c, "score": {"total": 9}})


def test_summary_job_before_scoring_retains_sources_and_prevents_duplicates(env, monkeypatch):
    from backend.app import models as m
    from backend.app.services.discovery import discovery_payload

    c = candidate()
    with env.sf() as db:
        batch = m.Discovery(
            owner_id=env.owner_id,
            data_mode="mock",
            query="test",
            payload={"status": "sources_ready", "input": {"topic": "test"}, "candidates": [c]},
        )
        db.add(batch)
        db.commit()
        id = batch.id
    calls = []

    def summarize(kind, inputs):
        assert kind == "discovery_brief"
        calls.append(kind)
        return {"briefs": [{"repo_id": 1, "fields": valid_fields()}]}, {
            "model": "mock",
            "raw_text": "retained model text",
        }

    monkeypatch.setattr(env.worker.providers, "text", summarize)
    assert env.post(f"/discoveries/{id}/brief", {"discovery_id": id}).status_code == 202
    assert env.post(f"/discoveries/{id}/brief", {"discovery_id": id}).status_code == 409
    env.drain()
    response = env.client.get(f"/api/v1/discoveries/{id}").json()["data"]
    assert response["brief_status"] == "ready"
    assert not response.get("score_runs")
    assert response["candidates"][0]["brief"]["fields"]["purpose"]["text"] == "通过摄像头识别深蹲并计数。"
    assert response["candidates"][0]["fragments"] == c["fragments"]
    assert response["brief_runs"][0]["raw_text"] == "retained model text"
    assert response["brief_job"]["status"] == "succeeded"
    assert env.post(f"/discoveries/{id}/brief", {"discovery_id": id}).status_code == 409
    assert calls == ["discovery_brief"]
    with env.sf() as db:
        data = db.get(m.Discovery, id)
        projection = discovery_payload(data.payload, data.created_at)
        assert projection["candidates"][0]["brief"] == response["candidates"][0]["brief"]
        outsider = m.Creator(login_name="brief-outsider", password_hash="unused")
        db.add(outsider)
        db.flush()
        data.owner_id = outsider.id
        db.commit()
    assert env.client.get(f"/api/v1/discoveries/{id}").status_code == 404
    assert env.post(f"/discoveries/{id}/brief", {"discovery_id": id}).status_code == 404


def test_unusable_summary_output_keeps_raw_and_all_candidates(env, monkeypatch):
    from backend.app import models as m

    with env.sf() as db:
        batch = m.Discovery(
            owner_id=env.owner_id,
            data_mode="mock",
            query="test",
            payload={"input": {"topic": "test"}, "candidates": [candidate()], "status": "sources_ready"},
        )
        db.add(batch)
        db.commit()
        id = batch.id
    monkeypatch.setattr(
        env.worker.providers,
        "text",
        lambda kind, inputs: (None, {"model": "mock", "raw_text": "invalid JSON preserved"}),
    )
    assert env.post(f"/discoveries/{id}/brief", {"discovery_id": id}).status_code == 202
    env.drain()
    data = env.client.get(f"/api/v1/discoveries/{id}").json()["data"]
    assert data["brief_status"] == "incomplete" and len(data["candidates"]) == 1
    assert data["brief_runs"][0]["raw_text"] == "invalid JSON preserved"
    assert all(v["text"] == "资料不足" for v in data["candidates"][0]["brief"]["fields"].values())


def test_new_summary_is_a_budgeted_text_call(env):
    import time

    from pydantic import SecretStr

    from backend.app import models as m
    from backend.app.core.db import transaction
    from backend.app.core.errors import AppError
    from backend.app.services import costs
    from backend.app.services.cost_reconciliation import CHARGEABLE
    from backend.app.services.jobs import enqueue, text_inputs
    from backend.app.services.providers import text_request

    settings = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "qwen_api_key": SecretStr("isolated-test"),
        }
    )
    with transaction(env.sf) as db:
        batch = m.Discovery(
            owner_id=env.owner_id,
            data_mode="live",
            query="test",
            payload={"input": {"topic": "test"}, "candidates": [candidate()]},
        )
        db.add(batch)
        db.flush()
        id = batch.id
    inputs = {"discovery_id": id}
    with pytest.raises(AppError, match="COST_AUTHORIZATION_REQUIRED"), transaction(env.sf) as db:
        enqueue(db, settings, env.owner_id, "discovery_brief", inputs)
    with transaction(env.sf) as db:
        q = costs.quote(db, settings, env.owner_id, "discovery_brief", inputs)
        request = text_request(
            settings, "discovery_brief", text_inputs(db, settings, env.owner_id, "discovery_brief", inputs)
        )
        assert request["enable_thinking"] is False and request["max_tokens"] == 8000
        assert "准备条件" in request["messages"][0]["content"]
        p = m.CostPreview(
            owner_id=env.owner_id,
            data_mode="live",
            action="discovery_brief",
            input_hash=costs.digest(inputs),
            input_json=inputs,
            expires_at=time.time() + 600,
        )
        db.add(p)
        db.flush()
        db.add(m.CostQuote(owner_id=env.owner_id, data_mode="live", preview_id=p.id, payload=q))
        a = m.CostAuthorization(
            owner_id=env.owner_id,
            data_mode="live",
            preview_id=p.id,
            action="discovery_brief",
            input_hash=p.input_hash,
            max_calls=1,
            expires_at=p.expires_at,
        )
        db.add(a)
        db.flush()
        auth = a.id
    settings.cost_single_limit_micros = q["maximum_micros"] - 1
    with pytest.raises(AppError, match="COST_SINGLE_LIMIT"), transaction(env.sf) as db:
        enqueue(db, settings, env.owner_id, "discovery_brief", inputs, auth)
    settings.cost_single_limit_micros = 1_000_000
    with transaction(env.sf) as db:
        j = enqueue(db, settings, env.owner_id, "discovery_brief", inputs, auth)
        assert "discovery_brief" in CHARGEABLE
        assert costs.summary(db, settings, env.owner_id)["held_micros"] == q["maximum_micros"]
        costs.before_dispatch(db, settings, j, text_inputs(db, settings, env.owner_id, j.kind, inputs))
        costs.settle(
            db, j, {"model": settings.qwen_model, "usage": {"prompt_tokens": 100, "completion_tokens": 100}}
        )
        db.flush()
        assert costs.summary(db, settings, env.owner_id)["priced_micros"] == 100


def test_score_projection_uses_same_brief_as_cards():
    from backend.app.services.discovery import discovery_payload

    c = candidate()
    c["brief"] = validate_brief(valid_fields(), c)
    payload = {
        "candidates": [c],
        "score_runs": [
            {
                "rule_version": "discovery-v2",
                "recommendations": [candidate()],
                "evaluated_candidates": [candidate()],
            }
        ],
    }
    view = discovery_payload(payload, 1789000000)
    assert view["candidates"][0]["brief"] == view["score_runs"][0]["evaluated_candidates"][0]["brief"]
    assert view["candidates"][0]["brief"] == view["score_runs"][0]["recommendations"][0]["brief"]
