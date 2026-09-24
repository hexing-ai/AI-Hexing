"""Versioned private sources and context-bound human confirmations."""

import time

from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import digest, iso, owned


def latest(db, task_id):
    return db.scalar(
        select(m.TaskContextVersion)
        .where(m.TaskContextVersion.task_id == task_id)
        .order_by(m.TaskContextVersion.revision.desc())
        .limit(1)
    )


def context_id(db, task_id):
    row = latest(db, task_id)
    return row.id if row else None


def confirmed(db, version):
    return bool(
        version
        and version.confirmed_at
        and version.payload.get("confirmed_context_id") == context_id(db, version.task_id)
    )


def payload_for(db, task):
    row = latest(db, task.id)
    if row:
        return row.payload
    review = db.get(m.Review, task.review_id)
    return dict(
        snapshot_id=task.snapshot_id,
        review_id=task.review_id,
        analysis_id=review.analysis_id,
        audience=task.audience,
        primary_highlight_id=review.primary_highlight_id,
        secondary_highlight_id=review.secondary_highlight_id,
        creator_tests=[],
    )


def view(db, task):
    row = latest(db, task.id)
    return {
        "id": row.id if row else None,
        "revision": task.revision,
        "created_at": iso(row.created_at) if row else iso(task.created_at),
        **payload_for(db, task),
    }


def change(db, task, body):
    from backend.app.services import costs
    from backend.app.services.creation import check_revision
    from backend.app.services.task_lifecycle import task_jobs

    check_revision(task, body.expected_revision)
    analysis = owned(db, m.Analysis, body.analysis_id, task.owner_id, task.data_mode)
    snap = owned(db, m.Snapshot, analysis.snapshot_id, task.owner_id, task.data_mode)
    if snap.tool_id != task.tool_id:
        raise AppError("TASK_TOOL_MISMATCH", "换工具请新建任务，当前任务只能采用同一工具的资料。", 422)
    refs = {f["id"] for f in snap.payload["fragments"]}
    valid = {
        h["id"]
        for h in analysis.payload["highlights"]
        if h.get("evidence_refs") and set(h["evidence_refs"]) <= refs
    }
    if body.primary_highlight_id not in valid or (
        body.secondary_highlight_id
        and (
            body.secondary_highlight_id not in valid
            or body.secondary_highlight_id == body.primary_highlight_id
        )
    ):
        raise AppError("HIGHLIGHT_INVALID", "请选择有有效来源且互不重复的主辅亮点。", 422)
    notes = []
    for note in body.creator_tests:
        if note.snapshot_id != snap.id:
            raise AppError("TEST_SNAPSHOT_MISMATCH", "实测记录对应旧资料，请移除或按新资料重新录入。", 422)
        record = note.model_dump(mode="json")
        notes.append({**record, "id": "test-" + digest(record)[:24], "source_type": "creator_test"})
    if len({n["id"] for n in notes}) != len(notes):
        raise AppError("TEST_DUPLICATE", "请移除重复的实测记录。", 422)
    candidate = dict(
        snapshot_id=snap.id,
        analysis_id=analysis.id,
        audience=body.audience,
        primary_highlight_id=body.primary_highlight_id,
        secondary_highlight_id=body.secondary_highlight_id,
        creator_tests=notes,
    )
    previous = payload_for(db, task)
    if candidate == {k: v for k, v in previous.items() if k != "review_id"}:
        return False
    # Preserve the initial selection too, before switching the task's current pointers.
    if not latest(db, task.id):
        db.add(
            m.TaskContextVersion(
                owner_id=task.owner_id,
                data_mode=task.data_mode,
                task_id=task.id,
                revision=0,
                payload=previous,
            )
        )
    review = m.Review(
        owner_id=task.owner_id,
        data_mode=task.data_mode,
        analysis_id=analysis.id,
        primary_highlight_id=body.primary_highlight_id,
        secondary_highlight_id=body.secondary_highlight_id,
    )
    db.add(review)
    db.flush()
    task.snapshot_id, task.review_id, task.audience = snap.id, review.id, body.audience
    task.revision += 1
    row = m.TaskContextVersion(
        owner_id=task.owner_id,
        data_mode=task.data_mode,
        task_id=task.id,
        revision=task.revision,
        payload={**candidate, "review_id": review.id},
    )
    db.add(row)
    db.flush()
    # prepare() commits dispatching before any call; never release an ambiguous request.
    for job in task_jobs(db, task):
        if job.kind not in ("generation", "writing", "audio_generation") or job.status not in (
            "queued",
            "running",
        ):
            continue
        attempts = list(db.scalars(select(m.Attempt).where(m.Attempt.job_id == job.id)))
        if any(a.dispatch_state != "not_sent" for a in attempts):
            continue
        costs.stop(db, job)
        job.status, job.phase, job.updated_at = "cancelled", "task_context_changed", time.time()
    db.add(
        m.EvidenceEvent(
            owner_id=task.owner_id,
            data_mode=task.data_mode,
            event_type="task_context_changed",
            object_id=task.id,
            details={"context_id": row.id, "revision": task.revision},
        )
    )
    return True
