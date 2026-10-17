"""B-04 uses isolated databases and simulated HTTP, never real enrollment."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.core.db import transaction
from backend.app.core.errors import AppError, ProviderError
from backend.app.main import create_app
from backend.app.services import costs
from backend.app.services.assets import save_asset
from backend.app.services.jobs import canonical_input, enqueue
from backend.app.services.providers import Providers, enrollment_request, wav_tone
from backend.app.worker import Worker


@pytest.fixture
def enrollment(env):
    s = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "provider_contract_version": "S1-2026-09-08",
            "voice_api_key": SecretStr("test-voice-only"),
            "voice_sample_allowed_host": "test.oss-cn-beijing.aliyuncs.com",
        }
    )
    with transaction(env.sf) as db:
        a = save_asset(db, s, env.owner_id, "live", wav_tone(6), "voice_sample")
        a.consent_record_id = "test-consent"
        a.source_url = "https://test.oss-cn-beijing.aliyuncs.com/own.wav?Signature=test-only&Expires=" + str(
            int(time.time() + 600)
        )
        a.cleanup_after = time.time() + 3600
        aid = a.id
    with TestClient(create_app(s, env.sf)) as client:
        r = client.post(
            "/api/v1/auth/login",
            json={"login_name": "tester", "password": "test-password-only"},
            headers={"Origin": s.web_origin},
        )
        live = replace(
            env, settings=s, client=client, worker=Worker(s, env.sf), csrf=r.json()["data"]["csrf_token"]
        )
        inputs = {
            "kind": "voice_enrollment",
            "contract_version": "S1-2026-09-08",
            "sample_asset_id": aid,
            "consent_record_id": "test-consent",
        }
        yield live, inputs


def preview(live, inputs):
    r = live.post("/cost-authorizations/preview", {"action": "voice_enrollment", "input": inputs})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def authorize(live, inputs):
    p = preview(live, inputs)
    r = live.post(
        "/cost-authorizations",
        {
            "preview_id": p["preview_id"],
            "action": "voice_enrollment",
            "input_hash": p["input_hash"],
            "max_calls": 1,
            "max_cost": 0,
            "accept_unknown_cost": False,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def submit(live, inputs, auth, key=None):
    return live.post("/validation-runs", {**inputs, "cost_authorization_id": auth}, key=key)


def test_zero_quote_private_once_and_separate_synthesis(enrollment):
    live, inputs = enrollment
    p = preview(live, inputs)
    assert p["maximum_cost"] == "0.000000"
    assert p["quote"]["billing_rule"] == costs.ENROLLMENT_RULE
    assert "后续试听和合成单独计费" in p["quote"]["notice"]
    assert all(v not in json.dumps(p) for v in ["Signature", "test-voice-only", "own.wav", "test-consent"])
    assert submit(live, inputs, None).status_code == 409
    auth = authorize(live, inputs)
    a = submit(live, inputs, auth, "enroll-once")
    assert a.status_code == 202
    assert submit(live, inputs, auth, "enroll-once").json()["data"] == a.json()["data"]
    assert submit(live, inputs, auth).status_code == 409
    with live.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.CostLedger)) == 1
        assert db.scalar(select(m.CostLedger)).reserved_micros == 0
        v = m.ProbeVoice(
            owner_id=live.owner_id,
            data_mode="live",
            sample_asset_id=inputs["sample_asset_id"],
            provider_scope=live.worker.providers.scope(),
            provider_voice_id="test-voice",
            status="ready",
        )
        db.add(v)
        db.flush()
        q = costs.quote(
            db, live.settings, live.owner_id, "voice_preview", {"probe_voice_id": v.id, "text": "你好"}
        )
        assert q["maximum_micros"] == len("你好".encode()) * 80 > 0


@pytest.mark.parametrize(
    "setting,value",
    [
        ("voice_target_model", "qwen3-tts-flash"),
        ("voice_region", "singapore"),
        ("voice_enrollment_endpoint", "https://dashscope.aliyuncs.com/wrong"),
        (
            "voice_enrollment_endpoint",
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization?change=1",
        ),
    ],
)
def test_unverified_scope_is_not_free(enrollment, setting, value):
    live, inputs = enrollment
    setattr(live.settings, setting, value)
    with live.sf() as db, pytest.raises(AppError, match="PRICE_UNVERIFIED"):
        costs.quote(db, live.settings, live.owner_id, "voice_enrollment", inputs)


@pytest.mark.parametrize(
    "change", ["key", "sha", "url", "consent", "host", "expired_sample", "expired_url", "price"]
)
def test_change_after_authorization_blocks_dispatch(enrollment, monkeypatch, change):
    live, inputs = enrollment
    aid = authorize(live, inputs)
    r = submit(live, inputs, aid)
    jid = r.json()["data"]["job_id"]
    if change == "key":
        live.settings.voice_api_key = SecretStr("changed-test-key")
    elif change == "host":
        live.settings.voice_sample_allowed_host = "other.oss-cn-beijing.aliyuncs.com"
    elif change == "price":
        live.settings.enrollment_price_valid_until = date(2000, 1, 1)
    else:
        with transaction(live.sf) as db:
            a = db.get(m.Asset, inputs["sample_asset_id"])
            if change == "sha":
                a.sha256 = "different"
            if change == "url":
                a.source_url += "&changed=1"
            if change == "consent":
                a.consent_record_id = "other"
            if change == "expired_sample":
                a.cleanup_after = time.time() - 1
            if change == "expired_url":
                a.source_url = "https://test.oss-cn-beijing.aliyuncs.com/own.wav?Expires=1&Signature=test"
    w = live.worker
    assert w.claim() == jid
    with pytest.raises(AppError) as e:
        w.prepare(jid)
    w.fail(jid, e.value)
    with live.sf() as db:
        assert costs.ledger_for(db, db.get(m.Job, jid)).status == "released"
        assert not list(db.scalars(select(m.Attempt).where(m.Attempt.dispatch_state != "not_sent")))
    assert not w.run_once()


@pytest.mark.parametrize("limit", ["cost_single_limit_micros", "cost_monthly_limit_micros"])
def test_zero_configuration_still_pauses(enrollment, limit):
    live, inputs = enrollment
    setattr(live.settings, limit, 0)
    r = live.post("/cost-authorizations/preview", {"action": "voice_enrollment", "input": inputs})
    assert r.json()["error"]["code"] == "COST_CALLS_PAUSED"


def test_zero_permission_does_not_generalize_and_exhausted_balance_allows_free(enrollment):
    live, inputs = enrollment
    with transaction(live.sf) as db:
        # Existing bill fills this month's positive budget.
        j = m.Job(
            owner_id=live.owner_id,
            data_mode="live",
            kind="writing",
            status="succeeded",
            input_json={},
            input_hash="old",
        )
        db.add(j)
        db.flush()
        db.add(
            m.CostReconciliation(
                owner_id=live.owner_id,
                data_mode="live",
                job_id=j.id,
                revision=1,
                resolution="billed",
                actual_micros=30_000_000,
                hold_micros=0,
                budget_month=costs.month(),
                payload={},
            )
        )
    assert preview(live, inputs)["budget"]["remaining_micros"] == 0
    with live.sf() as db:
        with pytest.raises(AppError, match="COST_SINGLE_LIMIT"):
            costs.check_limit(db, live.settings, live.owner_id, 0)
        with pytest.raises(AppError, match="COST_MONTHLY_LIMIT"):
            costs.check_limit(db, live.settings, live.owner_id, 80)
        with pytest.raises(AppError, match="COST_SINGLE_LIMIT"):
            costs.check_quote_limit(db, live.settings, live.owner_id, {"maximum_micros": 0})


def test_concurrent_same_permission_only_one_job(enrollment):
    live, inputs = enrollment
    auth = authorize(live, inputs)

    def attempt(_):
        try:
            with transaction(live.sf) as db:
                return enqueue(
                    db,
                    live.settings,
                    live.owner_id,
                    "voice_enrollment",
                    canonical_input("voice_enrollment", inputs),
                    auth,
                ).id
        except AppError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("COST_AUTHORIZATION_INVALID") == 1
    with live.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.Job)) == 1


def test_success_unknown_late_result_and_legacy_preserved(enrollment):
    live, inputs = enrollment
    auth = authorize(live, inputs)
    jid = submit(live, inputs, auth).json()["data"]["job_id"]
    w = live.worker
    assert w.claim() == jid
    _, prepared = w.prepare(jid)
    w.fail(jid, ProviderError("VOICE_RESULT_UNKNOWN", "test", uncertain=True))
    assert not w.run_once()
    with live.sf() as db:
        assert costs.ledger_for(db, db.get(m.Job, jid)).status == "unknown"
        assert costs.summary(db, live.settings, live.owner_id)["pending_count"] == 1
    w.finish(
        jid,
        {
            "voice_id": prepared["voice_id"],
            "provider_voice_id": "test-returned-voice",
            "metrics": {
                "request_id": "test-request",
                "model": live.settings.voice_target_model,
                "enrollment_completed": True,
            },
        },
    )
    with transaction(live.sf) as db:
        j = db.get(m.Job, jid)
        ledger = costs.ledger_for(db, j)
        assert ledger.status == "priced" and ledger.priced_micros == 0
        assert db.scalar(select(m.Attempt)).usage is None
        assert db.scalar(select(func.count()).select_from(m.CostReconciliation)) == 0
        legacy = m.Job(
            owner_id=live.owner_id,
            data_mode="live",
            kind="voice_enrollment",
            status="succeeded",
            input_json={},
            input_hash="legacy",
        )
        db.add(legacy)
        db.flush()
        db.add(m.Attempt(job_id=legacy.id, attempt_no=1, dispatch_state="finished"))
        db.flush()
        assert costs.summary(db, live.settings, live.owner_id)["historical_unknown_count"] == 1
        before = ledger.updated_at
        costs.settle(db, j, {"model": live.settings.voice_target_model, "enrollment_completed": True})
        assert ledger.updated_at == before


@pytest.mark.parametrize(
    "output", [{"voice_id": "test-created"}, {"voice_id": ""}, {}, None, {"voice_id": 42}]
)
def test_adapter_uses_quoted_request_and_never_invents_usage(enrollment, monkeypatch, output):
    live, inputs = enrollment
    with live.sf() as db:
        a = db.get(m.Asset, inputs["sample_asset_id"])
        url = a.source_url
        q = costs.quote(db, live.settings, live.owner_id, "voice_enrollment", inputs)

    def respond(request):
        body = json.loads(request.content)
        assert body == enrollment_request(live.settings, url)
        assert costs.digest(body) == q["request_hash"]
        return httpx.Response(200, json={"output": output, "request_id": "test-request"})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(respond)))
    if output == {"voice_id": "test-created"}:
        voice, metrics = Providers(live.settings).enroll(url)
        assert voice == "test-created" and metrics["enrollment_completed"] is True
        assert "usage" not in metrics
    else:
        with pytest.raises(ProviderError, match="VOICE_RESULT_UNKNOWN"):
            Providers(live.settings).enroll(url)
