import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.core.auth import hasher
from backend.app.core.db import transaction
from backend.app.core.errors import AppError, ProviderError
from backend.app.main import create_app
from backend.app.schemas import parse_json, validate_analysis, validate_writing
from backend.app.services.assets import asset_path, inspect_wav
from backend.app.services.common import normalize_repository
from backend.app.services.providers import Providers, wav_tone
from backend.app.worker import Worker


def getjob(env, id):
    return env.client.get("/api/v1/jobs/" + id).json()["data"]


def upload(env, data=None, name="sample.wav"):
    data = wav_tone(6) if data is None else data
    return env.client.post(
        "/api/v1/assets",
        files={"file": (name, data, "audio/wav")},
        data={
            "kind": "voice_sample",
            "purpose": "validation",
            "cleanup_after": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat(),
            "consent": json.dumps(
                {
                    "is_own_voice": True,
                    "purpose": "voice_clone_validation",
                    "notice_version": "S1-2026-09-08",
                    "cleanup_acknowledged": True,
                }
            ),
        },
        headers={
            "Origin": env.settings.web_origin,
            "X-CSRF-Token": env.csrf,
            "Idempotency-Key": str(time.time_ns()),
        },
    )


def validation(env, kind, **kw):
    return env.post("/validation-runs", {"kind": kind, "contract_version": "S1-2026-09-08", **kw})


@pytest.fixture
def voice_data(env):
    r = upload(env)
    assert r.status_code == 201, r.text
    sample = r.json()["data"]
    r = validation(
        env, "voice_enrollment", sample_asset_id=sample["id"], consent_record_id=sample["consent_record_id"]
    )
    assert r.status_code == 202, r.text
    env.drain()
    j = getjob(env, r.json()["data"]["job_id"])
    assert j["status"] == "succeeded", j
    return sample, j["result_ref"]["probe_voice_id"]


def test_health_auth_and_csrf(env):
    assert env.client.get("/api/v1/health").json()["data"] == {"status": "ok"}
    assert (
        env.client.post("/api/v1/imports", json={"repository_url": "https://github.com/a/b"}).status_code
        == 403
    )
    assert env.post("/auth/logout").status_code == 204
    assert env.client.get("/api/v1/tasks").status_code == 401
    assert env.client.get("/api/v1/openapi.json").status_code == 401


def test_bad_login_rate_limit_persists(env):
    for _ in range(10):
        r = env.client.post(
            "/api/v1/auth/login",
            json={"login_name": "tester", "password": "wrong-password-leak-check"},
            headers={"Origin": env.settings.web_origin},
        )
        assert r.status_code == 401
    r = env.client.post(
        "/api/v1/auth/login",
        json={"login_name": "tester", "password": "wrong-password-leak-check"},
        headers={"Origin": env.settings.web_origin},
    )
    assert r.status_code == 429
    assert "wrong-password-leak-check" not in r.text


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://evil.test/a/b",
        "https://github.com/a/b/issues",
        "https://github.com/a/b/tree/main",
        "https://github.com:443/a/b",
        "https://me@github.com/a/b",
        "https://127.0.0.1/a/b",
        "https://github.com/a/%2e%2e",
        "https://github.com/a/..",
    ],
)
def test_bad_repository_urls(url):
    with pytest.raises(AppError):
        normalize_repository(url)


def test_normalization():
    assert (
        normalize_repository("https://github.com/Owner/Repo.git/?utm_source=x#foo")
        == "https://github.com/owner/repo"
    )


def test_immutable_snapshot_task_and_cache(env, task_data):
    t = env.client.get("/api/v1/tasks/" + task_data["task"]).json()["data"]
    assert t["status"] == "in_progress"
    old = env.client.get("/api/v1/snapshots/" + task_data["snapshot"]).json()["data"]
    r = env.post("/tools/" + old["tool_id"] + "/snapshots")
    env.drain()
    assert getjob(env, r.json()["data"]["job_id"])["status"] == "succeeded"
    assert env.client.get("/api/v1/tasks/" + t["id"]).json()["data"]["snapshot_id"] == old["id"]
    r = env.post("/snapshots/" + old["id"] + "/analyses", {"audience": "测试观众"})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == task_data["analysis"]
    reopened = TestClient(create_app(env.settings, env.sf))
    reopened.cookies.update(env.client.cookies)
    assert reopened.get("/api/v1/tasks/" + t["id"]).json()["data"] == t
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Tool)) == 1


