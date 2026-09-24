"""Task lifecycle, bounded deletion previews and recoverable private-content cleanup."""

import json
import time
import zipfile

from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services import costs
from backend.app.services.assets import asset_path, delete_asset_file
from backend.app.services.common import digest, iso

TASK_KINDS = {"writing", "generation", "audio_generation", "export"}
CLOSED = {"trashed", "purged"}
RECOVERY_SECONDS = 7 * 86400


def state(db, task_id):
    row = db.get(m.TaskLifecycle, task_id) if task_id else None
    return row.state if row else "active"


def check_task(db, task_id, write=False):
    value = state(db, task_id)
    if value in CLOSED:
        raise AppError("TASK_REMOVED", "任务已移入回收站或已清理，请到任务管理查看。", 404)
    if write and value == "archived":
        raise AppError("TASK_ARCHIVED", "任务已归档，请先恢复再编辑或生成。")


def task_for_object(db, obj):
    if isinstance(obj, m.Task):
        return obj.id
    if isinstance(obj, (m.TextVersion, m.AudioVersion, m.ExportManifest, m.Generation)):
        return obj.task_id
    if isinstance(obj, m.ValidationRun) and obj.kind == "writing":
        return obj.input_ref.get("task_id")
    if isinstance(obj, m.CostPreview):
        return obj.input_json.get("task_id") if obj.action in TASK_KINDS else None
    return None


def check_object(db, obj):
    task_id = task_for_object(db, obj)
    if task_id:
        check_task(db, task_id, write=db.info.get("task_write", False))
    if isinstance(obj, m.Asset) and obj.kind in ("formal_audio", "export_package"):
        ids = set(db.scalars(select(m.TaskAssetLink.task_id).where(m.TaskAssetLink.asset_id == obj.id)))
        ids.update(db.scalars(select(m.AudioVersion.task_id).where(m.AudioVersion.asset_id == obj.id)))
        if ids and all(state(db, id) in CLOSED for id in ids):
            raise AppError("TASK_REMOVED", "所属任务已移入回收站或已清理，文件暂不可下载。", 404)


def task_job_id(job):
    return job.input_json.get("task_id") if job.kind in TASK_KINDS else None


def task_jobs(db, task):
    return list(
        db.scalars(
            select(m.Job).where(
                m.Job.owner_id == task.owner_id,
                m.Job.data_mode == task.data_mode,
                m.Job.kind.in_(TASK_KINDS),
                m.Job.input_json["task_id"].as_string() == task.id,
            )
        )
    )


def link_asset(db, task_id, asset_id):
    if not db.get(m.TaskAssetLink, (task_id, asset_id)):
        db.add(m.TaskAssetLink(task_id=task_id, asset_id=asset_id))
        db.flush()


def get_task(db, id, owner, mode):
    task = db.scalar(
        select(m.Task).where(m.Task.id == id, m.Task.owner_id == owner, m.Task.data_mode == mode)
    )
    if not task:
        raise AppError("NOT_FOUND", "记录不存在或不可访问。", 404)
    return task


def lifecycle_view(db, task):
    row = db.get(m.TaskLifecycle, task.id)
    status = row.state if row else "active"
    return {
        "task_id": task.id,
        "revision": task.revision,
        "state": status,
        "deleted_at": iso(row.deleted_at) if row else None,
        "purge_after": iso(row.purge_after) if row else None,
        "purged_at": iso(row.purged_at) if row else None,
        "can_restore": status == "archived" or bool(status == "trashed" and row.purge_after > time.time()),
        "cleanup_status": (row.cleanup_status if row else None),
        "recovery_days": 7,
    }


def task_summary(db, task):
    return {
        "id": task.id,
        "tool_id": task.tool_id,
        "snapshot_id": task.snapshot_id,
        "review_id": task.review_id,
        "audience": "",
        "revision": task.revision,
        "status": "trashed",
        "data_mode": task.data_mode,
        "materials_ready": False,
        "current_script": None,
        "current_copy": None,
        "current_audio": None,
        "lifecycle": lifecycle_view(db, task),
    }


def asset_ids(db, settings, task):
    ids = set(db.scalars(select(m.TaskAssetLink.asset_id).where(m.TaskAssetLink.task_id == task.id)))
    ids.update(db.scalars(select(m.AudioVersion.asset_id).where(m.AudioVersion.task_id == task.id)))
    ids.update(db.scalars(select(m.ExportManifest.asset_id).where(m.ExportManifest.task_id == task.id)))
    # Old rebuilds preceded asset ownership links. The immutable ZIP manifest identifies them.
    unlinked = db.scalars(
        select(m.Asset).where(
            m.Asset.owner_id == task.owner_id,
            m.Asset.data_mode == task.data_mode,
            m.Asset.kind == "export_package",
            m.Asset.state == "ready",
            ~m.Asset.id.in_(select(m.TaskAssetLink.asset_id)),
        )
    )
    for asset in unlinked:
        path = asset_path(settings, asset.storage_key)
        if not path.is_file():
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo("版本说明.json")
                if info.file_size > 200000 or info.compress_size > 200000:
                    raise ValueError()
                manifest = json.loads(archive.read(info))
                if manifest["task_id"] == task.id:
                    ids.add(asset.id)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            raise AppError("TASK_ASSET_UNVERIFIED", "旧素材包的任务归属无法核实，请先处理文件再预览删除。")
    ids.discard(None)
    return ids


