"""Budget tests use isolated databases and never call a real provider."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from backend.app import models as m
from backend.app.core.db import transaction
from backend.app.core.errors import AppError, ProviderError
from backend.app.services import costs
from backend.app.services.jobs import enqueue
from backend.app.worker import Worker


@pytest.fixture
def budget(env, task_data):
    with transaction(env.sf) as db:
        for cls in (m.Tool, m.Snapshot, m.Analysis, m.Review, m.Task):
            for row in db.scalars(select(cls)):
                row.data_mode = "live"
    s = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "provider_contract_version": "S1-2026-09-08",
            "qwen_api_key": SecretStr("test-only"),
            "voice_api_key": SecretStr("test-voice-only"),
        }
    )
    inputs = {"kind": "writing", "contract_version": "S1-2026-09-08", "task_id": task_data["task"]}
    return s, inputs


def authorize(env, budget):
    s, inputs = budget
    with transaction(env.sf) as db:
        p = m.CostPreview(
            owner_id=env.owner_id,
            data_mode="live",
            action="writing",
            input_hash=costs.digest(inputs),
            input_json=inputs,
            expires_at=time.time() + 600,
        )
        db.add(p)
        db.flush()
        q = m.CostQuote(
            owner_id=env.owner_id,
            data_mode="live",
            preview_id=p.id,
            payload=costs.quote(db, s, env.owner_id, "writing", inputs),
        )
        a = m.CostAuthorization(
            owner_id=env.owner_id,
            data_mode="live",
            preview_id=p.id,
            action="writing",
            input_hash=p.input_hash,
            max_calls=1,
            expires_at=p.expires_at,
        )
        db.add_all([q, a])
        db.flush()
        return a.id, q.payload["maximum_micros"]


def submit(env, budget, auth):
    s, inputs = budget
    with transaction(env.sf) as db:
        return enqueue(db, s, env.owner_id, "writing", inputs, auth).id


def state(env, budget):
    with env.sf() as db:
        return costs.summary(db, budget[0], env.owner_id)


def test_limits_and_atomic_concurrent_reservations(env, budget):
    a, maximum = authorize(env, budget)
    b, _ = authorize(env, budget)
    budget[0].cost_single_limit_micros = maximum - 1
    with pytest.raises(AppError, match="COST_SINGLE_LIMIT"):
        submit(env, budget, a)
    budget[0].cost_single_limit_micros = maximum
    budget[0].cost_monthly_limit_micros = maximum

    def attempt(auth):
        try:
            return submit(env, budget, auth)
        except AppError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [a, b]))
    assert results.count("COST_MONTHLY_LIMIT") == 1
    assert state(env, budget)["held_micros"] == maximum
    assert state(env, budget)["remaining_micros"] == 0


def test_unsent_release_and_unknown_hold_with_late_settlement(env, budget):
    a, maximum = authorize(env, budget)
    jobid = submit(env, budget, a)
    with transaction(env.sf) as db:
        costs.stop(db, db.get(m.Job, jobid))
    assert state(env, budget)["held_micros"] == 0
    a, _ = authorize(env, budget)
    jobid = submit(env, budget, a)
    with transaction(env.sf) as db:
        j = db.get(m.Job, jobid)
        db.add(m.Attempt(job_id=j.id, attempt_no=1, dispatch_state="dispatching"))
        db.flush()
        costs.stop(db, j)
    assert state(env, budget)["held_micros"] == maximum
    assert state(env, budget)["pending_count"] == 1
    with transaction(env.sf) as db:
        j = db.get(m.Job, jobid)
        for _ in range(2):
            costs.settle(
                db, j, {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
            )
    assert state(env, budget)["priced_micros"] == 280
    assert state(env, budget)["held_micros"] == 0


def test_dispatch_rechecks_input_and_limit(env, budget):
    a, maximum = authorize(env, budget)
    jobid = submit(env, budget, a)
    budget[0].cost_single_limit_micros = maximum - 1
    w = Worker(budget[0], env.sf)
    assert w.claim() == jobid
    with pytest.raises(AppError) as e:
        w.prepare(jobid)
    assert e.value.code == "COST_SINGLE_LIMIT"
    w.fail(jobid, e.value)
    assert state(env, budget)["held_micros"] == 0
    budget[0].cost_single_limit_micros = 1_000_000
    a, _ = authorize(env, budget)
    jobid = submit(env, budget, a)
    with transaction(env.sf) as db:
        task = db.get(m.Task, budget[1]["task_id"])
        task.audience = "changed after preview"
    assert w.claim() == jobid
    with pytest.raises(AppError) as e:
        w.prepare(jobid)
    assert e.value.code == "COST_QUOTE_CHANGED"


def test_worker_unknown_cancel_and_checkpoint_settlement(env, budget):
    a, maximum = authorize(env, budget)
    jobid = submit(env, budget, a)
    w = Worker(budget[0], env.sf)
    assert w.claim() == jobid
    j, _ = w.prepare(jobid)
    w.fail(jobid, ProviderError("UNKNOWN", "test", uncertain=True))
    assert state(env, budget)["held_micros"] == maximum
    with transaction(env.sf) as db:
        db.get(m.Job, jobid).status = "cancelled"
    # A cancelled remote call may still complete and incur charges.
    w.finish(
        jobid,
        {"metrics": {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 100, "completion_tokens": 100}}},
    )
    assert state(env, budget)["priced_micros"] == 100


def test_cross_month_dispatch_and_unknown_carry(env, budget, monkeypatch):
    a, maximum = authorize(env, budget)
    jobid = submit(env, budget, a)
    monkeypatch.setattr(costs, "month", lambda: "2026-10")
    w = Worker(budget[0], env.sf)
    w.claim()
    w.prepare(jobid)
    with env.sf() as db:
        assert costs.ledger_for(db, db.get(m.Job, jobid)).month == "2026-10"
    w.fail(jobid, ProviderError("UNKNOWN", "test", uncertain=True))
    monkeypatch.setattr(costs, "month", lambda: "2026-11")
    assert state(env, budget)["held_micros"] == maximum
    # Calendar uses Shanghai rather than machine-local timezone.
    monkeypatch.undo()
    timestamp = datetime(2026, 9, 30, 16, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    monkeypatch.setattr(costs.time, "time", lambda: timestamp)
    assert costs.month() == "2026-10"


@pytest.mark.parametrize("change", ["model", "key", "price", "thinking"])
def test_quote_changes_fail_closed(env, budget, change, monkeypatch):
    a, _ = authorize(env, budget)
    if change == "model":
        budget[0].qwen_model = "unpriced"
    if change == "key":
        budget[0].qwen_api_key = SecretStr("different")
    if change == "price":
        monkeypatch.setattr(costs, "PRICE_VERSION", "changed")
    if change == "thinking":
        budget[0].qwen_writing_thinking = True
    with pytest.raises(AppError):
        submit(env, budget, a)
    assert state(env, budget)["held_micros"] == 0


def test_overrun_blocks_future_spend_and_invalid_usage_holds(env, budget):
    a, maximum = authorize(env, budget)
    jobid = submit(env, budget, a)
    with transaction(env.sf) as db:
        j = db.get(m.Job, jobid)
        costs.settle(
            db, j, {"model": "qwen3.7-flash", "usage": {"prompt_tokens": -1, "completion_tokens": 10}}
        )
    assert state(env, budget)["held_micros"] == maximum
    with transaction(env.sf) as db:
        costs.settle(
            db,
            db.get(m.Job, jobid),
            {"model": "qwen3.7-flash", "usage": {"prompt_tokens": 2_000_000, "completion_tokens": 4000}},
        )
    assert state(env, budget)["blocked_by_overrun"]
    a, _ = authorize(env, budget)
    with pytest.raises(AppError) as e:
        submit(env, budget, a)
    assert e.value.code == "COST_RECONCILIATION_REQUIRED"


def test_legacy_permission_owner_mode_and_expired_price(env, budget, monkeypatch):
    a, _ = authorize(env, budget)
    with transaction(env.sf) as db:
        db.delete(db.scalar(select(m.CostQuote)))
    with pytest.raises(AppError) as e:
        submit(env, budget, a)
    assert e.value.code == "COST_QUOTE_REQUIRED"
    a, _ = authorize(env, budget)
    submit(env, budget, a)
    with env.sf() as db:
        assert costs.summary(db, budget[0], "other")["held_micros"] == 0
        assert costs.summary(db, env.settings, env.owner_id)["held_micros"] == 0
    budget[0].price_valid_until = date(2000, 1, 1)
    with env.sf() as db:
        with pytest.raises(AppError) as e:
            costs.quote(db, budget[0], env.owner_id, "writing", budget[1])
        assert e.value.code == "PRICE_EXPIRED"
    assert env.client.get("/api/v1/cost-budget").status_code == 200
    env.client.cookies.clear()
    assert env.client.get("/api/v1/cost-budget").status_code == 401


def test_populated_budget_migration_preserves_rows(env, task_data, pre_ranking_schema):
    import os
    import subprocess
    import sys

    from sqlalchemy import inspect, text

    from backend.app.core.config import ROOT

    engine = env.sf.kw["bind"]
    m.CostLedger.__table__.drop(engine)
    m.CostQuote.__table__.drop(engine)
    names = inspect(engine).get_table_names()

    def rows():
        with engine.connect() as db:
            return {
                name: sorted([tuple(r) for r in db.execute(text('SELECT * FROM "' + name + '"'))], key=repr)
                for name in names
            }

    before = rows()
    environment = {
        **os.environ,
        "DATABASE_URL": env.settings.database_url,
        "DATA_DIR": str(env.settings.data_dir),
    }
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini"]
    for arguments in [["stamp", "0004"], ["upgrade", "head"], ["upgrade", "head"]]:
        subprocess.run([*command, *arguments], cwd=ROOT, env=environment, capture_output=True, check=True)
    assert before == rows()
    with engine.connect() as db:
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
