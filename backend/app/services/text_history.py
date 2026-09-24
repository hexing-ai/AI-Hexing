"""Explicit, previewed history pruning. Content in surviving versions is unchanged."""

import json

from sqlalchemy import delete, select, update

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import digest
from backend.app.services.creation import check_revision


def cleanup_preview(db, task):
    versions = list(db.scalars(select(m.TextVersion).where(m.TextVersion.task_id == task.id)))
    by_id = {v.id: v for v in versions}
    reasons = {}

    def keep(id, reason):
        if id in by_id:
            reasons.setdefault(id, set()).add(reason)

    state = db.get(m.TaskTextState, task.id)
    if state:
        keep(state.script_id, "当前口播")
        keep(state.copy_id, "当前文案")
    for v in versions:
        if v.confirmed_at:
            keep(v.id, "已确认版本")
        if not v.payload.get("valid", False):
            keep(v.id, "失败记录")
    for script_id in db.scalars(select(m.AudioVersion.script_id).where(m.AudioVersion.task_id == task.id)):
        keep(script_id, "正式音频关联")
    for script_id, copy_id in db.execute(
        select(m.ExportManifest.script_id, m.ExportManifest.copy_id).where(
            m.ExportManifest.task_id == task.id
        )
    ):
        keep(script_id, "导出记录关联")
        keep(copy_id, "导出记录关联")
    # Active generation/reconciliation may still create a child or a linked copy.
    jobs = db.scalars(
        select(m.Job).where(
            m.Job.owner_id == task.owner_id,
            m.Job.data_mode == task.data_mode,
            m.Job.status.in_(["queued", "running", "needs_reconciliation"]),
        )
    )
    for job in jobs:
        if job.input_json.get("task_id") == task.id:
            keep(job.input_json.get("base_script_id"), "在途作业引用")
            keep(job.input_json.get("script_id"), "在途配音引用")
    # Defensive: preserve unexpected external references rather than breaking other tasks.
    for parent_id, script_id in db.execute(
        select(m.TextVersion.parent_id, m.TextVersion.script_id).where(m.TextVersion.task_id != task.id)
    ):
        keep(parent_id, "其他记录引用")
        keep(script_id, "其他记录引用")
    for script_id, copy_id in db.execute(
        select(m.TaskTextState.script_id, m.TaskTextState.copy_id).where(m.TaskTextState.task_id != task.id)
    ):
        keep(script_id, "其他记录引用")
        keep(copy_id, "其他记录引用")
    # Only retained copies keep their source scripts. Unused pairs can be pruned together.
    for id in list(reasons):
        keep(by_id[id].script_id, "保留文案关联的口播")
    removable = sorted(by_id.keys() - reasons.keys())
    protected = [{"id": id, "reasons": sorted(values)} for id, values in sorted(reasons.items())]
    return {
        "task_id": task.id,
        "expected_revision": task.revision,
        "preview_token": digest(
            {"task": task.id, "revision": task.revision, "remove": removable, "protected": protected}
        ),
        "removable_ids": removable,
        "removable_count": len(removable),
        "protected_count": len(protected),
        "protected": protected,
        "content_bytes": sum(
            len(json.dumps(by_id[id].payload, ensure_ascii=False).encode()) for id in removable
        ),
        "note": "保留当前、已确认、失败及被文案、音频、导出或在途作业引用的版本。生成原文与用量记录保留。",
    }


def cleanup_history(db, task, body):
    check_revision(task, body.expected_revision)
    preview = cleanup_preview(db, task)
    if preview["preview_token"] != body.preview_token:
        raise AppError("CLEANUP_PREVIEW_CHANGED", "可清理范围已变化，请重新预览后确认。")
    ids = preview["removable_ids"]
    if not ids:
        return {"deleted_count": 0, "revision": task.revision}
    # parent_id is lineage, not a content dependency. Explicitly record pruning,
    # never pretend the surviving version was based on a different ancestor.
    pruned = list(
        db.execute(
            select(m.TextVersion.id, m.TextVersion.parent_id).where(
                m.TextVersion.task_id == task.id,
                m.TextVersion.parent_id.in_(ids),
                m.TextVersion.id.not_in(ids),
            )
        )
    )
    db.execute(
        update(m.TextVersion)
        .where(m.TextVersion.task_id == task.id, m.TextVersion.parent_id.in_(ids))
        .values(parent_id=None)
    )
    db.execute(delete(m.TextVersion).where(m.TextVersion.task_id == task.id, m.TextVersion.id.in_(ids)))
    task.revision += 1
    db.add(
        m.EvidenceEvent(
            owner_id=task.owner_id,
            data_mode=task.data_mode,
            event_type="text_history_cleaned",
            object_id=task.id,
            details={
                "deleted_ids": ids,
                "pruned_parents": [list(row) for row in pruned],
                "content_bytes": preview["content_bytes"],
                "rule_version": "text-cleanup-v1",
            },
        )
    )
    return {"deleted_count": len(ids), "revision": task.revision}