def test_idempotency_and_parallel_creation(env, task_data):
    body = {"review_id": task_data["review"], "audience": "测试观众"}
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: env.post("/tasks", body, key="same-task-operation"), range(10)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["data"]["id"] for r in responses}) == 1
    assert env.post("/tasks", {**body, "audience": "另一人群"}, key="same-task-operation").status_code == 409
    assert (
        env.post("/tasks", body, key="new-task-operation").json()["data"]["id"]
        != responses[0].json()["data"]["id"]
    )


def test_import_dedup(env):
    for url in ["https://github.com/A/B", "https://github.com/a/b.git?x=y"]:
        assert env.post("/imports", {"repository_url": url}).status_code == 202
    env.drain()
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Tool)) == 1


def test_cross_owner_and_mode_denied(env, task_data, voice_data):
    with env.sf() as db:
        db.add(m.Creator(login_name="other", password_hash=hasher.hash("different-test-password")))
        db.commit()
    second = TestClient(create_app(env.settings, env.sf))
    second.post(
        "/api/v1/auth/login",
        json={"login_name": "other", "password": "different-test-password"},
        headers={"Origin": env.settings.web_origin},
    )
    ids = [
        ("tasks", task_data["task"]),
        ("snapshots", task_data["snapshot"]),
        ("analyses", task_data["analysis"]),
        ("probe-voices", voice_data[1]),
        ("assets", voice_data[0]["id"] + "/content"),
    ]
    for route, id in ids:
        assert second.get("/api/v1/" + route + "/" + id).status_code == 404
    live_settings = env.settings.model_copy(update={"provider_mode": "live"})
    live = TestClient(create_app(live_settings, env.sf))
    live.cookies.update(env.client.cookies)
    assert live.get("/api/v1/tasks/" + task_data["task"]).status_code == 404


def test_output_parsing_and_evidence(env):
    p = Providers(env.settings)
    fragments = [{"id": "f1", "text": "source"}]
    raw, _ = p.text("analysis", {"fragments": fragments})
    assert validate_analysis(parse_json("```json\n" + json.dumps(raw) + "\n```"), fragments)["highlights"]
    bad = copy.deepcopy(raw)
    bad["facts"][0]["evidence_refs"] = ["fake"]
    with pytest.raises(AppError):
        validate_analysis(bad, fragments)
    bad = copy.deepcopy(raw)
    bad["facts"][0]["source_type"] = "creator_test"
    with pytest.raises(AppError):
        validate_analysis(bad, fragments)
    with pytest.raises(AppError):
        parse_json('__import__("os").system("echo x")')
    raw["highlights"] = [{**raw["highlights"][0], "id": str(i)} for i in range(5)]
    checked = validate_analysis(raw, fragments)
    assert len(checked["highlights"]) == 3 and checked["warnings"]


def test_writing_partial_and_task_not_ready(env, task_data):
    r = validation(env, "writing", task_id=task_data["task"])
    env.drain()
    j = getjob(env, r.json()["data"]["job_id"])
    assert j["status"] == "succeeded", j
    run = env.client.get("/api/v1/validation-runs/" + j["result_ref"]["validation_run_id"]).json()["data"]
    assert all(run["output"]["checks"].values())
    raw, _ = Providers(env.settings).text("writing", {"tool_name": "a", "fragments": [{"id": "f1"}]})
    raw["script"]["intro"] = "关注我"
    output, checks = validate_writing(raw, "a", [{"id": "f1"}])
    assert not checks["script"] and checks["copy"] and "copy" in output
    assert env.client.get("/api/v1/tasks/" + task_data["task"]).json()["data"]["status"] == "in_progress"


@pytest.mark.parametrize("seconds", [29.9, 30.0, 35.0, 35.1])
def test_exact_duration(seconds):
    assert inspect_wav(wav_tone(seconds)) == pytest.approx(seconds * 1000, abs=0.001)


