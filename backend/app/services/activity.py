"""Read-only activity views: execution, content quality and result access are distinct."""

from sqlalchemy import and_, exists, or_, select

from backend.app import models as m
from backend.app.services.common import owned
from backend.app.services.views import job_view

LABELS = {
    "audio_generation": "正式配音",
    "export": "素材打包",
    "generation": "正式文本生成",
    "writing": "口播与文案实验",
    "analysis": "资料分析",
    "import": "仓库导入",
    "discovery_search": "主题搜索",
    "discovery_score": "候选评分",
    "discovery_brief": "工具中文介绍",
    "voice_enrollment": "声音复刻",
    "voice_preview": "短句试听",
    "voice_reuse": "音色复用",
    "voice_delete": "删除音色",
}
STATES = {
    "queued": "排队中",
    "running": "处理中",
    "succeeded": "已完成",
    "failed": "失败 · 需要处理",
    "needs_reconciliation": "结果待核对",
    "cancelled": "已停止采用",
}


def attention_condition():
    invalid_text = exists(
        select(m.TextVersion.id)
        .join(m.Generation, m.TextVersion.generation_id == m.Generation.id)
        .where(m.Generation.job_id == m.Job.id, m.TextVersion.payload["valid"].as_boolean().is_(False))
    )
    invalid_probe = exists(
        select(m.ValidationRun.id).where(
            m.ValidationRun.job_id == m.Job.id, m.ValidationRun.verdict.in_(["failed", "blocked"])
        )
    )
    invalid_audio = exists(
        select(m.AudioVersion.id).where(
            m.AudioVersion.job_id == m.Job.id,
            or_(
                m.AudioVersion.payload["duration_ms"].as_float() < 30000,
                m.AudioVersion.payload["duration_ms"].as_float() > 35000,
            ),
        )
    )
    return or_(
        m.Job.status.in_(["queued", "running", "failed", "needs_reconciliation"]),
        and_(m.Job.status == "succeeded", or_(invalid_text, invalid_probe, invalid_audio)),
    )