def delete_preview(db, settings, task):
    if state(db, task.id) not in ("active", "archived"):
        raise AppError("TASK_STATE_CONFLICT", "只有进行中或归档任务可以移入回收站。")
    jobs = task_jobs(db, task)
    ids = asset_ids(db, settings, task)
    assets = list(db.scalars(select(m.Asset).where(m.Asset.id.in_(ids))))
    texts = list(db.scalars(select(m.TextVersion).where(m.TextVersion.task_id == task.id)))
    fingerprint = {
        "task": task.id,
        "revision": task.revision,
        "state": state(db, task.id),
        "texts": sorted(v.id for v in texts),
        "assets": sorted((a.id, a.state, a.sha256, a.byte_size) for a in assets),
        "jobs": sorted((j.id, j.status, j.updated_at, digest(j.result_ref)) for j in jobs),
    }
    return {
        "task_id": task.id,
        "expected_revision": task.revision,
        "preview_token": digest(fingerprint),
        "text_versions": len(texts),
        "audio_versions": len(
            list(db.scalars(select(m.AudioVersion.id).where(m.AudioVersion.task_id == task.id)))
        ),
        "files": sum(a.state == "ready" for a in assets),
        "bytes": sum(a.byte_size for a in assets if a.state == "ready"),
        "unfinished_jobs": sum(j.status in ("queued", "running", "needs_reconciliation") for j in jobs),
        "recovery_days": 7,
        "kept": [
            "本人音色和录音",
            "共享工具资料和复核",
            "公开榜单",
            "调用及费用核对记录",
            "已下载到外部的文件",
        ],
    }


def change(db, settings, task, action, body):
    from backend.app.services.creation import check_revision

    check_revision(task, body.expected_revision)
    current = state(db, task.id)
    row = db.get(m.TaskLifecycle, task.id)
    if not row:
        row = m.TaskLifecycle(task_id=task.id)
        db.add(row)
        db.flush()
    if action == "archive":
        if current != "active":
            raise AppError("TASK_STATE_CONFLICT", "任务状态已变化，请刷新任务管理。")
        row.state = "archived"
    elif action == "restore":
        if current == "archived":
            row.state = "active"
        elif current == "trashed" and row.purge_after > time.time():
            row.state = row.previous_state
            row.deleted_at = row.purge_after = None
            row.cleanup_status = None
        else:
            raise AppError("TASK_RESTORE_EXPIRED", "任务已到清理期限或不可恢复。")
    elif action == "trash":
        preview = delete_preview(db, settings, task)
        if preview["preview_token"] != body.preview_token:
            raise AppError("TASK_DELETE_PREVIEW_CHANGED", "任务或产物已变化，请重新预览删除范围。")
        for asset_id in asset_ids(db, settings, task):
            link_asset(db, task.id, asset_id)
        row.previous_state, row.state = current, "trashed"
        row.deleted_at = time.time()
        row.purge_after = row.deleted_at + RECOVERY_SECONDS
        row.cleanup_status = "recovery_period"
        for job in task_jobs(db, task):
            if job.status in ("queued", "running", "needs_reconciliation"):
                costs.stop(db, job)
                job.status, job.phase, job.updated_at = "cancelled", "task_trashed", time.time()
    task.revision += 1
    db.add(
        m.EvidenceEvent(
            owner_id=task.owner_id,
            data_mode=task.data_mode,
            event_type="task_" + action,
            object_id=task.id,
            details={"from": current, "to": row.state, "revision": task.revision},
        )
    )
    db.flush()
    return lifecycle_view(db, task)


def guard_mutation(db, request, body, owner, mode):
    """Apply before replaying cached mutation responses as well as before new writes."""
    parts = request.url.path.split("/")
    if len(parts) >= 5 and parts[3] == "tasks":
        if len(parts) == 6 and parts[5] in ("archive", "restore", "trash"):
            return
        task = get_task(db, parts[4], owner, mode)
        check_task(db, task.id, write=True)
    task_id = body.get("task_id") or (body.get("input") or {}).get("task_id")
    if body.get("preview_id"):
        preview = db.get(m.CostPreview, body["preview_id"])
        if preview and preview.owner_id == owner and preview.data_mode == mode:
            task_id = task_for_object(db, preview)
    if task_id:
        task = get_task(db, task_id, owner, mode)
        check_task(db, task.id, write=True)
    if len(parts) >= 5 and parts[3] == "exports":
        item = db.get(m.ExportManifest, parts[4])
        if item and item.owner_id == owner and item.data_mode == mode:
            check_task(db, item.task_id, write=True)


