"""Directory-price accounting, never represented as a provider invoice.

All reservation decisions must run inside the application's BEGIN IMMEDIATE transaction.
"""

import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import digest, owned

PRICE_VERSION = "bailian-beijing-2026-09-09"
PRICE_SOURCE = "https://help.aliyun.com/zh/model-studio/model-pricing"
PRICE_VALID_UNTIL = datetime(2026, 10, 9, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
TEXT_ACTIONS = {"analysis", "writing", "generation", "discovery_score", "discovery_brief"}
VOICE_ACTIONS = {"voice_preview", "voice_reuse", "audio_generation"}
FREE_ACTIONS = {"import", "discovery_search", "voice_delete", "export"}
ENROLLMENT_PRICE_VERSION = "cosyvoice-beijing-create-free-2026-09-10"
ENROLLMENT_PRICE_SOURCE = "https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide"
ENROLLMENT_PRICE_VALID_UNTIL = datetime(2026, 10, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
ENROLLMENT_RULE = "cosyvoice_create_voice_free"


def enrollment_quote(db, settings, owner, inputs, prepared=None):
    from backend.app.services.jobs import validate_inputs
    from backend.app.services.providers import enrollment_request

    if time.time() >= ENROLLMENT_PRICE_VALID_UNTIL:
        raise AppError("PRICE_EXPIRED", "声音复刻价目已过核验期限，请先核实官方价格。")
    validate_inputs(db, settings, owner, "voice_enrollment", inputs)
    sample = owned(db, m.Asset, inputs["sample_asset_id"], owner, settings.provider_mode)
    source = urlsplit(sample.source_url or "")
    if (
        source.scheme != "https"
        or source.netloc != settings.voice_sample_allowed_host
        or not source.netloc.endswith(".aliyuncs.com")
        or not source.query
    ):
        raise AppError("SAMPLE_URL_INVALID", "请重新绑定当前私有 OSS 桶的短时签名 HTTPS URL。", 422)
    request = enrollment_request(settings, prepared["signed_url"] if prepared else sample.source_url)
    return {
        "price_version": ENROLLMENT_PRICE_VERSION,
        "source": ENROLLMENT_PRICE_SOURCE,
        "model": settings.voice_target_model,
        "billing_rule": ENROLLMENT_RULE,
        "maximum_micros": 0,
        "notice": "创建音色免费；本次会向百炼发送本人录音，后续试听和合成单独计费、另行确认。",
        "request_hash": digest(request),
        "sample_hash": digest(
            [sample.id, prepared["sample_sha"] if prepared else sample.sha256, sample.consent_record_id]
        ),
        "scope_hash": digest(
            [
                settings.voice_region,
                settings.voice_enrollment_endpoint,
                settings.voice_sample_allowed_host,
                settings.voice_api_key.get_secret_value(),
            ]
        ),
    }


def month():
    return datetime.fromtimestamp(time.time(), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


def money(micros):
    return f"{micros / 1_000_000:.6f}"


def quote(db, settings, owner, action, inputs, prepared=None):
    from backend.app.services.audio import prepare_audio
    from backend.app.services.jobs import text_inputs
    from backend.app.services.providers import text_request

    if action == "voice_enrollment":
        return enrollment_quote(db, settings, owner, inputs, prepared)
    if time.time() >= PRICE_VALID_UNTIL:
        raise AppError("PRICE_EXPIRED", "价目已过核验期限，请核实官方价格后再调用。")
    if action in VOICE_ACTIONS:
        if settings.voice_target_model != "cosyvoice-v3.5-flash" or settings.voice_region != "beijing":
            raise AppError("PRICE_UNVERIFIED", "该语音型号或地域尚未核实计价，已停止提交。")
        text = (
            prepared["text"]
            if prepared
            else prepare_audio(db, settings, owner, inputs)["synthesis_text"]
            if action == "audio_generation"
            else inputs["text"]
        )
        voice = owned(db, m.ProbeVoice, inputs["probe_voice_id"], owner, settings.provider_mode)
        request = {"text": text, "voice": voice.provider_voice_id, "format": "WAV_24000HZ_MONO_16BIT"}
        units = len(text.encode("utf-8"))
        maximum = units * 80
        model = settings.voice_target_model
        scope = [
            settings.voice_region,
            settings.voice_synthesis_endpoint,
            settings.voice_api_key.get_secret_value(),
        ]
        detail = {"characters_upper": units, "unit_micros": 80}
    elif action in TEXT_ACTIONS:
        if settings.qwen_model != "qwen3.7-flash" or settings.qwen_region != "beijing":
            raise AppError("PRICE_UNVERIFIED", "该文本型号或地域尚未核实计价，已停止提交。")
        request = text_request(
            settings,
            action,
            prepared if prepared is not None else text_inputs(db, settings, owner, action, inputs),
        )
        if request["enable_thinking"]:
            raise AppError("PRICE_UNVERIFIED", "当前预算仅核实非思考文本请求，请关闭思考后重新预览。")
        # Plain-text byte-level tokenization upper bound plus message framing margin.
        units = sum(len(msg["content"].encode("utf-8")) for msg in request["messages"]) + 1024
        if units > 1_000_000:
            raise AppError("COST_INPUT_TOO_LARGE", "输入超出已核实的计价范围。")
        maximum = (units * 12 + request["max_tokens"] * 48 + 9) // 10
        model = settings.qwen_model
        scope = [settings.qwen_region, settings.qwen_base_url, settings.qwen_api_key.get_secret_value()]
        detail = {"input_tokens_upper": units, "output_tokens_upper": request["max_tokens"]}
    else:
        raise AppError("PRICE_UNVERIFIED", "该操作的费用尚未可靠核实，金额预算下暂不提交。")
    return {
        "price_version": PRICE_VERSION,
        "source": PRICE_SOURCE,
        "model": model,
        "request_hash": digest(request),
        "scope_hash": digest(scope),
        "maximum_micros": maximum,
        **detail,
    }


def ledger_for(db, job):
    return db.scalar(select(m.CostLedger).where(m.CostLedger.job_id == job.id))


def summary(db, settings, owner, exclude=None, overrides=None):
    from backend.app.services.cost_reconciliation import CHARGEABLE, effect, latest_map

    jobs = list(
        db.scalars(
            select(m.Job).where(
                m.Job.owner_id == owner, m.Job.data_mode == settings.provider_mode, m.Job.kind.in_(CHARGEABLE)
            )
        )
    )
    ledgers = {
        r.job_id: r
        for r in db.scalars(
            select(m.CostLedger).where(
                m.CostLedger.owner_id == owner, m.CostLedger.data_mode == settings.provider_mode
            )
        )
    }
    attempts = {}
    for a in db.scalars(
        select(m.Attempt)
        .join(m.Job, m.Job.id == m.Attempt.job_id)
        .where(m.Job.owner_id == owner, m.Job.data_mode == settings.provider_mode)
        .order_by(m.Attempt.attempt_no)
    ):
        attempts.setdefault(a.job_id, []).append(a)
    reconciled = latest_map(db, owner, settings.provider_mode)
    reconciled.update(overrides or {})
    effects = [
        effect(j, ledgers.get(j.id), attempts.get(j.id, []), reconciled.get(j.id))
        for j in jobs
        if j.id != exclude
    ]
    current = month()
    priced = sum(e["priced_micros"] for e in effects if e["budget_month"] == current)
    actual = sum(e["actual_micros"] or 0 for e in effects if e["budget_month"] == current)
    held = sum(e["held_micros"] for e in effects)
    return {
        "month": current,
        "timezone": "Asia/Shanghai",
        "currency": "CNY",
        "single_limit_micros": settings.cost_single_limit_micros,
        "monthly_limit_micros": settings.cost_monthly_limit_micros,
        "priced_micros": priced,
        "actual_micros": actual,
        "held_micros": held,
        "remaining_micros": max(0, settings.cost_monthly_limit_micros - actual - priced - held),
        "pending_count": sum(e["pending"] for e in effects),
        "historical_unknown_count": sum(e["historical_unknown"] for e in effects),
        "awaiting_bill_count": sum(e["awaiting_bill"] for e in effects),
        "reconciled_count": sum(e["actual_micros"] is not None for e in effects),
        "blocked_by_overrun": any(e["overrun"] for e in effects),
        "scope": "本工作台模型请求：按原调用月份计算已核实实际费用及未对账目录价，另保留跨月未知预占；无可靠金额的历史费用继续未知，OSS/其他应用不包含。",
    }


def check_quote_limit(db, settings, owner, quoted, exclude=None):
    return check_limit(
        db,
        settings,
        owner,
        quoted["maximum_micros"],
        exclude,
        allow_zero=(
            quoted.get("billing_rule") == ENROLLMENT_RULE
            and quoted.get("price_version") == ENROLLMENT_PRICE_VERSION
        ),
    )


def check_limit(db, settings, owner, maximum, exclude=None, *, allow_zero=False):
    state = summary(db, settings, owner, exclude)
    if state["blocked_by_overrun"]:
        raise AppError("COST_RECONCILIATION_REQUIRED", "存在用量超出预占的记录，请先核对费用。")
    if settings.cost_single_limit_micros == 0 or settings.cost_monthly_limit_micros == 0:
        raise AppError("COST_CALLS_PAUSED", "费用上限设为零，模型调用已暂停，包括免费音色创建。")
    if maximum < 0 or (maximum == 0 and not allow_zero) or maximum > settings.cost_single_limit_micros:
        raise AppError("COST_SINGLE_LIMIT", "本次保守费用超过单次上限，已停止提交。")
    if maximum > state["remaining_micros"]:
        raise AppError("COST_MONTHLY_LIMIT", "本月剩余额度不足（含结果待核对占用），已停止提交。")
    return state


def authorization_quote(db, settings, auth):
    q = db.scalar(
        select(m.CostQuote).where(
            m.CostQuote.preview_id == auth.preview_id,
            m.CostQuote.owner_id == auth.owner_id,
            m.CostQuote.data_mode == "live",
        )
    )
    if not q:
        raise AppError("COST_QUOTE_REQUIRED", "旧许可没有金额报价，请重新预览并确认费用。")
    return q


def reserve(db, settings, job, auth):
    q = authorization_quote(db, settings, auth)
    fresh = quote(db, settings, job.owner_id, job.kind, job.input_json)
    if fresh != q.payload:
        raise AppError("COST_QUOTE_CHANGED", "输入、账号、模型或价目已变化，请重新预览费用。")
    check_quote_limit(db, settings, job.owner_id, fresh)
    db.add(
        m.CostLedger(
            owner_id=job.owner_id,
            data_mode=job.data_mode,
            job_id=job.id,
            quote_id=q.id,
            month=month(),
            reserved_micros=fresh["maximum_micros"],
        )
    )
    db.flush()


def before_dispatch(db, settings, job, inputs):
    if job.data_mode != "live" or job.kind in FREE_ACTIONS:
        return
    row = ledger_for(db, job)
    if not row or row.status != "reserved":
        raise AppError("COST_RESERVATION_REQUIRED", "没有有效预算预占，请重新预览费用。")
    q = db.get(m.CostQuote, row.quote_id)
    if quote(db, settings, job.owner_id, job.kind, job.input_json, prepared=inputs) != q.payload:
        raise AppError("COST_QUOTE_CHANGED", "发送前输入或计价已变化，本次未调用模型。")
    check_quote_limit(db, settings, job.owner_id, q.payload, exclude=job.id)
    row.month, row.updated_at = month(), time.time()


def valid_count(value):
    return type(value) is int and value >= 0


def settle(db, job, metrics):
    row = ledger_for(db, job)
    if not row or row.status in ("priced", "overrun", "released"):
        return
    q = db.get(m.CostQuote, row.quote_id).payload
    usage = metrics.get("usage") or {}
    amount = None
    overrun = False
    if (
        job.kind == "voice_enrollment"
        and q.get("billing_rule") == ENROLLMENT_RULE
        and q["maximum_micros"] == 0
        and metrics.get("model") == q["model"]
        and metrics.get("enrollment_completed") is True
    ):
        amount = 0
    if isinstance(usage, dict) and metrics.get("model") == q["model"]:
        if job.kind in VOICE_ACTIONS and valid_count(usage.get("characters")):
            amount = usage["characters"] * 80
        elif job.kind in TEXT_ACTIONS and all(
            valid_count(usage.get(k)) for k in ("prompt_tokens", "completion_tokens")
        ):
            a, b = usage["prompt_tokens"], usage["completion_tokens"]
            rate_in, rate_out = (2, 8) if a <= 32000 else (6, 24) if a <= 256000 else (12, 48)
            amount = (a * rate_in + b * rate_out + 9) // 10
            if a > q["input_tokens_upper"] or b > q["output_tokens_upper"]:
                overrun = True
    row.priced_micros = amount
    row.status = (
        "unknown" if amount is None else "overrun" if overrun or amount > row.reserved_micros else "priced"
    )
    row.updated_at = time.time()
    attempt = db.scalar(
        select(m.Attempt).where(m.Attempt.job_id == job.id).order_by(m.Attempt.attempt_no.desc())
    )
    if attempt:
        attempt.cost_status = row.status


def stop(db, job):
    row = ledger_for(db, job)
    if not row or row.status in ("priced", "overrun", "released"):
        return
    sent = db.scalar(
        select(m.Attempt.id).where(m.Attempt.job_id == job.id, m.Attempt.dispatch_state != "not_sent")
    )
    row.status = "unknown" if sent else "released"
    row.updated_at = time.time()
