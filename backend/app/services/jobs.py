import time

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.schemas import AnalysisIn, AudioGenerationIn, GenerationIn, ScoreIn, ValidationIn
from backend.app.services.assets import validate_sample_url_expiry
from backend.app.services.common import digest, owned


def canonical_input(action, value):
    value = dict(value)
    value.pop("cost_authorization_id", None)
    if action in ("generation", "discovery_score", "discovery_brief", "audio_generation"):
        model = {
            "generation": GenerationIn,
            "discovery_score": ScoreIn,
            "discovery_brief": ScoreIn,
            "audio_generation": AudioGenerationIn,
        }[action]
        return model.model_validate(value).model_dump(exclude={"cost_authorization_id"})
    if action == "analysis":
        snapshot_id = value.pop("snapshot_id", None)
        if not snapshot_id:
            raise AppError("INPUT_INVALID", "缺少资料快照。", 422)
        parsed = AnalysisIn.model_validate(value).model_dump(exclude={"cost_authorization_id"})
        return {"snapshot_id": snapshot_id, **parsed}
    parsed = ValidationIn.model_validate({**value, "kind": action})
    return parsed.model_dump(exclude={"cost_authorization_id"})


def validate_inputs(db, settings, owner, kind, inputs):
    mode = settings.provider_mode
    if kind == "audio_generation":
        from backend.app.services.audio import prepare_audio

        prepare_audio(db, settings, owner, inputs)
    elif kind == "analysis":
        owned(db, m.Snapshot, inputs["snapshot_id"], owner, mode)
    elif kind == "discovery_brief":
        batch = owned(db, m.Discovery, inputs["discovery_id"], owner, mode)
        if not batch.payload.get("candidates"):
            raise AppError("DISCOVERY_EMPTY", "请先完成来源搜索。")
        if batch.payload.get("brief_runs"):
            raise AppError("DISCOVERY_ALREADY_BRIEFED", "已有中文介绍结果，请查看原结果；不会重复调用。")
    elif kind == "discovery_score":
        batch = owned(db, m.Discovery, inputs["discovery_id"], owner, mode)
        if not batch.payload.get("candidates"):
            raise AppError("DISCOVERY_EMPTY", "请先完成来源搜索；没有候选时不发起收费评分。")
        if batch.payload.get("score_runs"):
            raise AppError("DISCOVERY_ALREADY_SCORED", "已有评分结果，请查看原结果；新搜索另建查询。")
    elif kind == "generation":
        from backend.app.services.creation import text_owned

        task = owned(db, m.Task, inputs["task_id"], owner, mode)
        if inputs.get("rewrite_section", "all") != "all" and (
            inputs["mode"] != "script" or not inputs.get("base_script_id")
        ):
            raise AppError("REWRITE_BASE_REQUIRED", "单段改写需当前口播，并选择只改写口播。", 422)
        if inputs.get("base_script_id"):
            text_owned(db, inputs["base_script_id"], task, "script")
        elif inputs["mode"] == "copy":
            raise AppError("SCRIPT_REQUIRED", "单独生成文案需要选择当前稿件。", 422)
    elif kind == "writing":
        owned(db, m.Task, inputs["task_id"], owner, mode)
    elif kind == "voice_enrollment":
        a = owned(db, m.Asset, inputs["sample_asset_id"], owner, mode)
        if (
            a.state != "ready"
            or a.kind != "voice_sample"
            or a.consent_record_id != inputs["consent_record_id"]
        ):
            raise AppError("SAMPLE_INVALID", "样本或用途确认无效。")
        if a.cleanup_after and a.cleanup_after <= time.time():
            raise AppError("SAMPLE_EXPIRED", "样本已到清理时间。")
        if mode == "live" and not a.source_url:
            raise AppError("SAMPLE_URL_REQUIRED", "请先绑定与该样本一致的短时受控 URL。")
        if mode == "live":
            validate_sample_url_expiry(a.source_url)
    elif kind in ("voice_preview", "voice_reuse"):
        v = owned(db, m.ProbeVoice, inputs["probe_voice_id"], owner, mode)
        from backend.app.services.providers import Providers

        if v.provider_scope != Providers(settings).scope():
            raise AppError("VOICE_SCOPE_MISMATCH", "音色账号、地域或型号不兼容。")
        allowed = ["ready"] if kind == "voice_reuse" else ["awaiting_preview", "ready"]
        if v.status not in allowed:
            raise AppError("VOICE_NOT_READY", "音色未完成试听确认或已停用。")
        if kind == "voice_reuse":
            prev = owned(db, m.ValidationRun, inputs["previous_run_id"], owner, mode)
            if (
                prev.kind not in ("voice_preview", "voice_reuse")
                or not prev.output
                or prev.input_ref.get("probe_voice_id") != v.id
            ):
                raise AppError("REUSE_REFERENCE_INVALID", "复用需要同一音色已成功的独立合成记录。")
            if prev.input_ref.get("text") == inputs["text"]:
                raise AppError("REUSE_TEXT_REQUIRED", "请使用另一段文字验证音色复用。")


