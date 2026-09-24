"""Formal audio is an immutable candidate tied to a confirmed script and voice."""

import base64
import re
import time

from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.assets import asset_path, normalize_cosyvoice_wav, save_asset
from backend.app.services.common import iso, owned
from backend.app.services.creation import state_for, text_owned
from backend.app.services.providers import Providers
from backend.app.services.task_context import confirmed, context_id


def script_text(version):
    return "\n".join(version.payload["content"][key] for key in ("pain", "intro", "feature", "example"))


def prepare_audio(db, settings, owner, inputs):
    task = owned(db, m.Task, inputs["task_id"], owner, settings.provider_mode)
    script = text_owned(db, inputs["script_id"], task, "script")
    state = state_for(db, task)
    if state.script_id != script.id or not confirmed(db, script) or not script.payload["valid"]:
        raise AppError("SCRIPT_NOT_CONFIRMED", "请先保存、采用并确认当前口播，再生成配音。")
    voice = owned(db, m.ProbeVoice, inputs["probe_voice_id"], owner, settings.provider_mode)
    if voice.status != "ready" or not voice.preview_confirmed_at or not voice.provider_voice_id:
        raise AppError("VOICE_NOT_READY", "请选择已试听确认的可用音色；停用或删除的音色不能合成。")
    if voice.revision != inputs["voice_revision"]:
        raise AppError("REVISION_CONFLICT", "音色已经更新，请重新选择并预览。")
    if voice.provider_scope != Providers(settings).scope():
        raise AppError("VOICE_SCOPE_MISMATCH", "音色账号、地域或型号不兼容，请在声音设置核对。")
    original = script_text(script)
    snap = db.get(m.Snapshot, task.snapshot_id)
    tool_name = snap.payload["metadata"]["full_name"].split("/")[-1]
    pronunciation = inputs.get("pronunciation", "")
    synthesis = (
        re.sub(
            r"(?<![A-Za-z0-9_-])" + re.escape(tool_name) + r"(?![A-Za-z0-9_-])",
            lambda _: pronunciation,
            original,
        )
        if pronunciation
        else original
    )
    # Conservative application bound for the already-verified single-request contract.
    if len(synthesis) > 2000:
        raise AppError(
            "SYNTHESIS_TEXT_TOO_LONG", "合成正文超过当前单请求 2000 字符上限，请压缩并重新确认稿件。", 422
        )
    return {
        "context_id": context_id(db, task.id),
        "original_text": original,
        "synthesis_text": synthesis,
        "tool_name": tool_name,
        "pronunciation": pronunciation,
        "pronunciation_reviewed": inputs.get("pronunciation_reviewed", False),
        "model": settings.voice_target_model,
        "region": settings.voice_region,
        "parameters": {"format": "WAV_24000HZ_MONO_16BIT"},
        "is_test_tone": settings.provider_mode == "mock",
        "duration_ms": None,
    }


def audio_view(db, version, current_script_id=None, settings=None):
    job = db.get(m.Job, version.job_id)
    asset = db.get(m.Asset, version.asset_id) if version.asset_id else None
    available = bool(asset and asset.state == "ready")
    if available and settings:
        available = asset_path(settings, asset.storage_key).is_file()
    duration = version.payload.get("duration_ms")
    within = duration is not None and 30000 <= duration <= 35000
    script = db.get(m.TextVersion, current_script_id) if current_script_id else None
    context_stale = version.payload.get("context_id") != context_id(db, version.task_id) and not confirmed(
        db, version
    )
    stale = current_script_id != version.script_id or context_stale
    status = "旧稿配音 · 待更新" if stale else "已试听确认" if confirmed(db, version) else "待试听确认"
    if context_stale and current_script_id == version.script_id:
        status = "资料已变 · 待重新试听确认"
    if not available:
        status = {
            "queued": "排队中",
            "running": "合成中",
            "failed": "合成失败",
            "needs_reconciliation": "结果待核对",
            "cancelled": "已停止采用",
        }.get(job.status, "音频不可用")
    elif not within:
        status = "时长未达标" + (" · 旧稿" if stale else "")
    return {
        "id": version.id,
        "job_id": version.job_id,
        "task_id": version.task_id,
        "script_id": version.script_id,
        "voice_id": version.voice_id,
        "voice_revision": version.voice_revision,
        "asset_id": asset.id if available else None,
        "created_at": iso(version.created_at),
        "confirmed_at": iso(version.confirmed_at) if confirmed(db, version) else None,
        "context_stale": context_stale,
        "can_confirm": bool(
            available
            and within
            and current_script_id == version.script_id
            and confirmed(db, script)
            and not confirmed(db, version)
        ),
        "status": status,
        "job_status": job.status,
        "stale": stale,
        "duration_valid": within,
        "available": available,
        **version.payload,
    }


