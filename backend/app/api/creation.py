"""S2 private endpoints share authentication, idempotency and transactions with S1."""

from fastapi import Request
from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.core.errors import AppError
from backend.app.services import platform_rules
from backend.app.services.common import iso, owned
from backend.app.services.creation import (
    check_revision,
    context_for,
    edit_text,
    text_owned,
    transition,
    version_view,
)
from backend.app.services.discovery import discovery_payload
from backend.app.services.jobs import canonical_input, enqueue, receipt, validate_inputs
from backend.app.services.providers import Providers
from backend.app.services.text_history import cleanup_history, cleanup_preview
from backend.app.services.views import task_view


def install(app, settings, read, mutate, ok):
    mode = settings.provider_mode

    @app.get("/api/v1/platform-rules/douyin")
    def platform_policy(request: Request):
        with read(request):
            return ok(request, platform_rules.describe())

    @app.post("/api/v1/tasks/{id}/generations")
    def generate(id: str, request: Request, body: s.GenerationIn):
        def run(db, owner):
            if id != body.task_id:
                raise AppError("TASK_MISMATCH", "任务标识不一致。", 422)
            task = owned(db, m.Task, id, owner, mode)
            check_revision(task, body.expected_revision)
            inputs = canonical_input("generation", body.model_dump())
            validate_inputs(db, settings, owner, "generation", inputs)
            Providers(settings).check("generation")
            job = enqueue(db, settings, owner, "generation", inputs, body.cost_authorization_id)
            db.add(m.Generation(owner_id=owner, data_mode=mode, task_id=id, job_id=job.id))
            return receipt(job), 202

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/tasks/{id}/texts")
    def texts(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            versions = db.scalars(
                select(m.TextVersion)
                .where(m.TextVersion.task_id == id)
                .order_by(m.TextVersion.created_at.desc())
            )
            groups = db.scalars(
                select(m.Generation)
                .where(m.Generation.task_id == id)
                .order_by(m.Generation.created_at.desc())
            )
            return ok(
                request,
                {
                    "task": task_view(task, db, settings),
                    "template_intro": "每天分享好用的工具，今天讲"
                    + context_for(db, task)["tool_name"]
                    + "。",
                    "versions": [version_view(v, db) for v in versions],
                    "platform_rules": platform_rules.describe(),
                    "groups": [{"id": g.id, "job_id": g.job_id, "output": g.output} for g in groups],
                },
            )

    @app.post("/api/v1/tasks/{id}/texts")
    def edit(id: str, request: Request, body: s.TextEditIn):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            v, changed = edit_text(db, task, body)
            return {
                "version": version_view(v, db),
                "task": task_view(task, db, settings),
                "changed": changed,
            }, 201 if changed else 200

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/tasks/{id}/texts/cleanup-preview")
    def preview_cleanup(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            return ok(request, cleanup_preview(db, task))

    @app.post("/api/v1/tasks/{id}/texts/cleanup")
    def clean_history(id: str, request: Request, body: s.TextCleanupIn):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            return cleanup_history(db, task, body), 200

        return mutate(request, body.model_dump(), run)

    def move(id, version_id, request, body, confirm):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            version = text_owned(db, version_id, task)
            transition(db, task, version, body.expected_revision, confirm)
            return {"version": version_view(version, db), "task": task_view(task, db, settings)}, 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/tasks/{id}/texts/{version_id}/adopt")
    def adopt(id: str, version_id: str, request: Request, body: s.RevisionIn):
        return move(id, version_id, request, body, False)

    @app.post("/api/v1/tasks/{id}/texts/{version_id}/confirm")
    def confirm(id: str, version_id: str, request: Request, body: s.TextConfirmIn):
        return move(id, version_id, request, body, True)

    @app.post("/api/v1/discoveries")
    def discover(request: Request, body: s.DiscoveryIn):
        def run(db, owner):
            batch = m.Discovery(
                owner_id=owner,
                data_mode=mode,
                query=body.topic,
                payload={"input": body.model_dump(), "status": "queued", "candidates": []},
            )
            db.add(batch)
            db.flush()
            job = enqueue(
                db, settings, owner, "discovery_search", {"discovery_id": batch.id, **body.model_dump()}
            )
            return {**receipt(job), "discovery_id": batch.id}, 202

        return mutate(request, body.model_dump(), run)

    def discovery_view(db, d):
        payload = discovery_payload(d.payload, d.created_at)
        if payload.get("brief_job_id"):
            job = db.get(m.Job, payload["brief_job_id"])
            if job and job.owner_id == d.owner_id and job.data_mode == d.data_mode:
                payload["brief_job"] = {"id": job.id, "status": job.status, "error": job.error}
        return {"id": d.id, "query": d.query, "created_at": iso(d.created_at), **payload}

    @app.get("/api/v1/discoveries")
    def discoveries(request: Request):
        with read(request) as (db, owner, _):
            rows = db.scalars(
                select(m.Discovery)
                .where(m.Discovery.owner_id == owner, m.Discovery.data_mode == mode)
                .order_by(m.Discovery.created_at.desc())
                .limit(30)
            )
            return ok(
                request,
                {
                    "items": [discovery_view(db, d) for d in rows],
                    "next_cursor": None,
                },
            )

    @app.get("/api/v1/discoveries/{id}")
    def discovery(id: str, request: Request):
        with read(request) as (db, owner, _):
            d = owned(db, m.Discovery, id, owner, mode)
            return ok(request, discovery_view(db, d))

    @app.post("/api/v1/discoveries/{id}/brief")
    def brief(id: str, request: Request, body: s.ScoreIn):
        def run(db, owner):
            if body.discovery_id != id:
                raise AppError("DISCOVERY_MISMATCH", "查询标识不一致。", 422)
            inputs = canonical_input("discovery_brief", body.model_dump())
            validate_inputs(db, settings, owner, "discovery_brief", inputs)
            Providers(settings).check("discovery_brief")
            active = db.scalars(
                select(m.Job).where(
                    m.Job.owner_id == owner,
                    m.Job.data_mode == mode,
                    m.Job.kind == "discovery_brief",
                    m.Job.status.in_(["queued", "running", "needs_reconciliation"]),
                )
            )
            if any(j.input_json.get("discovery_id") == id for j in active):
                raise AppError("DISCOVERY_BRIEF_IN_PROGRESS", "已有中文介绍作业，请查看原作业。")
            job = enqueue(db, settings, owner, "discovery_brief", inputs, body.cost_authorization_id)
            batch = owned(db, m.Discovery, id, owner, mode)
            batch.payload = {**batch.payload, "brief_job_id": job.id}
            return receipt(job), 202

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/discoveries/{id}/score")
    def score(id: str, request: Request, body: s.ScoreIn):
        def run(db, owner):
            if body.discovery_id != id:
                raise AppError("DISCOVERY_MISMATCH", "查询标识不一致。", 422)
            inputs = canonical_input("discovery_score", body.model_dump())
            validate_inputs(db, settings, owner, "discovery_score", inputs)
            Providers(settings).check("discovery_score")
            active = db.scalars(
                select(m.Job).where(
                    m.Job.owner_id == owner,
                    m.Job.data_mode == mode,
                    m.Job.kind == "discovery_score",
                    m.Job.status.in_(["queued", "running", "needs_reconciliation"]),
                )
            )
            if any(j.input_json.get("discovery_id") == id for j in active):
                raise AppError("DISCOVERY_SCORE_IN_PROGRESS", "本次候选已有评分作业，请查看原作业。")
            return receipt(
                enqueue(db, settings, owner, "discovery_score", inputs, body.cost_authorization_id)
            ), 202

        return mutate(request, body.model_dump(), run)