def enqueue(db, settings, owner, kind, inputs, authorization_id=None):
    from backend.app.services.task_lifecycle import TASK_KINDS, check_task

    if kind in TASK_KINDS and inputs.get("task_id"):
        check_task(db, inputs["task_id"], write=True)
    job = m.Job(
        owner_id=owner,
        data_mode=settings.provider_mode,
        kind=kind,
        input_json=inputs,
        input_hash=digest(inputs),
    )
    db.add(job)
    db.flush()
    if settings.provider_mode == "live" and kind not in (
        "import",
        "discovery_search",
        "voice_delete",
        "export",
    ):
        if not authorization_id:
            raise AppError("COST_AUTHORIZATION_REQUIRED", "请先确认本次调用可能产生的费用。")
        auth = owned(db, m.CostAuthorization, authorization_id, owner, "live")
        if (
            auth.action != kind
            or auth.input_hash != job.input_hash
            or auth.expires_at <= time.time()
            or auth.consumed_by
        ):
            raise AppError("COST_AUTHORIZATION_INVALID", "费用许可已到期、被使用或与输入不一致。")
        auth.consumed_by = job.id
        job.authorization_id = auth.id
        from backend.app.services.costs import reserve

        reserve(db, settings, job, auth)
    return job


def receipt(job):
    return {"job_id": job.id, "status": job.status, "status_url": f"/api/v1/jobs/{job.id}"}


def create_validation(db, settings, owner, inputs, auth_id):
    kind = inputs["kind"]
    validate_inputs(db, settings, owner, kind, inputs)
    job = enqueue(db, settings, owner, kind, inputs, auth_id)
    run = m.ValidationRun(
        owner_id=owner,
        data_mode=settings.provider_mode,
        kind=kind,
        job_id=job.id,
        contract_version=inputs["contract_version"],
        input_ref=inputs,
    )
    db.add(run)
    db.flush()
    return job


def text_inputs(db, settings, owner, kind, inputs):
    from backend.app.services.creation import context_for

    inputs = dict(inputs)
    if kind == "analysis":
        snap = owned(db, m.Snapshot, inputs["snapshot_id"], owner, settings.provider_mode)
        inputs = {
            **inputs,
            "fragments": snap.payload["fragments"],
            "metadata": snap.payload["metadata"],
        }
    elif kind in ("discovery_score", "discovery_brief"):
        batch = owned(db, m.Discovery, inputs["discovery_id"], owner, settings.provider_mode)
        inputs = {
            **inputs,
            **batch.payload["input"],
            "candidates": [
                {k: v for k, v in c.items() if k in {"metadata", "fragments", "missing_fields", "read_at"}}
                for c in batch.payload["candidates"]
            ],
        }
    elif kind == "generation":
        task = owned(db, m.Task, inputs["task_id"], owner, settings.provider_mode)
        inputs = {**inputs, **context_for(db, task)}
        if inputs.get("base_script_id"):
            base = db.get(m.TextVersion, inputs["base_script_id"])
            inputs["source_script"] = base.payload["content"]
            inputs["source_evidence_map"] = base.payload["evidence_map"]
    elif kind == "writing":
        task = owned(db, m.Task, inputs["task_id"], owner, settings.provider_mode)
        inputs = {**inputs, **context_for(db, task)}
    return inputs