def current_audio(db, task):
    state = db.get(m.TaskAudioState, task.id)
    return db.get(m.AudioVersion, state.audio_id) if state and state.audio_id else None


def material_state(db, task, settings=None):
    state = db.get(m.TaskTextState, task.id)
    script = db.get(m.TextVersion, state.script_id) if state and state.script_id else None
    copy = db.get(m.TextVersion, state.copy_id) if state and state.copy_id else None
    audio = current_audio(db, task)
    view = audio_view(db, audio, script.id if script else None, settings) if audio else None
    missing = []
    if not script or not confirmed(db, script) or not script.payload.get("valid"):
        missing.append("已确认口播")
    if not view or not (
        view["available"] and view["duration_valid"] and view["confirmed_at"] and not view["stale"]
    ):
        missing.append("与当前稿一致且已试听确认的合格音频")
    if (
        not copy
        or not confirmed(db, copy)
        or not copy.payload.get("valid")
        or not script
        or copy.script_id != script.id
    ):
        missing.append("与当前稿一致且已确认的发布文案")
    return {"ready": not missing, "missing": missing, "audio": view}, script, copy, audio


def save_audio(db, settings, job, result):
    version = db.scalar(select(m.AudioVersion).where(m.AudioVersion.job_id == job.id))
    voice = db.get(m.ProbeVoice, version.voice_id)
    if voice.status != "ready" or voice.revision != version.voice_revision:
        job.status, job.phase = "cancelled", "voice_changed_before_adoption"
        return False
    data = base64.b64decode(result["audio_base64"], validate=True)
    metrics = result.get("metrics", {})
    if job.data_mode == "live" and metrics.get("model") == "cosyvoice-v3.5-flash":
        data = normalize_cosyvoice_wav(data)
    asset = save_asset(db, settings, job.owner_id, job.data_mode, data, "formal_audio")
    version.asset_id = asset.id
    from backend.app.services.task_lifecycle import link_asset

    link_asset(db, version.task_id, asset.id)
    version.payload = {**version.payload, "duration_ms": asset.duration_ms, "metrics": metrics}
    job.result_ref = {"audio_id": version.id, "asset_id": asset.id}
    return True


def confirm_audio(db, settings, task, version):
    state = state_for(db, task)
    script = db.get(m.TextVersion, state.script_id) if state.script_id else None
    if not script or script.id != version.script_id or not confirmed(db, script):
        raise AppError("AUDIO_STALE", "这段音频基于旧稿，请为当前已确认稿生成配音。")
    view = audio_view(db, version, script.id, settings)
    if not view["available"]:
        raise AppError("ASSET_UNAVAILABLE", "音频文件不可用，请查看原处理记录。", 404)
    if not view["duration_valid"]:
        raise AppError(
            "AUDIO_DURATION_INVALID", "实际时长不在 30–35 秒内；可下载草稿，请调整正文后重新生成。"
        )
    version.confirmed_at = time.time()
    version.payload = {**version.payload, "confirmed_context_id": context_id(db, task.id)}
    state = db.get(m.TaskAudioState, task.id)
    if not state:
        state = m.TaskAudioState(task_id=task.id)
        db.add(state)
    state.audio_id = version.id
    task.revision += 1
    db.add(
        m.EvidenceEvent(
            owner_id=task.owner_id,
            data_mode=task.data_mode,
            event_type="audio_confirmed",
            object_id=version.id,
            details={
                "script_id": script.id,
                "context_id": context_id(db, task.id),
                "mock_workflow_only": task.data_mode == "mock",
            },
        )
    )
