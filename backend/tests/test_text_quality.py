import json

import httpx
import respx
from pydantic import SecretStr
from sqlalchemy import select

from backend.app import models as m
from backend.app.services.providers import Providers
from backend.app.services.quality import inspect_writing


def test_malformed_evidence_and_partial_fields():
    result = inspect_writing(
        {
            "script": {"pain": 7},
            "copy": {"body": "保留文案", "tags": ["测试"]},
            "evidence_map": {"feature": [{}]},
        },
        "工具",
        [{"id": "f1"}],
        {},
    )
    assert result["checks"]["copy"] and not result["checks"]["script"]
    assert not result["checks"]["evidence"]
    assert any(i["path"] == "script.pain" for i in result["issues"])
    assert result["content"]["copy"]["body"] == "保留文案"


def test_unknown_license_and_false_test_blocked(env):
    raw, _ = Providers(env.settings).text("writing", {"tool_name": "a", "fragments": [{"id": "f1"}]})
    raw["copy"]["tags"] = ["GitHub开源"]
    raw["script"]["pain"] = "我测试了，免费商用有效"
    report = inspect_writing(raw, "a", [{"id": "f1"}], {"metadata": {"license": None}})
    assert not report["checks"]["claims"]
    assert {"LICENSE_UNKNOWN", "UNVERIFIED_TEST", "PRICE_LICENSE_CLAIM"} <= {
        i["code"] for i in report["issues"]
    }


@respx.mock
def test_invalid_json_saved_with_usage_and_restart_no_resend(env, task_data):
    provider = Providers(
        env.settings.model_copy(
            update={
                "provider_mode": "live",
                "live_calls_enabled": True,
                "provider_contract_version": "S1-2026-09-08",
                "qwen_api_key": SecretStr("fixture"),
            }
        )
    )
    route = respx.post(provider.s.qwen_base_url + "/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "fixture-request",
                "usage": {"total_tokens": 12},
                "choices": [{"message": {"content": '<script>alert("test")</script> invalid json'}}],
            },
        )
    )
    env.worker.providers = provider
    r = env.post(
        "/validation-runs",
        {"kind": "writing", "task_id": task_data["task"], "contract_version": "S1-2026-09-08"},
    )
    job_id = r.json()["data"]["job_id"]
    # Simulate death after checkpoint, then recover without another provider call.
    claimed = env.worker.claim()
    job, inputs = env.worker.prepare(claimed)
    assert inputs["metadata"]["license"] is None
    assert inputs["highlights"][0]["role"] == "primary"
    result = env.worker.perform(job, inputs)
    env.worker.checkpoint(job, result)
    with env.sf() as db:
        j = db.get(m.Job, job_id)
        j.lease_expires_at = 0
        db.commit()
    env.worker.recover()
    env.worker.recover()
    with env.sf() as db:
        run = db.scalar(select(m.ValidationRun).where(m.ValidationRun.job_id == job_id))
        assert run.verdict == "failed"
        assert run.output["raw_text"].startswith("<script>")
        assert run.output["metrics"]["usage"]["total_tokens"] == 12
        assert run.output["issues"][0]["code"] == "JSON_OBJECT_REQUIRED"
        assert db.get(m.Job, job_id).status == "succeeded"
        run_id = run.id
    assert route.call_count == 1
    assert env.client.get("/api/v1/validation-runs/" + run_id).status_code == 200
    env.client.cookies.clear()
    assert env.client.get("/api/v1/validation-runs/" + run_id).status_code == 401


def test_historical_review_candidate_offline():
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "fixtures/reviewed-writing.json").read_text())
    result = inspect_writing(raw, "sketchbook", [{"id": "f1"}, {"id": "f2"}], {"metadata": {"license": None}})
    assert all(result["checks"].values())
    assert result["human_review_required"]