def test_live_sample_save_does_not_require_provider_configuration(env, monkeypatch):
    env.settings.provider_mode = "live"
    env.settings.live_calls_enabled = False
    env.settings.voice_api_key = SecretStr("")
    env.settings.voice_sample_allowed_host = ""

    def no_external_call(*args, **kwargs):
        pytest.fail("Local sample upload must not call the voice provider")

    monkeypatch.setattr(Providers, "enroll", no_external_call)
    response = upload(env)
    assert response.status_code == 201, response.text
    sample = response.json()["data"]
    with env.sf() as db:
        assert db.get(m.Asset, sample["id"]).data_mode == "live"
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0
        assert db.scalar(select(func.count()).select_from(m.ProbeVoice)) == 0
    response = env.post(
        "/assets/" + sample["id"] + "/source-url",
        {"url": "https://example.oss-cn-beijing.aliyuncs.com/sample.wav?signature=test-only"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SAMPLE_HOST_NOT_CONFIGURED"
    response = validation(
        env, "voice_enrollment", sample_asset_id=sample["id"], consent_record_id=sample["consent_record_id"]
    )
    assert response.status_code >= 400
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0
    assert upload(env, b"not a WAV").status_code == 415


def test_upload_boundaries(env):
    assert upload(env, b"not a WAV").status_code == 415
    assert upload(env, name="../sample.wav").status_code == 422
    assert upload(env, wav_tone(1)).status_code == 422
    with pytest.raises(AppError):
        inspect_wav(wav_tone()[:-100])
    with pytest.raises(AppError):
        asset_path(env.settings, "../../outside")


def test_voice_reuse_disable_delete(env, voice_data):
    sample, voice = voice_data
    assert (
        validation(env, "voice_reuse", probe_voice_id=voice, text="b", previous_run_id="bad").status_code
        == 409
    )
    r = validation(env, "voice_preview", probe_voice_id=voice, text="第一段文字")
    env.drain()
    j = getjob(env, r.json()["data"]["job_id"])
    assert j["status"] == "succeeded", j
    first = j["result_ref"]["validation_run_id"]
    v = env.client.get("/api/v1/probe-voices/" + voice).json()["data"]
    assert (
        env.post(
            "/probe-voices/" + voice + "/confirm",
            {"preview_run_id": first, "accepted": True, "expected_revision": v["revision"]},
        ).status_code
        == 200
    )
    r = validation(env, "voice_reuse", probe_voice_id=voice, text="第二段文字", previous_run_id=first)
    env.drain()
    j = getjob(env, r.json()["data"]["job_id"])
    assert j["status"] == "succeeded", j
    second = j["result_ref"]["validation_run_id"]
    a = env.client.get("/api/v1/validation-runs/" + first).json()["data"]["output"]
    b = env.client.get("/api/v1/validation-runs/" + second).json()["data"]["output"]
    assert a["voice_fingerprint"] == b["voice_fingerprint"] and a["text_hash"] != b["text_hash"]
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.ProbeVoice)) == 1
    assert env.client.get("/api/v1/assets/" + b["asset_id"] + "/content").status_code == 200
    v = env.client.get("/api/v1/probe-voices/" + voice).json()["data"]
    assert "provider_voice_id" not in v
    assert (
        env.post("/probe-voices/" + voice + "/disable", {"expected_revision": v["revision"]}).status_code
        == 200
    )
    assert validation(env, "voice_preview", probe_voice_id=voice, text="不允许").status_code == 409
    assert env.client.get("/api/v1/assets/" + b["asset_id"] + "/content").status_code == 200
    v = env.client.get("/api/v1/probe-voices/" + voice).json()["data"]
    r = env.post(
        "/probe-voices/" + voice, {"confirm": True, "expected_revision": v["revision"]}, method="DELETE"
    )
    env.drain()
    assert getjob(env, r.json()["data"]["job_id"])["result_ref"]["state"] == "deleted"
    assert env.client.get("/api/v1/assets/" + sample["id"] + "/content").status_code == 404


def test_cancel_late_enrollment_is_not_revived(env):
    sample = upload(env).json()["data"]
    r = validation(
        env, "voice_enrollment", sample_asset_id=sample["id"], consent_record_id=sample["consent_record_id"]
    )
    id = r.json()["data"]["job_id"]
    assert env.worker.claim() == id
    job, inputs = env.worker.prepare(id)
    assert env.post("/jobs/" + id + "/cancel").status_code == 200
    result = env.worker.perform(job, inputs)
    env.worker.finish(id, result)
    assert getjob(env, id)["status"] == "cancelled"
    with env.sf() as db:
        voice = db.get(m.ProbeVoice, result["voice_id"])
        assert voice.status == "remote_deletion_pending" and voice.provider_voice_id


