"""Fixed-version private packages. Rebuilding never performs model work."""

import base64
import hashlib
import io
import json
import re
import time
import zipfile

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.assets import asset_path, save_blob
from backend.app.services.audio import material_state
from backend.app.services.common import iso
from backend.app.services.platform_rules import compose_copy
from backend.app.services.platform_rules import evaluate as platform_check
from backend.app.services.task_context import confirmed, context_id


def safe_name(name):
    return re.sub(r"[^\w.-]", "_", name, flags=re.UNICODE)[:80].strip(".") or "tool"


def text_body(version):
    if version.kind == "script":
        content = version.payload["content"] or {}
        return "\n".join(str(content.get(key, "")) for key in ("pain", "intro", "feature", "example"))
    return compose_copy(version.payload["content"])


def freeze_manifest(db, settings, task, complete):
    state, script, copy, audio = material_state(db, task, settings)
    if complete and not state["ready"]:
        raise AppError("MATERIALS_NOT_READY", "完整素材尚未就绪：" + "；".join(state["missing"]))
    if not script and not copy and not audio:
        raise AppError("EXPORT_EMPTY", "还没有可导出的素材，请先保存稿件。")
    snap = db.get(m.Snapshot, task.snapshot_id)
    name = snap.payload["metadata"]["full_name"].split("/")[-1]
    audio_asset = (
        db.get(m.Asset, audio.asset_id) if audio and audio.asset_id and state["audio"]["available"] else None
    )
    return {
        "schema_version": 2,
        "context_id": context_id(db, task.id),
        "task_id": task.id,
        "task_revision": task.revision,
        "tool_name": name,
        "complete": complete,
        "mode": task.data_mode,
        "missing": state["missing"],
        "notice": "模拟验收素材，音频为测试音。"
        if task.data_mode == "mock"
        else "创作素材；平台限制与剪辑后的成片需另行复核。",
        "script": {
            "id": script.id,
            "confirmed_at": iso(script.confirmed_at) if confirmed(db, script) else None,
            "text": text_body(script),
        }
        if script
        else None,
        "copy": {
            "id": copy.id,
            "script_id": copy.script_id,
            "confirmed_at": iso(copy.confirmed_at) if confirmed(db, copy) else None,
            "text": text_body(copy),
            "platform_limits": platform_check(copy.payload["content"]),
        }
        if copy
        else None,
        "audio": {
            "id": audio.id,
            "script_id": audio.script_id,
            "asset_id": audio_asset.id,
            "sha256": audio_asset.sha256,
            "duration_ms": audio_asset.duration_ms,
            "confirmed_at": iso(audio.confirmed_at) if confirmed(db, audio) else None,
            "stale": state["audio"]["stale"],
            "synthesis_text": audio.payload["synthesis_text"],
        }
        if audio_asset
        else None,
    }


def export_view(db, item, settings=None):
    job = db.get(m.Job, item.job_id)
    asset = db.get(m.Asset, item.asset_id) if item.asset_id else None
    available = bool(asset and asset.state == "ready")
    if available and settings:
        available = asset_path(settings, asset.storage_key).is_file()
    return {
        "id": item.id,
        "job_id": item.job_id,
        "status": job.status,
        "created_at": iso(item.created_at),
        "complete": item.payload["complete"],
        "task_revision": item.payload["task_revision"],
        "mode": item.data_mode,
        "available": available,
        "missing": item.payload["missing"],
        "script_id": item.script_id,
        "copy_id": item.copy_id,
        "audio_id": item.audio_id,
        "download_path": f"/api/v1/exports/{item.id}/content" if available else None,
    }


def build_package(settings, payload, audio_bytes=None):
    prefix = f"{safe_name(payload['tool_name'])}-{payload['task_id'][:8]}-r{payload['task_revision']}"
    suffix = "模拟" if payload["mode"] == "mock" else "完整" if payload["complete"] else "部分草稿"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for kind, label in [("script", "口播"), ("copy", "发布文案")]:
            entry = payload[kind]
            if entry:
                archive.writestr(f"{prefix}-{label}-{entry['id'][:8]}-{suffix}.txt", entry["text"])
        if payload["audio"]:
            if audio_bytes is None or hashlib.sha256(audio_bytes).hexdigest() != payload["audio"]["sha256"]:
                raise AppError(
                    "EXPORT_AUDIO_CHANGED", "固定版本的音频丢失或校验失败，请处理文件；不会重新调用模型。"
                )
            archive.writestr(f"{prefix}-配音-{payload['audio']['id'][:8]}-{suffix}.wav", audio_bytes)
        archive.writestr("版本说明.json", json.dumps(payload, ensure_ascii=False, indent=2))
    return stream.getvalue()


def save_export(db, settings, job, result):
    item = db.get(m.ExportManifest, job.input_json["export_id"])
    # Ignore a late response from a superseded local rebuild.
    if item.job_id != job.id:
        raise AppError("EXPORT_SUPERSEDED", "此打包操作已被更新的操作替代。")
    data = base64.b64decode(result["package_base64"], validate=True)
    asset = save_blob(
        db,
        settings,
        job.owner_id,
        job.data_mode,
        data,
        "export_package",
        "application/zip",
        ".zip",
        cleanup_after=time.time() + 86400,
    )
    item.asset_id = asset.id
    from backend.app.services.task_lifecycle import link_asset

    link_asset(db, item.task_id, asset.id)
    job.result_ref = {"export_id": item.id, "task_id": item.task_id}
