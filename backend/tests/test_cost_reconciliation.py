"""Invoice tests use isolated fake bill lines; no actual account bill is asserted."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.core.db import transaction
from backend.app.core.errors import AppError
from backend.app.schemas import CostReconcileConfirmIn
from backend.app.services import cost_reconciliation as billing
from backend.app.services import costs
from backend.tests.test_task_lifecycle import change as lifecycle_change
from backend.tests.test_task_lifecycle import expire


def bill_job(
    env,
    *,
    legacy=False,
    ledger_status="unknown",
    job_status="needs_reconciliation",
    amount=600000,
    priced=None,
    sent=True,
    task_id=None,
):
    with transaction(env.sf) as db:
        job = m.Job(
            owner_id=env.owner_id,
            data_mode="mock",
            kind="writing",
            status=job_status,
            input_json={"task_id": task_id, "private_text": "PRIVATE_TASK_BODY"},
            input_hash="fixture-hash",
        )
        db.add(job)
        db.flush()
        db.add(
            m.Attempt(
                job_id=job.id,
                attempt_no=1,
                dispatch_state="dispatching" if sent else "not_sent",
                provider_request_id="fixture-request-" + job.id,
                usage=None,
                settings_summary={"model": "qwen3.7-flash", "private_key": "PRIVATE_NOT_FOR_VIEW"},
            )
        )
        if not legacy:
            p = m.CostPreview(
                owner_id=env.owner_id,
                data_mode="mock",
                action="writing",
                input_hash=job.input_hash,
                input_json=job.input_json,
                expires_at=time.time() + 600,
            )
            db.add(p)
            db.flush()
            q = m.CostQuote(
                owner_id=env.owner_id,
                data_mode="mock",
                preview_id=p.id,
                payload={
                    "model": "qwen3.7-flash",
                    "maximum_micros": amount,
                    "input_tokens_upper": 1000000,
                    "output_tokens_upper": 4000,
                    "price_version": "fixture-only",
                    "source": "https://example.test/pricing",
                },
            )
            db.add(q)
            db.flush()
            db.add(
                m.CostLedger(
                    owner_id=env.owner_id,
                    data_mode="mock",
                    job_id=job.id,
                    quote_id=q.id,
                    month=costs.month(),
                    reserved_micros=amount,
                    priced_micros=priced,
                    status=ledger_status,
                )
            )
        return job.id


def data(env, id):
    r = env.client.get("/api/v1/cost-records/" + id)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def budget(env):
    return env.client.get("/api/v1/cost-budget").json()["data"]


def draft(env, id, **extra):
    return {
        "expected_revision": data(env, id)["revision"],
        "resolution": "billed",
        "actual_amount": "0.123456",
        "billing_month": costs.month(),
        "evidence_type": "bill_line",
        "evidence_reference": "fake-line-" + id,
        "match_details": "按模型、日期及请求编号核对本作业全部调用",
        "reason": "隔离账单核对测试",
        **extra,
    }


def preview(env, id, body):
    r = env.post("/cost-records/" + id + "/preview", body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def commit(env, id, body=None, key=None):
    body = body or draft(env, id)
    p = preview(env, id, body)
    r = env.post(
        "/cost-records/" + id + "/reconciliations",
        {**body, "preview_token": p["preview_token"], "confirm": True},
        key=key,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_unknown_invoice_precision_keeps_original_and_preview_is_readonly(env):
    id = bill_job(env)
    initial = data(env, id)
    p = preview(env, id, draft(env, id))
    assert budget(env)["held_micros"] == 600000
    assert p["budget_after"]["actual_micros"] == 123456 and p["budget_after"]["held_micros"] == 0
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(m.CostReconciliation)) == 0
    result = commit(env, id)
    assert result["budget"]["actual_micros"] == 123456 and result["budget"]["held_micros"] == 0
    now = data(env, id)
    assert now["raw"] == initial["raw"]
    assert now["effective"]["actual_micros"] == 123456
    assert "PRIVATE_TASK_BODY" not in str(now) and "PRIVATE_NOT_FOR_VIEW" not in str(now)
    assert len(env.client.get("/api/v1/cost-records?state=reconciled").json()["data"]["items"]) == 1
    assert env.client.get("/api/v1/cost-records").json()["data"]["items"] == []
    with env.sf() as db:
        assert db.get(m.Job, id).status == "needs_reconciliation"
        assert db.scalar(select(func.count()).select_from(m.Job)) == 1
        assert db.scalar(select(m.Attempt)).usage is None


def test_corrections_zero_and_reopen_are_append_only(env):
    id = bill_job(env, ledger_status="priced", priced=300000)
    assert budget(env)["priced_micros"] == 300000
    commit(env, id)
    first = data(env, id)["latest"]
    commit(env, id, draft(env, id, actual_amount="0.2", reason="更正第一次录入的金额"))
    assert budget(env)["actual_micros"] == 200000 and budget(env)["priced_micros"] == 0
    body = {"expected_revision": 2, "resolution": "reopened", "reason": "原明细匹配有误，撤回核对"}
    p = preview(env, id, body)
    assert p["budget_after"]["held_micros"] == 600000
    commit(env, id, body)
    assert budget(env)["actual_micros"] == 0 and budget(env)["held_micros"] == 600000
    commit(
        env, id, draft(env, id, resolution="not_charged", actual_amount="0", reason="供应商明确证实未收费")
    )
    assert budget(env)["held_micros"] == budget(env)["actual_micros"] == 0
    hist = env.client.get(f"/api/v1/cost-records/{id}/history?limit=2").json()["data"]
    assert [r["revision"] for r in hist["items"]] == [4, 3] and hist["next_before"] == 3
    older = env.client.get(f"/api/v1/cost-records/{id}/history?before=3").json()["data"]
    assert older["items"][-1] == first
    assert data(env, id)["raw"]["ledger"]["priced_micros"] == 300000


@pytest.mark.parametrize(
    "changes",
    [
        {"actual_amount": "-1"},
        {"actual_amount": "NaN"},
        {"actual_amount": "1e-3"},
        {"actual_amount": "0.0000001"},
        {"actual_amount": "1000000.000001"},
        {"actual_amount": 0.1},
        {"actual_amount": "0"},
        {"resolution": "not_charged", "actual_amount": "1"},
        {"billing_month": "2999-01"},
        {"evidence_reference": ""},
        {"match_details": ""},
    ],
)
def test_invalid_money_and_missing_evidence_never_writes(env, changes):
    id = bill_job(env)
    r = env.post("/cost-records/" + id + "/preview", draft(env, id, **changes))
    assert r.status_code == 422, r.text
    assert data(env, id)["revision"] == 0 and budget(env)["held_micros"] == 600000


def test_preview_staleness_idempotency_and_worker_updates(env):
    id = bill_job(env)
    body = draft(env, id)
    p = preview(env, id, body)
    bill_job(env)
    assert (
        env.post(
            f"/cost-records/{id}/reconciliations",
            {**body, "preview_token": p["preview_token"], "confirm": True},
        ).status_code
        == 409
    )
    p = preview(env, id, body)
    with transaction(env.sf) as db:
        costs.settle(
            db,
            db.get(m.Job, id),
            {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 20}},
        )
    assert (
        env.post(
            f"/cost-records/{id}/reconciliations",
            {**body, "preview_token": p["preview_token"], "confirm": True},
        ).status_code
        == 409
    )
    p = preview(env, id, body)
    body = {**body, "preview_token": p["preview_token"], "confirm": True}
    first = env.post(f"/cost-records/{id}/reconciliations", body, key="repeat-invoice-confirm")
    repeat = env.post(f"/cost-records/{id}/reconciliations", body, key="repeat-invoice-confirm")
    assert first.status_code == repeat.status_code == 201
    assert first.json()["data"] == repeat.json()["data"]
    assert (
        env.post(
            f"/cost-records/{id}/reconciliations",
            {**body, "actual_amount": "0.5"},
            key="repeat-invoice-confirm",
        ).status_code
        == 409
    )
    assert env.post(f"/cost-records/{id}/reconciliations", body).status_code == 409
    assert data(env, id)["revision"] == 1


def test_concurrent_same_version_and_duplicate_bill_line_cannot_double_release(env):
    id = bill_job(env)
    body = draft(env, id)
    p = preview(env, id, body)
    form = CostReconcileConfirmIn(**body, preview_token=p["preview_token"], confirm=True)

    def run():
        try:
            with transaction(env.sf) as db:
                billing.reconcile(db, env.settings, db.get(m.Job, id), form)
            return "saved"
        except AppError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results.count("saved") == 1 and results.count("COST_REVISION_CONFLICT") == 1
    other = bill_job(env)
    r = env.post(
        f"/cost-records/{other}/preview", draft(env, other, evidence_reference="  FAKE-line-" + id + "  ")
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "COST_EVIDENCE_DUPLICATE"
    assert budget(env)["held_micros"] == 600000 and budget(env)["actual_micros"] == 123456


def test_historical_unknown_and_month_attribution_cannot_shift_budget(env, monkeypatch):
    id = bill_job(env, legacy=True)
    with transaction(env.sf) as db:
        a = db.scalar(select(m.Attempt).where(m.Attempt.job_id == id))
        a.created_at = datetime(2026, 8, 15, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    monkeypatch.setattr(costs, "month", lambda: "2026-09")
    assert budget(env)["historical_unknown_count"] == 1
    commit(env, id, draft(env, id, actual_amount="0.5"))
    assert data(env, id)["effective"]["budget_month"] == "2026-08"
    assert budget(env)["actual_micros"] == 0 and budget(env)["historical_unknown_count"] == 0
    current_id = bill_job(env)
    commit(env, current_id, draft(env, current_id, actual_amount="0.8", billing_month="2026-08"))
    assert data(env, current_id)["effective"]["budget_month"] == "2026-09"
    assert budget(env)["actual_micros"] == 800000
    commit(
        env, id, {"expected_revision": 1, "resolution": "reopened", "reason": "旧账单归属存在疑问，撤回核对"}
    )
    assert budget(env)["held_micros"] == 500000 and budget(env)["historical_unknown_count"] == 1
    monkeypatch.setattr(costs, "month", lambda: "2026-10")
    assert budget(env)["held_micros"] == 500000 and budget(env)["actual_micros"] == 0


def test_overrun_truth_recorded_and_monthly_budget_still_enforced(env):
    id = bill_job(env, ledger_status="overrun", priced=2000000)
    assert budget(env)["blocked_by_overrun"]
    commit(env, id, draft(env, id, actual_amount="31"))
    state = budget(env)
    assert (
        state["actual_micros"] == 31000000
        and state["remaining_micros"] == 0
        and not state["blocked_by_overrun"]
    )
    with env.sf() as db:
        with pytest.raises(AppError, match="COST_MONTHLY_LIMIT"):
            costs.check_limit(db, env.settings, env.owner_id, 1)
    commit(env, id, {"expected_revision": 1, "resolution": "reopened", "reason": "超额明细需要重新核对"})
    assert budget(env)["held_micros"] == 31000000 and budget(env)["blocked_by_overrun"]


def test_late_usage_never_overrides_invoice_and_result_cleanup_remains_blocked(env, task_data):
    id = bill_job(env, task_id=task_data["task"])
    commit(env, id)
    with transaction(env.sf) as db:
        costs.settle(
            db,
            db.get(m.Job, id),
            {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
        )
        costs.stop(db, db.get(m.Job, id))
    assert budget(env)["actual_micros"] == 123456 and budget(env)["held_micros"] == 0
    assert data(env, id)["raw"]["ledger"]["priced_micros"] == 280
    task = task_data["task"]
    assert lifecycle_change(env, task, "trash").status_code == 200
    expire(env, task)
    env.worker.cleanup()
    with env.sf() as db:
        assert db.get(m.TaskLifecycle, task).cleanup_status == "waiting_for_result_reconciliation"
        a = db.scalar(select(m.Attempt).where(m.Attempt.job_id == id))
        a.dispatch_state = "finished"
        db.commit()
    env.worker.cleanup()
    assert env.client.get(f"/api/v1/tasks/{task}/context").status_code == 404
    assert data(env, id)["latest"]["actual_micros"] == 123456
    assert env.client.get(f"/api/v1/cost-records/{id}/history").status_code == 200
    with env.sf() as db:
        assert db.get(m.TaskLifecycle, task).state == "purged"
        assert db.scalar(select(func.count()).select_from(m.CostReconciliation)) == 1


def test_permissions_lifecycle_and_no_send_rules(env, task_data):
    id = bill_job(env, task_id=task_data["task"])
    body = draft(env, id)
    assert env.client.post(f"/api/v1/cost-records/{id}/preview", json=body).status_code == 403
    assert env.client.get("/api/v1/cost-records/missing").status_code == 404
    running = bill_job(env, job_status="running")
    assert env.post(f"/cost-records/{running}/preview", draft(env, running)).status_code == 409
    unsent = bill_job(env, sent=False, ledger_status="released", job_status="failed")
    assert env.post(f"/cost-records/{unsent}/preview", draft(env, unsent)).status_code == 422
    commit(env, unsent, draft(env, unsent, resolution="not_charged", actual_amount="0"))
    assert lifecycle_change(env, task_data["task"], "archive").status_code == 200
    commit(env, id)
    with transaction(env.sf) as db:
        db.get(m.Job, id).data_mode = "live"
    assert env.client.get(f"/api/v1/cost-records/{id}").status_code == 404
    with transaction(env.sf) as db:
        db.get(m.Job, id).data_mode = "mock"
        c = m.Creator(login_name="other-b03", password_hash="unused")
        db.add(c)
        db.flush()
        db.get(m.Job, id).owner_id = c.id
    assert env.client.get(f"/api/v1/cost-records/{id}/history").status_code == 404
    with transaction(env.sf) as db:
        free = m.Job(
            owner_id=env.owner_id,
            data_mode="mock",
            kind="export",
            input_hash="free",
            input_json={},
            status="succeeded",
        )
        db.add(free)
        db.flush()
        free_id = free.id
    assert env.client.get(f"/api/v1/cost-records/{free_id}").status_code == 422
    env.client.cookies.clear()
    assert env.client.get("/api/v1/cost-records").status_code == 401


def test_list_stable_pagination_and_mode_isolation(env):
    ids = {bill_job(env, legacy=True) for _ in range(3)}
    seen = []
    cursor = None
    while True:
        r = env.client.get("/api/v1/cost-records?limit=1" + ("&cursor=" + cursor if cursor else "")).json()[
            "data"
        ]
        seen.extend(v["id"] for v in r["items"])
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and set(seen) == ids
    with env.sf() as db:
        assert (
            costs.summary(db, env.settings, "other")["remaining_micros"]
            == env.settings.cost_monthly_limit_micros
        )
        assert (
            costs.summary(db, env.settings.model_copy(update={"provider_mode": "live"}), env.owner_id)[
                "historical_unknown_count"
            ]
            == 0
        )


def test_reopened_invoice_keeps_larger_late_usage_and_unsent_zero_does_not_respend(env):
    id = bill_job(env)
    commit(env, id, draft(env, id, actual_amount="0.8"))
    commit(env, id, {"expected_revision": 1, "resolution": "reopened", "reason": "明细有误需重新确认金额"})
    with transaction(env.sf) as db:
        costs.settle(
            db,
            db.get(m.Job, id),
            {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 2_000_000, "completion_tokens": 100}},
        )
    assert budget(env)["held_micros"] > 800000
    assert budget(env)["blocked_by_overrun"]
    commit(env, id, draft(env, id, actual_amount="0.25"))
    assert budget(env)["held_micros"] == 0 and budget(env)["actual_micros"] == 250000
    assert not budget(env)["blocked_by_overrun"]


def test_parallel_different_jobs_cannot_reuse_one_bill_detail(env):
    ids = [bill_job(env), bill_job(env)]
    forms = []
    for id in ids:
        body = draft(env, id, evidence_reference="one-shared-bill-line")
        p = preview(env, id, body)
        forms.append(CostReconcileConfirmIn(**body, preview_token=p["preview_token"], confirm=True))

    def run(index):
        try:
            with transaction(env.sf) as db:
                billing.reconcile(db, env.settings, db.get(m.Job, ids[index]), forms[index])
            return "saved"
        except AppError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results.count("saved") == 1
    assert results.count("COST_EVIDENCE_DUPLICATE") == 1
    assert budget(env)["held_micros"] == 600000
