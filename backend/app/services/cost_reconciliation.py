"""Human invoice reconciliation overlays; never rewrites a provider result or a raw ledger."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import digest, iso, owned

CHARGEABLE = {
    "analysis",
    "writing",
    "generation",
    "discovery_score",
    "discovery_brief",
    "voice_enrollment",
    "voice_preview",
    "voice_reuse",
    "audio_generation",
}
LABELS = {
    "billed": "已按账单核对",
    "not_charged": "已证实未收费",
    "reopened": "已撤回核对 · 保守占用",
    "directory": "目录价核算 · 待账单核对",
    "held": "预占/未知 · 待账单核对",
    "released": "未发送 · 已释放预占",
    "legacy_unknown": "历史费用未知",
    "not_sent": "未发送",
}


def latest(db, job_id):
    return db.scalar(
        select(m.CostReconciliation)
        .where(m.CostReconciliation.job_id == job_id)
        .order_by(m.CostReconciliation.revision.desc())
        .limit(1)
    )


def latest_map(db, owner, mode):
    newest = (
        select(m.CostReconciliation.job_id, func.max(m.CostReconciliation.revision).label("rev"))
        .where(m.CostReconciliation.owner_id == owner, m.CostReconciliation.data_mode == mode)
        .group_by(m.CostReconciliation.job_id)
        .subquery()
    )
    return {
        r.job_id: record(r)
        for r in db.scalars(
            select(m.CostReconciliation).join(
                newest,
                and_(
                    m.CostReconciliation.job_id == newest.c.job_id,
                    m.CostReconciliation.revision == newest.c.rev,
                ),
            )
        )
    }


def record(r):
    if r is None:
        return None
    return {
        "id": r.id,
        "revision": r.revision,
        "created_at": iso(r.created_at),
        "previous_id": r.previous_id,
        "resolution": r.resolution,
        "actual_micros": r.actual_micros,
        "hold_micros": r.hold_micros,
        "budget_month": r.budget_month,
        **r.payload,
    }


def attempts_for(db, job):
    return list(
        db.scalars(select(m.Attempt).where(m.Attempt.job_id == job.id).order_by(m.Attempt.attempt_no))
    )


def usage_month(job, ledger, attempts):
    if ledger:
        return ledger.month
    sent = [a for a in attempts if a.dispatch_state != "not_sent"]
    return datetime.fromtimestamp(
        sent[0].created_at if sent else job.created_at, ZoneInfo("Asia/Shanghai")
    ).strftime("%Y-%m")


def effect(job, ledger, attempts, reconciliation):
    sent = any(a.dispatch_state != "not_sent" for a in attempts)
    contribution = {
        "budget_month": usage_month(job, ledger, attempts),
        "actual_micros": None,
        "priced_micros": 0,
        "held_micros": 0,
        "overrun": False,
        "historical_unknown": False,
        "pending": False,
        "awaiting_bill": sent,
        "status": "not_sent",
    }
    if ledger:
        contribution["status"] = (
            "released"
            if ledger.status == "released"
            else "directory"
            if ledger.status == "priced"
            else "held"
        )
        if ledger.status == "priced":
            contribution["priced_micros"] = ledger.priced_micros or 0
        if ledger.status in ("reserved", "unknown", "overrun"):
            contribution["held_micros"] = max(ledger.reserved_micros, ledger.priced_micros or 0)
        contribution["overrun"] = ledger.status == "overrun"
        contribution["pending"] = ledger.status in ("unknown", "overrun")
    elif sent:
        contribution.update(status="legacy_unknown", historical_unknown=True)
    if reconciliation:
        contribution.update(
            status=reconciliation["resolution"], budget_month=reconciliation["budget_month"], priced_micros=0
        )
        if reconciliation["resolution"] == "reopened":
            contribution.update(
                held_micros=max(
                    reconciliation["hold_micros"],
                    ledger.reserved_micros if ledger else 0,
                    (ledger.priced_micros or 0) if ledger else 0,
                ),
                pending=True,
                awaiting_bill=True,
            )
        else:
            contribution.update(
                actual_micros=reconciliation["actual_micros"],
                held_micros=0,
                overrun=False,
                pending=False,
                historical_unknown=False,
                awaiting_bill=False,
            )
    return contribution


def raw_snapshot(job, ledger, attempts):
    return {
        "job_id": job.id,
        "job_status": job.status,
        "job_updated_at": job.updated_at,
        "ledger": {
            "id": ledger.id,
            "status": ledger.status,
            "month": ledger.month,
            "reserved_micros": ledger.reserved_micros,
            "priced_micros": ledger.priced_micros,
            "updated_at": ledger.updated_at,
        }
        if ledger
        else None,
        "attempts": [
            {
                "number": a.attempt_no,
                "dispatch_state": a.dispatch_state,
                "request_id": a.provider_request_id,
                "created_at": iso(a.created_at),
                "usage": a.usage,
                "model": a.settings_summary.get("model"),
                "cost_status": a.cost_status,
            }
            for a in attempts
        ],
    }


def cost_job(db, id, owner, mode):
    job = owned(db, m.Job, id, owner, mode)
    if job.kind not in CHARGEABLE:
        raise AppError("COST_JOB_INVALID", "这不是模型费用记录。", 422)
    return job


def detail(db, job):
    from backend.app.services.costs import ledger_for

    ledger, attempts, recent = ledger_for(db, job), attempts_for(db, job), record(latest(db, job.id))
    effective = effect(job, ledger, attempts, recent)
    quote = db.get(m.CostQuote, ledger.quote_id) if ledger else None
    return {
        "id": job.id,
        "kind": job.kind,
        "created_at": iso(job.created_at),
        "mode": job.data_mode,
        "revision": recent["revision"] if recent else 0,
        "can_reconcile": job.status not in ("queued", "running"),
        "effective": effective,
        "status_label": LABELS[effective["status"]],
        "latest": recent,
        "raw": raw_snapshot(job, ledger, attempts),
        "quote": {k: quote.payload.get(k) for k in ("model", "price_version", "source", "maximum_micros")}
        if quote
        else None,
    }


def list_records(db, owner, mode, state, cursor, limit):
    sent = exists(
        select(m.Attempt.id).where(m.Attempt.job_id == m.Job.id, m.Attempt.dispatch_state != "not_sent")
    )
    has_ledger = exists(select(m.CostLedger.id).where(m.CostLedger.job_id == m.Job.id))
    # Latest resolution for this job, independent of the number of corrections.
    resolution = (
        select(m.CostReconciliation.resolution)
        .where(m.CostReconciliation.job_id == m.Job.id)
        .order_by(m.CostReconciliation.revision.desc())
        .limit(1)
        .correlate(m.Job)
        .scalar_subquery()
    )
    q = select(m.Job).where(
        m.Job.owner_id == owner,
        m.Job.data_mode == mode,
        m.Job.kind.in_(CHARGEABLE),
        or_(sent, has_ledger, resolution.is_not(None)),
    )
    if state == "pending":
        q = q.where(or_(and_(sent, resolution.is_(None)), resolution == "reopened"))
    if state == "reconciled":
        q = q.where(resolution.in_(["billed", "not_charged"]))
    if cursor:
        anchor = cost_job(db, cursor, owner, mode)
        q = q.where(
            or_(
                m.Job.created_at < anchor.created_at,
                and_(m.Job.created_at == anchor.created_at, m.Job.id < anchor.id),
            )
        )
    rows = list(db.scalars(q.order_by(m.Job.created_at.desc(), m.Job.id.desc()).limit(limit + 1)))
    items = []
    for job in rows[:limit]:
        d = detail(db, job)
        items.append(
            {
                "directory_amount_micros": (d["raw"]["ledger"] or {}).get("priced_micros"),
                **{
                    k: d[k]
                    for k in (
                        "id",
                        "kind",
                        "created_at",
                        "mode",
                        "revision",
                        "can_reconcile",
                        "effective",
                        "status_label",
                    )
                },
            }
        )
    return {"items": items, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


def preview(db, settings, job, body):
    from backend.app.services import costs

    info = detail(db, job)
    if not info["can_reconcile"]:
        raise AppError("COST_JOB_ACTIVE", "作业仍在排队或处理中，请结束后再核对账单。")
    if info["revision"] != body.expected_revision:
        raise AppError("COST_REVISION_CONFLICT", "费用核对已有新版本，请读取当前记录后重新预览。")
    prior = info["latest"]
    proposal = {
        "resolution": body.resolution,
        "actual_micros": None,
        "hold_micros": 0,
        "budget_month": info["effective"]["budget_month"],
        "billing_month": body.billing_month,
        "evidence_type": body.evidence_type,
        "evidence_reference": body.evidence_reference,
        "match_details": body.match_details,
        "reason": body.reason,
        "evidence_hash": None,
    }
    if body.resolution == "reopened":
        if not prior or prior["resolution"] == "reopened":
            raise AppError("COST_REOPEN_INVALID", "只有已核实的账单记录可以撤回核对。")
        if any(
            (
                body.actual_amount is not None,
                body.billing_month,
                body.evidence_type,
                body.evidence_reference,
                body.match_details,
            )
        ):
            raise AppError("COST_REOPEN_FIELDS", "撤回只填写原因，不提交新的账单金额或凭据。", 422)
        raw = info["raw"]["ledger"] or {}
        proposal["hold_micros"] = max(
            raw.get("reserved_micros", 0),
            raw.get("priced_micros") or 0,
            prior["actual_micros"] or 0,
            prior["hold_micros"],
        )
    else:
        if (
            body.actual_amount is None
            or not body.billing_month
            or not body.evidence_type
            or not body.evidence_reference
            or len(body.match_details) < 5
        ):
            raise AppError(
                "COST_EVIDENCE_REQUIRED",
                "请填写实际净额、账单月份、具体明细/工单定位及本作业全部调用的匹配依据。",
                422,
            )
        actual = int(Decimal(body.actual_amount) * 1_000_000)
        if actual > 1_000_000_000_000 or (actual == 0) != (body.resolution == "not_charged"):
            raise AppError(
                "COST_AMOUNT_INVALID",
                "金额需在范围内；零费用请明确选择已证实未收费，收费金额必须大于零。",
                422,
            )
        if body.billing_month > costs.month():
            raise AppError("COST_MONTH_INVALID", "不能使用未来月份的账单。", 422)
        if actual > 0 and not any(a["dispatch_state"] != "not_sent" for a in info["raw"]["attempts"]):
            raise AppError("COST_NOT_SENT", "本作业没有发送记录，不能直接记为已收费；请先核对所选作业。", 422)
        evidence_hash = digest([body.evidence_type, " ".join(body.evidence_reference.casefold().split())])
        if db.scalar(
            select(m.CostReconciliation.id).where(
                m.CostReconciliation.owner_id == job.owner_id,
                m.CostReconciliation.data_mode == job.data_mode,
                m.CostReconciliation.evidence_hash == evidence_hash,
                m.CostReconciliation.job_id != job.id,
            )
        ):
            raise AppError(
                "COST_EVIDENCE_DUPLICATE", "这条账单明细已用于其他作业，请核对明细定位，不能重复记账。"
            )
        proposal.update(actual_micros=actual, evidence_hash=evidence_hash)
    before = costs.summary(db, settings, job.owner_id)
    after = costs.summary(db, settings, job.owner_id, overrides={job.id: proposal})
    token = digest({"raw": info["raw"], "prior": prior, "proposal": proposal, "budget": before})
    return {
        "preview_token": token,
        "expected_revision": info["revision"],
        "proposal": proposal,
        "raw": info["raw"],
        "previous": prior,
        "budget_before": before,
        "budget_after": after,
        "notice": "按本人提供的账单依据记账，不代表供应商结果已恢复；不会调用模型。",
    }


def reconcile(db, settings, job, body):
    result = preview(db, settings, job, body)
    if result["preview_token"] != body.preview_token:
        raise AppError("COST_PREVIEW_CHANGED", "作业、用量或预算已变化，请重新预览后确认。")
    p = result["proposal"]
    row = m.CostReconciliation(
        owner_id=job.owner_id,
        data_mode=job.data_mode,
        job_id=job.id,
        revision=body.expected_revision + 1,
        previous_id=(result["previous"] or {}).get("id"),
        resolution=p["resolution"],
        actual_micros=p["actual_micros"],
        hold_micros=p["hold_micros"],
        budget_month=p["budget_month"],
        evidence_hash=p["evidence_hash"],
        payload={
            **{
                k: p[k]
                for k in ("billing_month", "evidence_type", "evidence_reference", "match_details", "reason")
            },
            "before": result["raw"],
            "budget_before": result["budget_before"],
            "budget_after": result["budget_after"],
        },
    )
    db.add(row)
    db.flush()
    db.add(
        m.EvidenceEvent(
            owner_id=job.owner_id,
            data_mode=job.data_mode,
            event_type="cost_reconciled",
            object_id=job.id,
            details={"reconciliation_id": row.id, "revision": row.revision, "resolution": row.resolution},
        )
    )
    return {"record": detail(db, job), "budget": result["budget_after"]}