def list_jobs(db, owner, mode, *, task_id=None, area=None, bucket=None, cursor=None, limit=20):
    q = select(m.Job).where(m.Job.owner_id == owner, m.Job.data_mode == mode)
    if task_id:
        task = owned(db, m.Task, task_id, owner, mode)
        # Source import/analysis belongs to the pinned task snapshot, never all voice experiments.
        q = q.where(
            or_(
                m.Job.input_json["task_id"].as_string() == task.id,
                m.Job.input_json["snapshot_id"].as_string() == task.snapshot_id,
                m.Job.result_ref["snapshot_id"].as_string() == task.snapshot_id,
            )
        )
    if area == "discover":
        q = q.where(
            m.Job.kind.in_(["import", "analysis", "discovery_search", "discovery_score", "discovery_brief"])
        )
    elif area == "validation":
        q = q.where(
            m.Job.kind.in_(["writing", "voice_enrollment", "voice_preview", "voice_reuse", "voice_delete"])
        )
    if bucket:
        q = q.where(attention_condition() if bucket == "attention" else ~attention_condition())
    if cursor:
        anchor = owned(db, m.Job, cursor, owner, mode)
        q = q.where(
            or_(
                m.Job.created_at < anchor.created_at,
                and_(m.Job.created_at == anchor.created_at, m.Job.id < anchor.id),
            )
        )
    rows = list(db.scalars(q.order_by(m.Job.created_at.desc(), m.Job.id.desc()).limit(limit + 1)))
    return {
        "items": [activity_view(db, j) for j in rows[:limit]],
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


def activity_view(db, job, detail=False):
    data = job_view(job)
    if detail:
        from backend.app.services.cost_reconciliation import CHARGEABLE

        if job.kind in CHARGEABLE:
            data["cost_record_path"] = "/costs/" + job.id
    # Raw failed text and signed inputs never belong in a scrolling summary list.
    refs = {k: v for k, v in (job.result_ref or {}).items() if isinstance(v, str)}
    data["result_ref"] = refs

    def related(model, identity):
        item = db.get(model, identity) if identity else None
        return item if item and item.owner_id == job.owner_id and item.data_mode == job.data_mode else None

    task = related(m.Task, job.input_json.get("task_id"))
    from backend.app.services.task_lifecycle import CLOSED, state, task_job_id

    if task_job_id(job) and state(db, task_job_id(job)) in CLOSED:
        data.update(
            result_ref={},
            title="已移除的创作任务",
            kind_label=LABELS.get(job.kind, job.kind),
            status_label="任务已移入回收站或已清理",
            needs_attention=False,
            task_id=None,
        )
        if detail:
            data.update(
                actions=[],
                guidance="请在任务管理查看恢复期限。原调用与费用记录保留，不会重新发送。",
                billing_note="任务删除不代表调用免费；未知费用仍需按原请求核对。",
                attempts=[
                    {
                        "number": a.attempt_no,
                        "request_id": a.provider_request_id,
                        "usage": a.usage,
                        "model": a.settings_summary.get("model"),
                        "elapsed_ms": a.elapsed_ms,
                        "cost_status": a.cost_status,
                    }
                    for a in db.scalars(select(m.Attempt).where(m.Attempt.job_id == job.id))
                ],
            )
        return data
    task_id = task.id if task else None
    snap_id = (
        job.input_json.get("snapshot_id") or refs.get("snapshot_id") or (task.snapshot_id if task else None)
    )
    snap = related(m.Snapshot, snap_id)
    run = db.scalar(
        select(m.ValidationRun).where(
            m.ValidationRun.job_id == job.id,
            m.ValidationRun.owner_id == job.owner_id,
            m.ValidationRun.data_mode == job.data_mode,
        )
    )
    group = db.scalar(
        select(m.Generation).where(
            m.Generation.job_id == job.id,
            m.Generation.owner_id == job.owner_id,
            m.Generation.data_mode == job.data_mode,
        )
    )
    versions = (
        list(
            db.scalars(
                select(m.TextVersion).where(
                    m.TextVersion.generation_id == group.id,
                    m.TextVersion.owner_id == job.owner_id,
                    m.TextVersion.data_mode == job.data_mode,
                )
            )
        )
        if group
        else []
    )
    audio = db.scalar(
        select(m.AudioVersion).where(
            m.AudioVersion.job_id == job.id,
            m.AudioVersion.owner_id == job.owner_id,
            m.AudioVersion.data_mode == job.data_mode,
        )
    )
    title = snap.payload["metadata"]["full_name"] if snap else None
    if job.kind.startswith("discovery"):
        batch = related(m.Discovery, job.input_json.get("discovery_id"))
        title = batch.query if batch else "工具发现"
    else:
        batch = None
    title = title or (
        "我的声音" if job.kind.startswith("voice") else job.input_json.get("repository_url") or "创作处理"
    )
    status = STATES.get(job.status, job.status)
    attention = job.status in ("queued", "running", "failed", "needs_reconciliation")
    result = (
        {**audio.payload, "asset_id": audio.asset_id}
        if audio
        else group.output
        if group
        else (run.output if run else None)
    )
    from backend.app.services.task_context import confirmed

    if job.status == "succeeded":
        if audio:
            from backend.app.services.audio import audio_view

            state = db.get(m.TaskTextState, audio.task_id)
            view = audio_view(db, audio, state.script_id if state else None)
            status = view["status"]
            attention = not view["duration_valid"]
        elif group:
            if any(not v.payload.get("valid") for v in versions):
                status, attention = "已完成 · 有内容需修正", True
            elif not versions:
                status = "已完成 · 候选已清理"
            elif all(confirmed(db, v) for v in versions):
                status = "已完成 · 稿件已确认"
            else:
                state = db.get(m.TaskTextState, group.task_id)
                current = {state.script_id, state.copy_id} if state else set()
                status = (
                    "已完成 · 待确认" if all(v.id in current for v in versions) else "已完成 · 候选待采用"
                )
        elif run and run.verdict in ("failed", "blocked"):
            status, attention = "调用已完成 · 内容需复核", True
        elif job.kind == "writing":
            status = "实验完成 · 内容待复核"
        elif job.kind in ("voice_preview", "voice_reuse"):
            asset = related(m.Asset, (result or {}).get("asset_id"))
            status = "合成完成 · 可试听" if asset and asset.state == "ready" else "已完成 · 音频不可用"
    data.update(
        title=title,
        kind_label=LABELS.get(job.kind, job.kind),
        status_label=status,
        needs_attention=attention,
        task_id=task_id,
    )
    if not detail:
        return data
    attempts = list(
        db.scalars(select(m.Attempt).where(m.Attempt.job_id == job.id).order_by(m.Attempt.attempt_no))
    )
    data["attempts"] = [
        {
            "number": a.attempt_no,
            "request_id": a.provider_request_id,
            "usage": a.usage,
            "model": a.settings_summary.get("model"),
            "elapsed_ms": a.elapsed_ms,
            "cost_status": a.cost_status,
        }
        for a in attempts
    ]
    data["billing_note"] = (
        "金额待核实；可按请求编号核对供应商记录。" if data["possible_charge"] else "本次未调用收费模型。"
    )
    from backend.app.services.costs import ledger_for, money

    ledger = ledger_for(db, job)
    if ledger:
        state_label = {
            "reserved": "已预占",
            "unknown": "结果/用量待对账，保留预占",
            "priced": "已按目录价核算",
            "released": "未发送，已释放预占",
            "overrun": "用量超出预占，已暂停新增调用",
        }[ledger.status]
        data["billing_note"] = f"{state_label}；原预占 ¥{money(ledger.reserved_micros)}。"
        if ledger.priced_micros is not None:
            data["billing_note"] += f" 用量核算 ¥{money(ledger.priced_micros)}（非云账单）。"
    from backend.app.services.cost_reconciliation import latest

    reconciliation = latest(db, job.id)
    if reconciliation:
        data["billing_note"] += (
            " 最近人工核对已撤回，按保守金额占用。"
            if reconciliation.resolution == "reopened"
            else f" 已按账单核对实际净额 ¥{money(reconciliation.actual_micros)}；原记录保留。"
        )
    if job.data_mode == "mock":
        data["billing_note"] = "模拟处理，未调用真实模型。"
    data["guidance"] = "点击对应结果继续复核；处理完成不等于内容已经确认。"
    if job.status == "needs_reconciliation":
        data["guidance"] = (
            "调用可能已经受理。请先在百炼控制台核对请求记录及账单；缺少请求编号时保留本地记录，结合操作时间核对。不会自动重复调用。"
        )
    elif job.status == "failed":
        code = (job.error or {}).get("code", "")
        if "SAMPLE" in code or "VOICE" in code or "SYNTHESIS" in code:
            data["guidance"] = (
                "先按失败原因检查样本地址、有效期、音色状态或百炼权限。处理后可回到声音设置提交新操作，并重新确认费用。"
            )
        elif job.kind in ("generation", "audio_generation"):
            data["guidance"] = (
                "原稿已保留。可回到文本创作检查资料或修改稿件；需要重新生成时，在页面重新确认本次费用。"
            )
        else:
            data["guidance"] = "请按失败原因修正输入或配置，再从原功能入口提交新操作；不会自动重试。"
    elif job.status == "cancelled":
        data["guidance"] = "已停止采用此操作的后续结果，底层记录保留。供应商已受理的调用仍可能计费。"
    elif job.status in ("queued", "running"):
        data["guidance"] = "可以离开页面，返回后仍能查看进度和结果。停止采用不保证取消供应商已受理的请求。"
    data["actions"] = []
    if audio:
        data["actions"].append(
            {"kind": "audio", "label": "查看本次配音", "task_id": audio.task_id, "id": audio.id}
        )
    elif job.kind == "export" and task:
        data["actions"].append(
            {
                "kind": "export",
                "label": "查看素材包",
                "task_id": task.id,
                "id": job.input_json.get("export_id"),
            }
        )
    elif task:
        data["actions"].append(
            {
                "kind": "task",
                "label": ("查看本次候选" if versions else "查看生成原文")
                if group and group.output
                else "前往文本创作",
                "task_id": task.id,
                "generation_id": group.id if group else None,
            }
        )
    if snap:
        data["actions"].append({"kind": "snapshot", "label": "查看资料", "id": snap.id})
    analysis = related(m.Analysis, refs.get("analysis_id"))
    if analysis:
        data["actions"].append({"kind": "analysis", "label": "查看分析与亮点", "id": analysis.id})
    if job.kind in ("import", "discovery_search", "discovery_score", "discovery_brief"):
        data["actions"].append({"kind": "discover", "label": "前往工具发现"})
    if job.kind.startswith("voice"):
        data["actions"].append(
            {
                "kind": "voice",
                "label": "前往声音设置",
                "id": job.input_json.get("probe_voice_id") or refs.get("probe_voice_id"),
            }
        )
    if result:
        data["result"] = {
            k: v
            for k, v in result.items()
            if k in ("content", "checks", "issues", "raw_text", "duration_ms", "is_test_tone")
        }
        asset_id = result.get("asset_id")
        asset = db.get(m.Asset, asset_id) if asset_id else None
        if (
            asset
            and asset.owner_id == job.owner_id
            and asset.data_mode == job.data_mode
            and asset.state == "ready"
        ):
            data["result"]["asset_id"] = asset.id
        if job.kind == "writing" and "raw_text" not in result:
            data["result"]["raw_note"] = "此历史实验未留存完整原文，已丢弃字段无法恢复。"
    if batch:
        scored = next((r for r in batch.payload.get("score_runs", []) if r.get("job_id") == job.id), None)
        data["discovery"] = {
            "query": batch.query,
            "candidates": [
                {
                    "name": c["metadata"]["full_name"],
                    "url": c["metadata"]["html_url"],
                    "reason": c.get("score", {}).get("reason"),
                }
                for c in (scored or batch.payload).get("recommendations", batch.payload.get("candidates", []))
            ],
            "rejected": (scored or {}).get("rejected", []),
            "raw_text": (scored or {}).get("raw_text"),
        }
    failure = (job.result_ref or {}).get("text_failure")
    if isinstance(failure, dict):
        data["result"] = {"raw_text": failure.get("raw_text"), "raw_output": failure.get("raw_output")}
    return data