def purge_due(db, settings):
    rows = list(
        db.scalars(
            select(m.TaskLifecycle)
            .where(
                m.TaskLifecycle.state == "trashed",
                m.TaskLifecycle.purge_after <= time.time(),
            )
            .order_by(m.TaskLifecycle.cleanup_checked_at, m.TaskLifecycle.task_id)
            .limit(10)
        )
    )
    for row in rows:
        row.cleanup_checked_at = time.time()
        task = db.get(m.Task, row.task_id)
        jobs = task_jobs(db, task)
        ids = {j.id for j in jobs}
        attempts = list(db.scalars(select(m.Attempt).where(m.Attempt.job_id.in_(ids))))
        if any(a.dispatch_state not in ("not_sent", "finished") for a in attempts):
            row.cleanup_status = "waiting_for_result_reconciliation"
            continue
        assets = list(
            db.scalars(
                select(m.Asset)
                .join(m.TaskAssetLink, m.TaskAssetLink.asset_id == m.Asset.id)
                .where(m.TaskAssetLink.task_id == task.id)
            )
        )
        shared = any(
            db.scalar(
                select(m.TaskAssetLink.task_id).where(
                    m.TaskAssetLink.asset_id == a.id,
                    m.TaskAssetLink.task_id != task.id,
                )
            )
            or db.scalar(
                select(m.AudioVersion.id).where(
                    m.AudioVersion.asset_id == a.id, m.AudioVersion.task_id != task.id
                )
            )
            for a in assets
        )
        if shared:
            row.cleanup_status = "shared_asset_reference"
            continue
        try:
            for asset in assets:
                delete_asset_file(db, settings, asset)
                asset.source_url = None
            for job in jobs:
                for suffix in ("json", "part"):
                    asset_path(settings, f"checkpoints/{job.id}.{suffix}").unlink(missing_ok=True)
        except (OSError, AppError):
            row.cleanup_status = "file_cleanup_failed"
            continue
        private_ids = {task.id, *ids, *(a.id for a in assets)}
        for context in db.scalars(select(m.TaskContextVersion).where(m.TaskContextVersion.task_id == task.id)):
            private_ids.add(context.id)
            context.payload = {"purged": True}
        for cls in (m.TextVersion, m.AudioVersion, m.ExportManifest, m.Generation):
            for obj in db.scalars(select(cls).where(cls.task_id == task.id)):
                private_ids.add(obj.id)
                if cls == m.Generation:
                    obj.output = None
                else:
                    obj.payload = {"purged": True}
                    if cls in (m.TextVersion, m.AudioVersion):
                        obj.confirmed_at = None
        for job in jobs:
            job.input_json = {"task_id": task.id, "purged": True}
            job.result_ref = {"task_id": task.id, "purged": True}
        for run in db.scalars(select(m.ValidationRun).where(m.ValidationRun.job_id.in_(ids))):
            private_ids.add(run.id)
            run.input_ref, run.output, run.human_verdict = {"task_id": task.id}, None, None
        for preview in db.scalars(
            select(m.CostPreview).where(
                m.CostPreview.owner_id == task.owner_id,
                m.CostPreview.data_mode == task.data_mode,
            )
        ):
            if preview.input_json.get("task_id") == task.id:
                preview.input_json = {"task_id": task.id, "purged": True}

        def contains(value):
            if isinstance(value, str):
                return value in private_ids
            if isinstance(value, dict):
                return any(contains(v) for v in value.values())
            if isinstance(value, list):
                return any(contains(v) for v in value)
            return False

        for op in db.scalars(
            select(m.Operation).where(
                m.Operation.owner_id == task.owner_id, m.Operation.data_mode == task.data_mode
            )
        ):
            if contains(op.response) or any("/" + id in op.scope for id in private_ids):
                op.response, op.status_code = {"task_id": task.id, "purged": True}, 410
        for cls in (m.TaskTextState, m.TaskAudioState):
            pointer = db.get(cls, task.id)
            if pointer:
                db.delete(pointer)
        task.audience = ""
        row.state, row.cleanup_status, row.purged_at = "purged", "completed", time.time()
        db.add(
            m.EvidenceEvent(
                owner_id=task.owner_id,
                data_mode=task.data_mode,
                event_type="task_purged",
                object_id=task.id,
                details={"retained": "identity_and_billing_audit"},
            )
        )
