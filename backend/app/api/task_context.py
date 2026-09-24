"""Source adjustments never invoke a provider or publish catalog content."""

from fastapi import Query, Request
from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.services.common import iso, owned
from backend.app.services.task_context import change, view
from backend.app.services.views import task_view


def install(app, settings, read, mutate, ok):
    mode = settings.provider_mode

    def source(analysis, snapshot):
        return {
            "id": analysis.id,
            "snapshot_id": snapshot.id,
            "created_at": iso(analysis.created_at),
            "source_type": "repository",
            "analysis": analysis.payload,
            "snapshot": snapshot.payload,
        }

    @app.get("/api/v1/tasks/{id}/context")
    def context(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            current = view(db, task)
            analysis = db.get(m.Analysis, current["analysis_id"])
            return ok(
                request,
                {"current": current, "source": source(analysis, db.get(m.Snapshot, task.snapshot_id))},
            )

    @app.get("/api/v1/tasks/{id}/context/sources")
    def sources(
        id: str, request: Request, cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50)
    ):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            q = (
                select(m.Analysis, m.Snapshot)
                .join(m.Snapshot, m.Analysis.snapshot_id == m.Snapshot.id)
                .where(
                    m.Snapshot.tool_id == task.tool_id,
                    m.Snapshot.owner_id == owner,
                    m.Snapshot.data_mode == mode,
                    m.Analysis.owner_id == owner,
                    m.Analysis.data_mode == mode,
                )
            )
            if cursor:
                q = q.where(m.Analysis.id > cursor)
            rows = list(db.execute(q.order_by(m.Analysis.id).limit(limit + 1)))
            return ok(
                request,
                {
                    "items": [source(a, snap) for a, snap in rows[:limit]],
                    "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None,
                },
            )

    @app.get("/api/v1/tasks/{id}/context/history")
    def history(
        id: str, request: Request, before: int | None = None, limit: int = Query(default=20, ge=1, le=50)
    ):
        with read(request) as (db, owner, _):
            owned(db, m.Task, id, owner, mode)
            q = select(m.TaskContextVersion).where(m.TaskContextVersion.task_id == id)
            if before is not None:
                q = q.where(m.TaskContextVersion.revision < before)
            rows = list(db.scalars(q.order_by(m.TaskContextVersion.revision.desc()).limit(limit + 1)))
            return ok(
                request,
                {
                    "items": [
                        {"id": r.id, "revision": r.revision, "created_at": iso(r.created_at), **r.payload}
                        for r in rows[:limit]
                    ],
                    "next_before": rows[limit - 1].revision if len(rows) > limit else None,
                },
            )

    @app.patch("/api/v1/tasks/{id}/context")
    def update_context(id: str, request: Request, body: s.TaskContextIn):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            changed = change(db, task, body)
            return {"changed": changed, "current": view(db, task), "task": task_view(task, db, settings)}, 200

        return mutate(request, body.model_dump(mode="json"), run)