def test_recovery_after_sending_never_resends(env):
    r = env.post("/imports", {"repository_url": "https://github.com/a/b"})
    id = r.json()["data"]["job_id"]
    env.worker.claim()
    env.worker.prepare(id)
    with transaction(env.sf) as db:
        db.get(m.Job, id).lease_expires_at = 0
    w = Worker(env.settings, env.sf)
    w.recover()
    assert getjob(env, id)["status"] == "needs_reconciliation"
    assert not w.run_once()


def test_recovery_before_sending_and_from_checkpoint(env):
    id = env.post("/imports", {"repository_url": "https://github.com/a/b"}).json()["data"]["job_id"]
    env.worker.claim()
    with transaction(env.sf) as db:
        db.get(m.Job, id).lease_expires_at = 0
    env.worker.recover()
    assert getjob(env, id)["status"] == "queued"
    env.worker.claim()
    job, inputs = env.worker.prepare(id)
    result = env.worker.perform(job, inputs)
    env.worker.checkpoint(job, result)
    with transaction(env.sf) as db:
        db.get(m.Job, id).lease_expires_at = 0
    env.worker.recover()
    assert getjob(env, id)["status"] == "succeeded"
    assert not env.worker.checkpoint_path(id).exists()


def test_unknown_exception_redacted_and_no_retry(env, task_data, monkeypatch):
    def failed(*args):
        raise ProviderError("UNKNOWN", "结果未知", uncertain=True)

    monkeypatch.setattr(env.worker.providers, "text", failed)
    r = validation(env, "writing", task_id=task_data["task"])
    env.drain()
    j = getjob(env, r.json()["data"]["job_id"])
    assert j["status"] == "needs_reconciliation" and not j["retryable"]
    assert not env.worker.run_once()


def test_live_without_key_and_experiment_disabled(env):
    live = env.settings.model_copy(update={"provider_mode": "live"})
    p = Providers(live)
    assert "QWEN_API_KEY" in p.readiness("analysis")
    with pytest.raises(AppError):
        p.check("analysis")
    production = env.settings.model_copy(update={"app_env": "production"})
    client = TestClient(create_app(production, env.sf))
    client.cookies.update(env.client.cookies)
    assert client.get("/api/v1/validation-runs").status_code == 403


def test_cost_authorization_binding(env, task_data):
    # Test deterministic accounting using live namespace fixtures, never send network requests.
    with transaction(env.sf) as db:
        for cls in [m.Tool, m.Snapshot, m.Analysis, m.Review, m.Task]:
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
    live = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "provider_contract_version": "S1-2026-09-08",
            "qwen_api_key": SecretStr("test-secret-not-real"),
        }
    )
    client = TestClient(create_app(live, env.sf))
    client.cookies.update(env.client.cookies)

    def post(path, body):
        return client.post(
            "/api/v1" + path,
            json=body,
            headers={
                "Origin": live.web_origin,
                "X-CSRF-Token": env.csrf,
                "Idempotency-Key": str(time.time_ns()),
            },
        )

    body = {"kind": "writing", "task_id": task_data["task"], "contract_version": "S1-2026-09-08"}
    assert post("/validation-runs", body).status_code == 409
    p = post("/cost-authorizations/preview", {"action": "writing", "input": body})
    assert p.status_code == 201, p.text
    p = p.json()["data"]
    a = {
        "preview_id": p["preview_id"],
        "action": "writing",
        "input_hash": p["input_hash"],
        "max_calls": 1,
        "accept_unknown_cost": False,
    }
    assert post("/cost-authorizations", a).status_code == 409
    auth = post("/cost-authorizations", {**a, "max_cost": float(p["maximum_cost"])}).json()["data"]["id"]
    r = post("/validation-runs", {**body, "cost_authorization_id": auth})
    assert r.status_code == 202, r.text
    assert post("/validation-runs", {**body, "cost_authorization_id": auth}).status_code == 409
    assert "test-secret-not-real" not in client.get("/api/v1/capabilities").text


