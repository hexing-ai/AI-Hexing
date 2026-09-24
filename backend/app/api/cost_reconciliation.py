"""Private bill reconciliation endpoints with shared auth and serialized transactions."""

from typing import Literal

from fastapi import Query, Request
from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.services import cost_reconciliation as billing


def install(app, settings, read, mutate, ok):
    mode = settings.provider_mode

    @app.get("/api/v1/cost-records")
    def records(
        request: Request,
        state: Literal["pending", "reconciled", "all"] = "pending",
        cursor: str | None = None,
        limit: int = Query(default=20, ge=1, le=50),
    ):
        with read(request) as (db, owner, _):
            return ok(request, billing.list_records(db, owner, mode, state, cursor, limit))

    @app.get("/api/v1/cost-records/{id}")
    def record(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, billing.detail(db, billing.cost_job(db, id, owner, mode)))

    @app.get("/api/v1/cost-records/{id}/history")
    def history(
        id: str, request: Request, before: int | None = None, limit: int = Query(default=20, ge=1, le=50)
    ):
        with read(request) as (db, owner, _):
            billing.cost_job(db, id, owner, mode)
            q = select(m.CostReconciliation).where(m.CostReconciliation.job_id == id)
            if before is not None:
                q = q.where(m.CostReconciliation.revision < before)
            rows = list(db.scalars(q.order_by(m.CostReconciliation.revision.desc()).limit(limit + 1)))
            return ok(
                request,
                {
                    "items": [billing.record(r) for r in rows[:limit]],
                    "next_before": rows[limit - 1].revision if len(rows) > limit else None,
                },
            )

    @app.post("/api/v1/cost-records/{id}/preview")
    def preview(id: str, request: Request, body: s.CostReconcileIn):
        def run(db, owner):
            return billing.preview(db, settings, billing.cost_job(db, id, owner, mode), body), 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/cost-records/{id}/reconciliations")
    def reconcile(id: str, request: Request, body: s.CostReconcileConfirmIn):
        def run(db, owner):
            return billing.reconcile(db, settings, billing.cost_job(db, id, owner, mode), body), 201

        return mutate(request, body.model_dump(), run)
