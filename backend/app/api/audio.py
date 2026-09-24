"""S3 endpoints; mutation infrastructure is shared with the existing workbench."""

from urllib.parse import quote

from fastapi import Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.core.errors import AppError
from backend.app.services.assets import asset_path
from backend.app.services.audio import audio_view, confirm_audio, material_state, prepare_audio
from backend.app.services.common import owned
from backend.app.services.creation import check_revision, text_owned
from backend.app.services.exports import export_view, freeze_manifest, safe_name, text_body
from backend.app.services.jobs import canonical_input, enqueue, receipt
from backend.app.services.providers import Providers
from backend.app.services.task_context import confirmed
from backend.app.services.views import task_view
from backend.app.services.voice_library import named_voice_view


def install(app, settings, read, mutate, ok):
    mode = settings.provider_mode

    def preview(db, owner, id, body):
        if id != body.task_id:
            raise AppError("TASK_MISMATCH", "任务标识不一致。", 422)
        task = owned(db, m.Task, id, owner, mode)
        check_revision(task, body.expected_revision)
        inputs = canonical_input("audio_generation", body.model_dump())
        return task, inputs, prepare_audio(db, settings, owner, inputs)

    @app.post("/api/v1/tasks/{id}/audio-preview")
    def audio_preview(id: str, request: Request, body: s.AudioGenerationIn):
        def run(db, owner):
            _, inputs, payload = preview(db, owner, id, body)
            return {
                **payload,
                "script_id": inputs["script_id"],
                "voice_revision": inputs["voice_revision"],
            }, 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/tasks/{id}/audio-generations")
    def generate(id: str, request: Request, body: s.AudioGenerationIn):
        def run(db, owner):
            task, inputs, payload = preview(db, owner, id, body)
            Providers(settings).check("audio_generation")
            active = db.scalars(
                select(m.Job).where(
                    m.Job.owner_id == owner,
                    m.Job.data_mode == mode,
                    m.Job.kind == "audio_generation",
                    m.Job.status.in_(["queued", "running", "needs_reconciliation"]),
                )
            )
            if any(
                all(
                    j.input_json.get(key) == inputs.get(key)
                    for key in ("task_id", "script_id", "probe_voice_id", "voice_revision", "pronunciation")
                )
                for j in active
            ):
                raise AppError(
                    "AUDIO_IN_PROGRESS", "同一稿件和音色已有在途或待核对的合成，请查看原处理记录。"
                )
            job = enqueue(db, settings, owner, "audio_generation", inputs, body.cost_authorization_id)
            version = m.AudioVersion(
                owner_id=owner,
                data_mode=mode,
                task_id=id,
                script_id=body.script_id,
                voice_id=body.probe_voice_id,
                voice_revision=body.voice_revision,
                job_id=job.id,
                payload=payload,
            )
            db.add(version)
            db.flush()
            return {**receipt(job), "audio_id": version.id}, 202

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/tasks/{id}/audio")
    def audio_list(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            state, script, _, _ = material_state(db, task, settings)
            rows = db.scalars(
                select(m.AudioVersion)
                .where(m.AudioVersion.task_id == id)
                .order_by(m.AudioVersion.created_at.desc())
            )
            voices = db.scalars(
                select(m.ProbeVoice)
                .where(m.ProbeVoice.owner_id == owner, m.ProbeVoice.data_mode == mode)
                .order_by(m.ProbeVoice.created_at.desc())
            )
            exports = db.scalars(
                select(m.ExportManifest)
                .where(m.ExportManifest.task_id == id)
                .order_by(m.ExportManifest.created_at.desc())
            )
            return ok(
                request,
                {
                    "task": task_view(task, db, settings),
                    "materials": state,
                    "versions": [audio_view(db, v, script.id if script else None, settings) for v in rows],
                    "voices": [named_voice_view(db, v, settings) for v in voices],
                    "exports": [export_view(db, e, settings) for e in exports],
                },
            )

    @app.post("/api/v1/tasks/{id}/audio/{audio_id}/confirm")
    def confirm(id: str, audio_id: str, request: Request, body: s.AudioConfirmIn):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            check_revision(task, body.expected_revision)
            version = owned(db, m.AudioVersion, audio_id, owner, mode)
            if version.task_id != id:
                raise AppError("AUDIO_REFERENCE_INVALID", "音频不属于当前任务。", 422)
            confirm_audio(db, settings, task, version)
            return task_view(task, db, settings), 200

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/tasks/{id}/texts/{version_id}/download")
    def download_text(
        id: str, version_id: str, request: Request, format: str = Query(default="txt", pattern="^(txt|md)$")
    ):
        with read(request) as (db, owner, _):
            task = owned(db, m.Task, id, owner, mode)
            version = text_owned(db, version_id, task)
            snap = db.get(m.Snapshot, task.snapshot_id)
            name = safe_name(snap.payload["metadata"]["full_name"].split("/")[-1])
            label = "已确认" if confirmed(db, version) else "草稿"
            filename = f"{name}-{id[:8]}-{version.kind}-{version.id[:8]}-{label}.{format}"
            return Response(
                text_body(version),
                media_type="text/plain" if format == "txt" else "text/markdown",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
                    "Cache-Control": "no-store",
                },
            )

    @app.post("/api/v1/tasks/{id}/exports")
    def create_export(id: str, request: Request, body: s.ExportIn):
        def run(db, owner):
            task = owned(db, m.Task, id, owner, mode)
            check_revision(task, body.expected_revision)
            payload = freeze_manifest(db, settings, task, body.complete)
            item = m.ExportManifest(
                owner_id=owner,
                data_mode=mode,
                task_id=id,
                payload=payload,
                script_id=(payload["script"] or {}).get("id"),
                copy_id=(payload["copy"] or {}).get("id"),
                audio_id=(payload["audio"] or {}).get("id"),
            )
            # Assign the identity before enqueuing; both writes commit in one transaction.
            item.id = m.uid()
            job = enqueue(db, settings, owner, "export", {"export_id": item.id, "task_id": id})
            item.job_id = job.id
            db.add(item)
            return {**receipt(job), "export_id": item.id}, 202

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/exports/{id}/rebuild")
    def rebuild(id: str, request: Request, body: s.DeleteIn):
        def run(db, owner):
            item = owned(db, m.ExportManifest, id, owner, mode)
            job = db.get(m.Job, item.job_id)
            if job.status in ("queued", "running"):
                return {**receipt(job), "export_id": id}, 200
            job = enqueue(db, settings, owner, "export", {"export_id": id, "task_id": item.task_id})
            item.job_id = job.id
            return {**receipt(job), "export_id": id}, 202

        return mutate(request, body.model_dump(), run)

    @app.get("/api/v1/exports/{id}/content")
    def download_export(id: str, request: Request):
        with read(request) as (db, owner, _):
            item = owned(db, m.ExportManifest, id, owner, mode)
            if not export_view(db, item, settings)["available"]:
                raise AppError("EXPORT_UNAVAILABLE", "素材包未完成或已清理，请重新打包；不会调用模型。", 404)
            asset = db.get(m.Asset, item.asset_id)
            label = "模拟" if mode == "mock" else "完整" if item.payload["complete"] else "部分草稿"
            filename = f"{safe_name(item.payload['tool_name'])}-{item.task_id[:8]}-r{item.payload['task_revision']}-{label}.zip"
            return FileResponse(
                asset_path(settings, asset.storage_key),
                media_type="application/zip",
                filename=filename,
                headers={"Cache-Control": "no-store"},
            )