def test_cleanup_due_sample(env):
    sample = upload(env).json()["data"]
    with transaction(env.sf) as db:
        db.get(m.Asset, sample["id"]).cleanup_after = time.time() - 1
    env.worker.cleanup()
    assert env.client.get("/api/v1/assets/" + sample["id"] + "/content").status_code == 404


def test_session_expiry_and_unified_http_errors(env):
    assert "error" in env.client.get("/api/v1/missing").json()
    with transaction(env.sf) as db:
        for row in db.scalars(select(m.Session)):
            row.expires_at = 0
    assert env.client.get("/api/v1/tasks").status_code == 401


def test_parallel_model_job_has_one_attempt(env, task_data):
    body = {"kind": "writing", "task_id": task_data["task"], "contract_version": "S1-2026-09-08"}
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(
            pool.map(lambda _: env.post("/validation-runs", body, key="same-model-operation"), range(10))
        )
    assert all(r.status_code == 202 for r in responses)
    ids = {r.json()["data"]["job_id"] for r in responses}
    assert len(ids) == 1
    env.drain()
    with env.sf() as db:
        assert (
            db.scalar(select(func.count()).select_from(m.Attempt).where(m.Attempt.job_id == next(iter(ids))))
            == 1
        )


def test_ready_checkpoint_recovers_without_resend(env):
    id = env.post("/imports", {"repository_url": "https://github.com/a/b"}).json()["data"]["job_id"]
    env.worker.claim()
    job, inputs = env.worker.prepare(id)
    result = env.worker.perform(job, inputs)
    env.worker.checkpoint(job, result)
    env.worker.fail(id, ProviderError("SAVE_INTERRUPTED", "保存中断", uncertain=True))
    env.worker.recover()
    assert getjob(env, id)["status"] == "succeeded"
    assert getjob(env, id)["error"] is None


def test_cancelled_cloning_holds_sample_until_result(env):
    sample = upload(env).json()["data"]
    r = validation(
        env, "voice_enrollment", sample_asset_id=sample["id"], consent_record_id=sample["consent_record_id"]
    )
    id = r.json()["data"]["job_id"]
    env.worker.claim()
    env.worker.prepare(id)
    env.post("/jobs/" + id + "/cancel")
    assert env.post("/assets/" + sample["id"], {"confirm": True}, method="DELETE").status_code == 409


def test_oversized_and_missing_evidence_are_visible(env, monkeypatch):
    def source(url):
        return (
            {"id": 200, "full_name": "a/b", "html_url": url, "description": "source", "license": None},
            "x" * 30000,
            [],
        )

    monkeypatch.setattr(env.worker.providers, "github", source)
    r = env.post("/imports", {"repository_url": "https://github.com/a/b"})
    env.drain()
    snap_id = getjob(env, r.json()["data"]["job_id"])["result_ref"]["snapshot_id"]
    snapshot = env.client.get("/api/v1/snapshots/" + snap_id).json()["data"]
    assert len(snapshot["readme_text"]) == 30000
    assert sum(len(f["text"]) for f in snapshot["fragments"]) == 24000
    assert any("truncated" in message for message in snapshot["missing_fields"])


def test_failed_result_keeps_unknown_cost_and_safe_error(env, task_data, monkeypatch):
    def invalid(*args):
        raise RuntimeError("fake-secret-should-never-be-returned")

    monkeypatch.setattr(env.worker.providers, "text", invalid)
    r = validation(env, "writing", task_id=task_data["task"])
    env.drain()
    job = getjob(env, r.json()["data"]["job_id"])
    assert job["status"] == "needs_reconciliation"
    assert "fake-secret" not in json.dumps(job)


@pytest.mark.parametrize(
    "query",
    [
        "Expires=1",
        "Expires=invalid",
        "Expires=1&Expires=2",
        "x-oss-date=20200101T000000Z&x-oss-expires=1800",
        "x-oss-date=invalid&x-oss-expires=1800",
    ],
)
def test_sample_signature_expiry_validation(query):
    from backend.app.services.assets import validate_sample_url_expiry

    with pytest.raises(AppError) as error:
        validate_sample_url_expiry("https://bucket.oss-cn-beijing.aliyuncs.com/a.wav?" + query)
    assert error.value.code in {"SAMPLE_URL_EXPIRED", "SAMPLE_URL_INVALID"}
    assert query not in error.value.message


