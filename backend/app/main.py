import hashlib
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from starlette.exceptions import HTTPException

from backend.app import models as m
from backend.app import schemas as s
from backend.app.api.window_login import register_window_login
from backend.app.core.auth import authenticate, client_address, login
from backend.app.core.config import get_settings
from backend.app.core.db import factory, transaction
from backend.app.core.errors import AppError, error_dict
from backend.app.services import costs
from backend.app.services import task_lifecycle as life
from backend.app.services.activity import activity_view, list_jobs
from backend.app.services.assets import asset_path, delete_asset_file, save_asset, validate_sample_url_expiry
from backend.app.services.common import digest, iso, normalize_repository, owned
from backend.app.services.jobs import canonical_input, create_validation, enqueue, receipt, validate_inputs
from backend.app.services.providers import Providers
from backend.app.services.views import asset_view, job_view, task_view, validation_view, voice_view
from backend.app.services.voice_library import clear_default


def create_app(settings=None, session_factory=None):
    settings = settings or get_settings()
    sf = session_factory or factory(settings)
    app = FastAPI(title="AI-Hexing", version="0.3.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.sf = settings, sf

    @app.middleware("http")
    async def boundaries(request, call_next):
        request.state.request_id = m.uid()
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("hexing").error("request_failed request_id=%s", request.state.request_id)
            response = JSONResponse(
                {
                    "error": error_dict(
                        AppError("INTERNAL_ERROR", "操作失败，请提供请求编号以便排查。", 500),
                        request.state.request_id,
                    )
                },
                status_code=500,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(AppError)
    async def known_error(request, e):
        return JSONResponse({"error": error_dict(e, request.state.request_id)}, status_code=e.status)

    @app.exception_handler(HTTPException)
    async def http_error(request, e):
        error = AppError("HTTP_" + str(e.status_code), "请求地址或方法不可用。", e.status_code)
        return JSONResponse({"error": error_dict(error, request.state.request_id)}, status_code=e.status_code)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid(request, e):
        return JSONResponse(
            {
                "error": error_dict(
                    AppError("INPUT_INVALID", "输入格式不正确，请检查必填项。", 422), request.state.request_id
                )
            },
            status_code=422,
        )

    def ok(request, data, status=200):
        return JSONResponse({"data": data, "request_id": request.state.request_id}, status_code=status)

    register_window_login(app, settings, sf, ok)

    @contextmanager
    def read(request):
        with sf() as db:
            owner, session = authenticate(db, request, settings)
            yield db, owner.id, session

    def mutate(request, body, fn):
        with transaction(sf) as db:
            owner, _ = authenticate(db, request, settings, write=True)
            db.info["task_write"] = True
            life.guard_mutation(db, request, body, owner.id, settings.provider_mode)
            key = request.headers.get("idempotency-key", "")
            if not 8 <= len(key) <= 100:
                raise AppError("IDEMPOTENCY_KEY_REQUIRED", "缺少有效操作标识，请刷新页面。", 422)
            scope = request.method + ":" + request.url.path
            h = digest(body)
            op = db.scalar(
                select(m.Operation).where(
                    m.Operation.owner_id == owner.id,
                    m.Operation.data_mode == settings.provider_mode,
                    m.Operation.scope == scope,
                    m.Operation.key == key,
                )
            )
            if op:
                if op.request_hash != h:
                    raise AppError("IDEMPOTENCY_CONFLICT", "同一操作标识不能用于不同内容。")
                if op.status_code == 410:
                    raise AppError("TASK_PURGED", "原任务已清理，该操作不能重新执行。", 410)
                if scope == "POST:/api/v1/tasks" and op.response.get("id"):
                    life.check_task(db, op.response["id"], write=True)
                return ok(request, op.response, op.status_code)
            data, status = fn(db, owner.id)
            db.add(
                m.Operation(
                    owner_id=owner.id,
                    data_mode=settings.provider_mode,
                    scope=scope,
                    key=key,
                    request_hash=h,
                    response=data,
                    status_code=status,
                )
            )
            return ok(request, data, status)

    def validation_enabled():
        if not settings.enable_validation_tools or settings.app_env not in ("development", "test"):
            raise AppError("VALIDATION_DISABLED", "实验工具仅在开发环境开放。", 403)

    @app.get("/api/v1/health")
    def health(request: Request):
        return ok(request, {"status": "ok"})

    @app.get("/api/v1/openapi.json")
    def private_openapi(request: Request):
        with read(request):
            pass
        return app.openapi()

    @app.post("/api/v1/auth/login")
    def sign_in(request: Request, body: s.LoginIn):
        if request.headers.get("origin") != settings.web_origin:
            raise AppError("ORIGIN_INVALID", "登录来源不受信任。", 403)
        with transaction(sf) as db:
            creator, token, csrf = login(
                db, settings, body.login_name, body.password, client_address(request, settings)
            )
            response = ok(request, {"creator_id": creator.id, "csrf_token": csrf})
            response.set_cookie(
                "hexing_session",
                token,
                httponly=True,
                secure=settings.app_env == "production",
                samesite="strict",
                max_age=settings.session_ttl_seconds,
                path="/",
            )
            response.set_cookie(
                "hexing_csrf",
                csrf,
                httponly=True,
                secure=settings.app_env == "production",
                samesite="strict",
                max_age=settings.session_ttl_seconds,
                path="/",
            )
            return response

    @app.get("/api/v1/auth/me")
    def me(request: Request):
        with read(request) as (_, owner, session):
            csrf = request.cookies.get("hexing_csrf", "")
            if digest(csrf) != session.csrf_hash:
                raise AppError("SESSION_INVALID", "请重新登录以恢复会话。", 401)
            return ok(request, {"creator_id": owner, "csrf_token": csrf})

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        with transaction(sf) as db:
            _, session = authenticate(db, request, settings, write=True)
            session.revoked = True
        r = Response(status_code=204)
        r.delete_cookie("hexing_session")
        r.delete_cookie("hexing_csrf")
        return r

    @app.get("/api/v1/capabilities")
    def capabilities(request: Request):
        with read(request):
            p = Providers(settings)
            return ok(
                request,
                {
                    "mode": settings.provider_mode,
                    "contracts": [
                        {
                            "kind": k,
                            "state": "blocked"
                            if p.readiness(k)
                            else ("mock" if settings.provider_mode == "mock" else "configured_unverified"),
                            "missing_config_names": p.readiness(k),
                        }
                        for k in ["import", "analysis", "writing", "voice_enrollment", "voice_preview"]
                    ],
                    "validation_tools_enabled": settings.enable_validation_tools
                    and settings.app_env in ("development", "test"),
                    "contract_version": "S1-2026-09-08",
                    "upload": {
                        "format": "16-bit PCM WAV",
                        "max_bytes": 10 * 1024 * 1024,
                        "min_seconds": 5,
                        "max_seconds": 60,
                        "min_sample_rate": 16000,
                    },
                    "notice_version": "S1-2026-09-08",
                },
            )

    @app.post("/api/v1/imports")
    def imports(request: Request, body: s.ImportIn):
        url = normalize_repository(body.repository_url)

        def run(db, owner):
            Providers(settings).check("import")
            return receipt(enqueue(db, settings, owner, "import", {"repository_url": url})), 202

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/tools/{id}/snapshots")
    def refresh(id: str, request: Request):
        def run(db, owner):
            tool = owned(db, m.Tool, id, owner, settings.provider_mode)
            Providers(settings).check("import")
            return receipt(
                enqueue(
                    db, settings, owner, "import", {"repository_url": tool.canonical_url, "refresh": True}
                )
            ), 202

        return mutate(request, {}, run)

    @app.get("/api/v1/snapshots/{id}")
    def snapshot(id: str, request: Request):
        with read(request) as (db, owner, _):
            snap = owned(db, m.Snapshot, id, owner, settings.provider_mode)
            return ok(
                request,
                {
                    "id": snap.id,
                    "tool_id": snap.tool_id,
                    "source_sha": snap.source_sha,
                    "fetched_at": iso(snap.created_at),
                    "data_mode": snap.data_mode,
                    **snap.payload,
                },
            )

    @app.post("/api/v1/snapshots/{id}/analyses")
    def analyse(id: str, request: Request, body: s.AnalysisIn):
        inputs = canonical_input("analysis", {"snapshot_id": id, **body.model_dump()})

        def run(db, owner):
            validate_inputs(db, settings, owner, "analysis", inputs)
            previous = db.scalar(
                select(m.Analysis)
                .where(
                    m.Analysis.owner_id == owner,
                    m.Analysis.data_mode == settings.provider_mode,
                    m.Analysis.context_hash == digest(inputs),
                )
                .order_by(m.Analysis.created_at.desc())
            )
            if previous:
                return {"id": previous.id, "snapshot_id": previous.snapshot_id, **previous.payload}, 200
            Providers(settings).check("analysis")
            return receipt(enqueue(db, settings, owner, "analysis", inputs, body.cost_authorization_id)), 202

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/analyses/{id}")
    def analysis(id: str, request: Request):
        with read(request) as (db, owner, _):
            a = owned(db, m.Analysis, id, owner, settings.provider_mode)
            return ok(request, {"id": a.id, "snapshot_id": a.snapshot_id, **a.payload})

    @app.post("/api/v1/analyses/{id}/reviews")
    def review(id: str, request: Request, body: s.ReviewIn):
        def run(db, owner):
            a = owned(db, m.Analysis, id, owner, settings.provider_mode)
            valid = {h["id"] for h in a.payload["highlights"] if h["evidence_refs"]}
            if body.primary_highlight_id not in valid or (
                body.secondary_highlight_id
                and (
                    body.secondary_highlight_id not in valid
                    or body.secondary_highlight_id == body.primary_highlight_id
                )
            ):
                raise AppError("HIGHLIGHT_INVALID", "请选择有证据且互不重复的亮点。", 422)
            row = m.Review(
                owner_id=owner,
                data_mode=settings.provider_mode,
                analysis_id=id,
                primary_highlight_id=body.primary_highlight_id,
                secondary_highlight_id=body.secondary_highlight_id,
            )
            db.add(row)
            db.flush()
            return {
                "id": row.id,
                "analysis_id": id,
                "primary_highlight_id": row.primary_highlight_id,
                "secondary_highlight_id": row.secondary_highlight_id,
                "confirmed_at": iso(row.created_at),
            }, 201

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/tasks")
    def create_task(request: Request, body: s.TaskIn):
        def run(db, owner):
            r = owned(db, m.Review, body.review_id, owner, settings.provider_mode)
            a = owned(db, m.Analysis, r.analysis_id, owner, settings.provider_mode)
            snap = owned(db, m.Snapshot, a.snapshot_id, owner, settings.provider_mode)
            if body.audience != a.payload["audience"]:
                raise AppError("AUDIENCE_CHANGED", "受众改变后需重新分析和复核。")
            t = m.Task(
                owner_id=owner,
                data_mode=settings.provider_mode,
                tool_id=snap.tool_id,
                snapshot_id=snap.id,
                review_id=r.id,
                audience=body.audience,
            )
            db.add(t)
            db.flush()
            return task_view(t, db, settings), 201

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/tasks")
    def tasks(
        request: Request,
        cursor: str | None = None,
        limit: int = Query(default=30, ge=1, le=100),
        lifecycle: Literal["active", "archived", "trashed"] = "active",
    ):
        with read(request) as (db, owner, _):
            q = (
                select(m.Task)
                .outerjoin(m.TaskLifecycle, m.TaskLifecycle.task_id == m.Task.id)
                .where(
                    m.Task.owner_id == owner,
                    m.Task.data_mode == settings.provider_mode,
                    func.coalesce(m.TaskLifecycle.state, "active") == lifecycle,
                )
            )
            if cursor:
                q = q.where(m.Task.id > cursor)
            rows = list(db.scalars(q.order_by(m.Task.id).limit(limit + 1)))
            return ok(
                request,
                {
                    "items": [task_view(t, db, settings) for t in rows[:limit]],
                    "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
                },
            )

    @app.get("/api/v1/tasks/{id}")
    def task(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, task_view(owned(db, m.Task, id, owner, settings.provider_mode), db, settings))

    @app.get("/api/v1/jobs")
    def jobs(
        request: Request,
        task_id: str | None = None,
        area: str | None = Query(default=None, pattern="^(discover|validation)$"),
        bucket: str | None = Query(default=None, pattern="^(attention|history)$"),
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=50),
    ):
        with read(request) as (db, owner, _):
            return ok(
                request,
                list_jobs(
                    db,
                    owner,
                    settings.provider_mode,
                    task_id=task_id,
                    area=area,
                    bucket=bucket,
                    cursor=cursor,
                    limit=limit,
                ),
            )

    @app.get("/api/v1/jobs/{id}")
    def job(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(
                request, activity_view(db, owned(db, m.Job, id, owner, settings.provider_mode), detail=True)
            )

    @app.post("/api/v1/jobs/{id}/cancel")
    def cancel(id: str, request: Request):
        def run(db, owner):
            j = owned(db, m.Job, id, owner, settings.provider_mode)
            if j.status not in ("queued", "running", "needs_reconciliation"):
                raise AppError("JOB_TERMINAL", "作业已结束。")
            costs.stop(db, j)
            j.status, j.updated_at = "cancelled", time.time()
            return job_view(j), 200

        return mutate(request, {}, run)

    @app.get("/api/v1/cost-budget")
    def cost_budget(request: Request):
        with read(request) as (db, owner, _):
            return ok(request, costs.summary(db, settings, owner))

    @app.post("/api/v1/cost-authorizations/preview")
    def cost_preview(request: Request, body: s.PreviewIn):
        inputs = canonical_input(body.action, body.input)

        def run(db, owner):
            validate_inputs(db, settings, owner, body.action, inputs)
            Providers(settings).check(body.action)
            quoted = (
                costs.quote(db, settings, owner, body.action, inputs)
                if settings.provider_mode == "live"
                else None
            )
            budget = costs.check_quote_limit(db, settings, owner, quoted) if quoted else None
            p = m.CostPreview(
                owner_id=owner,
                data_mode=settings.provider_mode,
                action=body.action,
                input_hash=digest(inputs),
                input_json=inputs,
                expires_at=time.time() + 600,
            )
            db.add(p)
            db.flush()
            if quoted:
                db.add(
                    m.CostQuote(
                        owner_id=owner, data_mode=settings.provider_mode, preview_id=p.id, payload=quoted
                    )
                )
            return {
                "maximum_cost": costs.money(quoted["maximum_micros"]) if quoted else None,
                "quote": quoted and {k: v for k, v in quoted.items() if not k.endswith("_hash")},
                "budget": budget,
                "preview_id": p.id,
                "input_hash": p.input_hash,
                "provider": "百炼",
                "model": settings.qwen_model
                if body.action in ("analysis", "writing", "generation", "discovery_score", "discovery_brief")
                else settings.voice_target_model,
                "data_categories": ["GitHub 简介与 README 原文"]
                if body.action == "discovery_brief"
                else ["已复核资料/文字"]
                if body.action in ("analysis", "writing", "generation", "discovery_score", "discovery_brief")
                else ["本人录音或合成文字/音色"],
                "estimated_cost": None,
                "cost_status": "quoted" if quoted else "unknown",
                "max_calls": 1,
                "expires_at": iso(p.expires_at),
            }, 201

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/cost-authorizations")
    def cost_authorize(request: Request, body: s.AuthorizationIn):
        def run(db, owner):
            p = owned(db, m.CostPreview, body.preview_id, owner, settings.provider_mode)
            if (
                p.expires_at <= time.time()
                or p.action != body.action
                or p.input_hash != body.input_hash
                or body.max_calls != 1
            ):
                raise AppError(
                    "COST_AUTHORIZATION_INVALID",
                    "费用许可已到期或与本次预览不一致。",
                )
            q = db.scalar(select(m.CostQuote).where(m.CostQuote.preview_id == p.id))
            if settings.provider_mode == "live":
                if (
                    not q
                    or body.max_cost is None
                    or body.accept_unknown_cost
                    or body.max_cost != q.payload["maximum_micros"] / 1_000_000
                ):
                    raise AppError("COST_QUOTE_REQUIRED", "请明确确认本次已核实的费用预占上限。")
                if costs.quote(db, settings, owner, p.action, p.input_json) != q.payload:
                    raise AppError("COST_QUOTE_CHANGED", "报价已变化，请重新预览。")
                costs.check_quote_limit(db, settings, owner, q.payload)
            existing = db.scalar(select(m.CostAuthorization).where(m.CostAuthorization.preview_id == p.id))
            if existing:
                raise AppError("PREVIEW_ALREADY_AUTHORIZED", "该预览已确认，请使用原费用许可。")
            a = m.CostAuthorization(
                owner_id=owner,
                data_mode=settings.provider_mode,
                preview_id=p.id,
                action=p.action,
                input_hash=p.input_hash,
                max_calls=1,
                expires_at=p.expires_at,
            )
            db.add(a)
            db.flush()
            return {
                "id": a.id,
                "action": a.action,
                "input_hash": a.input_hash,
                "max_calls": 1,
                "max_cost": costs.money(q.payload["maximum_micros"]) if q else None,
                "cost_status": "quoted" if q else "unknown",
                "expires_at": iso(a.expires_at),
            }, 201

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/assets")
    async def upload(
        request: Request,
        file: UploadFile = File(),
        kind: str = Form(),
        purpose: str = Form(),
        cleanup_after: str = Form(),
        consent: str = Form(),
    ):
        with sf() as db:
            authenticate(db, request, settings, write=True)
        validation_enabled()
        if kind != "voice_sample" or purpose != "validation":
            raise AppError("ASSET_KIND_INVALID", "本阶段仅接受实验录音样本。", 422)
        # Saving a private local sample does not contact a provider. Provider
        # readiness remains mandatory when submitting the actual enrollment.
        consent_body = s.ConsentIn.model_validate_json(consent)
        try:
            deadline = datetime.fromisoformat(cleanup_after.replace("Z", "+00:00")).timestamp()
            if not time.time() < deadline <= time.time() + 7 * 86400:
                raise ValueError()
        except ValueError:
            raise AppError("CLEANUP_TIME_INVALID", "请设置未来七天内的本次实验样本清理时间。", 422)
        if any(c in (file.filename or "") for c in ["/", "\\", "\x00"]) or ".." in (file.filename or ""):
            raise AppError("FILENAME_INVALID", "文件名不安全，请重命名后上传。", 422)
        data = await file.read(min(settings.max_upload_bytes, 10 * 1024 * 1024) + 1)
        if len(data) > min(settings.max_upload_bytes, 10 * 1024 * 1024):
            raise AppError("UPLOAD_TOO_LARGE", "录音文件不得超过 10 MB。", 413)

        def run(db, owner):
            event = m.EvidenceEvent(
                owner_id=owner,
                data_mode=settings.provider_mode,
                event_type="sample_consent",
                object_id=m.uid(),
                details=consent_body.model_dump(),
            )
            db.add(event)
            db.flush()
            a = save_asset(db, settings, owner, settings.provider_mode, data, kind, deadline, event.id)
            event.object_id = a.id
            return asset_view(a), 201

        return mutate(
            request,
            {
                "sha256": hashlib.sha256(data).hexdigest(),
                "cleanup_after": cleanup_after,
                "consent": consent_body.model_dump(),
            },
            run,
        )

    class SourceURL(BaseModel):
        url: str = Field(max_length=4000)

    @app.post("/api/v1/assets/{id}/source-url")
    def source_url(id: str, request: Request, body: SourceURL):
        validation_enabled()
        parsed = urlsplit(body.url)
        host = settings.voice_sample_allowed_host
        if not host:
            raise AppError(
                "SAMPLE_HOST_NOT_CONFIGURED",
                "尚未配置私有 OSS 桶域名，请填写 VOICE_SAMPLE_ALLOWED_HOST 并重启后端和 worker。",
                503,
            )
        if (
            parsed.scheme != "https"
            or parsed.netloc != host
            or not host.endswith(".aliyuncs.com")
            or not parsed.query
        ):
            raise AppError("SAMPLE_URL_INVALID", "请使用已配置私有存储域名的短时签名 HTTPS URL。", 422)

        def run(db, owner):
            a = owned(db, m.Asset, id, owner, settings.provider_mode)
            if a.kind != "voice_sample" or a.state != "ready":
                raise AppError("SAMPLE_INVALID", "样本不可使用。")
            validate_sample_url_expiry(body.url)
            a.source_url = body.url
            return {"id": id, "source_url_bound": True}, 200

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/assets/{id}/content")
    def download(id: str, request: Request):
        with read(request) as (db, owner, _):
            a = owned(db, m.Asset, id, owner, settings.provider_mode)
            p = asset_path(settings, a.storage_key)
            if a.state != "ready" or not p.is_file():
                raise AppError("ASSET_UNAVAILABLE", "文件不存在或正在清理。", 404)
            filename = f"{a.kind}-{a.id}.wav"
            if a.kind == "formal_audio":
                from backend.app.services.exports import safe_name

                audio = db.scalar(select(m.AudioVersion).where(m.AudioVersion.asset_id == a.id))
                if audio:
                    task = db.get(m.Task, audio.task_id)
                    snap = db.get(m.Snapshot, task.snapshot_id)
                    state = db.get(m.TaskTextState, task.id)
                    label = (
                        "旧稿"
                        if not state or state.script_id != audio.script_id
                        else "已确认"
                        if audio.confirmed_at
                        else "草稿"
                    )
                    filename = f"{safe_name(snap.payload['metadata']['full_name'].split('/')[-1])}-{task.id[:8]}-{audio.id[:8]}-{label}.wav"
            return FileResponse(p, media_type=a.mime, filename=filename, content_disposition_type="inline")

    @app.get("/api/v1/assets/{id}")
    def asset_metadata(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, asset_view(owned(db, m.Asset, id, owner, settings.provider_mode)))

    @app.delete("/api/v1/assets/{id}")
    def delete_asset(id: str, request: Request, body: s.DeleteIn):
        def run(db, owner):
            a = owned(db, m.Asset, id, owner, settings.provider_mode)
            jobs = db.scalars(
                select(m.Job).where(
                    m.Job.owner_id == owner,
                    m.Job.data_mode == settings.provider_mode,
                    m.Job.status.in_(["queued", "running", "needs_reconciliation"]),
                )
            )
            if any(j.input_json.get("sample_asset_id") == id for j in jobs):
                raise AppError("ASSET_IN_USE", "样本仍有在途或待核对作业，请先处理原作业。")
            pending_voice = db.scalar(
                select(m.ProbeVoice).where(
                    m.ProbeVoice.sample_asset_id == id,
                    m.ProbeVoice.status.in_(["cloning", "needs_reconciliation", "remote_deletion_pending"]),
                )
            )
            if pending_voice:
                raise AppError("ASSET_IN_USE", "关联音色仍在处理或待核对，样本暂不能删除。")
            if db.scalar(select(m.AudioVersion.id).where(m.AudioVersion.asset_id == id)):
                raise AppError("ASSET_IN_USE", "这是正式配音的版本文件，不能从样本清理入口删除。")
            delete_asset_file(db, settings, a)
            a.source_url = None
            return {"id": id, "state": "deleted"}, 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/validation-runs")
    def validate(request: Request, body: s.ValidationIn):
        validation_enabled()
        inputs = canonical_input(body.kind, body.model_dump())

        def run(db, owner):
            Providers(settings).check(body.kind)
            if body.contract_version != "S1-2026-09-08":
                raise AppError("CONTRACT_VERSION_INVALID", "实验契约版本不匹配。", 422)
            return receipt(create_validation(db, settings, owner, inputs, body.cost_authorization_id)), 202

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/validation-runs")
    def validations(request: Request):
        validation_enabled()
        with read(request) as (db, owner, _):
            rows = db.scalars(
                select(m.ValidationRun)
                .where(m.ValidationRun.owner_id == owner, m.ValidationRun.data_mode == settings.provider_mode)
                .order_by(m.ValidationRun.created_at.desc())
                .limit(50)
            )
            return ok(
                request,
                {
                    "items": [
                        validation_view(v)
                        for v in rows
                        if v.kind != "writing"
                        or life.state(db, v.input_ref.get("task_id")) not in life.CLOSED
                    ],
                    "next_cursor": None,
                },
            )

    @app.get("/api/v1/validation-runs/{id}")
    def validation(id: str, request: Request):
        validation_enabled()
        with read(request) as (db, owner, _):
            return ok(request, validation_view(owned(db, m.ValidationRun, id, owner, settings.provider_mode)))

    @app.get("/api/v1/probe-voices")
    def voices(request: Request):
        with read(request) as (db, owner, _):
            rows = db.scalars(
                select(m.ProbeVoice)
                .where(m.ProbeVoice.owner_id == owner, m.ProbeVoice.data_mode == settings.provider_mode)
                .order_by(m.ProbeVoice.created_at.desc())
            )
            return ok(request, {"items": [voice_view(v) for v in rows], "next_cursor": None})

    @app.get("/api/v1/probe-voices/{id}")
    def voice(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, voice_view(owned(db, m.ProbeVoice, id, owner, settings.provider_mode)))

    def revision_check(v, revision):
        if v.revision != revision:
            raise AppError("REVISION_CONFLICT", "音色状态已改变，请刷新。")

    @app.post("/api/v1/probe-voices/{id}/confirm")
    def confirm_voice(id: str, request: Request, body: s.VoiceConfirmIn):
        def run(db, owner):
            v = owned(db, m.ProbeVoice, id, owner, settings.provider_mode)
            revision_check(v, body.expected_revision)
            r = owned(db, m.ValidationRun, body.preview_run_id, owner, settings.provider_mode)
            if (
                v.status not in ("awaiting_preview", "ready")
                or r.input_ref.get("probe_voice_id") != id
                or r.kind != "voice_preview"
                or not r.output
            ):
                raise AppError("PREVIEW_INVALID", "请先完成当前音色的短句试听。")
            a = owned(db, m.Asset, r.output["asset_id"], owner, settings.provider_mode)
            if a.state != "ready":
                raise AppError("PREVIEW_INVALID", "试听文件不可用。")
            v.status, v.preview_confirmed_at = "ready", time.time()
            v.revision += 1
            r.human_verdict = "mock_workflow_confirmed" if settings.provider_mode == "mock" else "accepted"
            return voice_view(v), 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/probe-voices/{id}/disable")
    def disable_voice(id: str, request: Request, body: s.RevisionIn):
        def run(db, owner):
            v = owned(db, m.ProbeVoice, id, owner, settings.provider_mode)
            revision_check(v, body.expected_revision)
            if v.status in ("deleted", "deletion_pending", "remote_deletion_pending"):
                raise AppError("VOICE_NOT_ACTIVE", "音色已进入删除流程。")
            v.status, v.revision = "disabled", v.revision + 1
            clear_default(db, v)
            return voice_view(v), 200

        return mutate(request, body.model_dump(), run)

    @app.delete("/api/v1/probe-voices/{id}")
    def delete_voice(id: str, request: Request, body: s.VoiceDeleteIn):
        def run(db, owner):
            v = owned(db, m.ProbeVoice, id, owner, settings.provider_mode)
            revision_check(v, body.expected_revision)
            if v.status in ("deleted", "deletion_pending"):
                raise AppError("VOICE_NOT_ACTIVE", "音色已删除或正在删除。")
            v.status, v.revision = "deletion_pending", v.revision + 1
            clear_default(db, v)
            return receipt(enqueue(db, settings, owner, "voice_delete", {"probe_voice_id": id})), 202

        return mutate(request, body.model_dump(), run)

    from backend.app.api.creation import install

    install(app, settings, read, mutate, ok)
    from backend.app.api.audio import install as install_audio

    install_audio(app, settings, read, mutate, ok)
    from backend.app.api.voices import install as install_voices

    install_voices(app, settings, read, mutate, ok)
    from backend.app.api.ranking import install as install_ranking

    install_ranking(app, settings, sf, read, mutate, ok)
    from backend.app.api.task_lifecycle import install as install_lifecycle

    install_lifecycle(app, settings, read, mutate, ok)
    from backend.app.api.task_context import install as install_context

    install_context(app, settings, read, mutate, ok)
    from backend.app.api.cost_reconciliation import install as install_cost_reconciliation

    install_cost_reconciliation(app, settings, read, mutate, ok)
    return app


app = create_app()