def test_expired_sample_url_rejected_before_job_or_cost_preview(env):
    from backend.app.services.assets import validate_sample_url_expiry

    future = str(int(time.time()) + 1800)
    validate_sample_url_expiry("https://bucket.oss-cn-beijing.aliyuncs.com/a.wav?Expires=" + future)
    env.settings.provider_mode = "live"
    env.settings.voice_sample_allowed_host = "bucket.oss-cn-beijing.aliyuncs.com"
    sample = upload(env).json()["data"]
    expired = "https://bucket.oss-cn-beijing.aliyuncs.com/a.wav?Expires=1&Signature=private-test"
    response = env.post("/assets/" + sample["id"] + "/source-url", {"url": expired})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SAMPLE_URL_EXPIRED"
    with env.sf() as db:
        # Simulate a link that was bound earlier and expired before submission.
        db.get(m.Asset, sample["id"]).source_url = expired
        db.commit()
    response = env.post(
        "/cost-authorizations/preview",
        {
            "action": "voice_enrollment",
            "input": {
                "kind": "voice_enrollment",
                "contract_version": "S1-2026-09-08",
                "sample_asset_id": sample["id"],
                "consent_record_id": sample["consent_record_id"],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SAMPLE_URL_EXPIRED"
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0
        assert db.scalar(select(func.count()).select_from(m.CostPreview)) == 0


def test_saved_live_audio_recovery_without_provider_resend(env, voice_data, monkeypatch):
    import base64

    from backend.tests.test_audio_headers import streaming_header

    _, voice_id = voice_data
    response = validation(env, "voice_preview", probe_voice_id=voice_id, text="测试已有音频恢复。")
    job_id = response.json()["data"]["job_id"]
    assert env.worker.claim() == job_id
    job, inputs = env.worker.prepare(job_id)
    result = env.worker.perform(job, inputs)
    original = base64.b64decode(result["audio_base64"])
    result["audio_base64"] = base64.b64encode(streaming_header(original)).decode()
    result["metrics"] = {"model": "cosyvoice-v3.5-flash", "request_id": "saved-provider-request"}
    env.worker.checkpoint(job, result)
    # Model a completed live response rejected by the old local WAV validator.
    env.settings.provider_mode = "live"
    with env.sf() as db:
        row = db.get(m.Job, job_id)
        row.data_mode, row.status = "live", "failed"
        row.error = {"code": "AUDIO_FORMAT_INVALID", "message": "old local format rejection"}
        db.get(m.ProbeVoice, voice_id).data_mode = "live"
        run = db.scalar(select(m.ValidationRun).where(m.ValidationRun.job_id == job_id))
        run.data_mode, run.verdict = "live", "failed"
        db.commit()
        before = db.scalar(select(func.count()).select_from(m.Attempt))

    def forbidden(*args, **kwargs):
        pytest.fail("Local recovery must not call providers")

    monkeypatch.setattr(env.worker, "perform", forbidden)
    env.worker.recover_audio(job_id)
    env.worker.recover_audio(job_id)  # Idempotent; no duplicate artifact.
    with env.sf() as db:
        row = db.get(m.Job, job_id)
        assert row.status == "succeeded" and row.error is None
        asset = db.get(m.Asset, row.result_ref["asset_id"])
        assert asset_path(env.settings, asset.storage_key).read_bytes() == original
        assert db.scalar(select(func.count()).select_from(m.Attempt)) == before
        attempt = db.scalar(select(m.Attempt).where(m.Attempt.job_id == job_id))
        assert attempt.provider_request_id == "saved-provider-request"
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.EvidenceEvent)
                .where(
                    m.EvidenceEvent.event_type == "audio_local_recovery", m.EvidenceEvent.object_id == job_id
                )
            )
            == 1
        )
        run = db.scalar(select(m.ValidationRun).where(m.ValidationRun.job_id == job_id))
        assert run.output["is_test_tone"] is False and run.verdict == "passed"
    assert not env.worker.checkpoint_path(job_id).exists()
