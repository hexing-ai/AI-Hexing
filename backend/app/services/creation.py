"""Text candidates and explicit current-version transitions."""

import json
import time

from sqlalchemy import select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import iso, owned
from backend.app.services.platform_rules import evaluate as platform_check
from backend.app.services.quality import inspect_writing
from backend.app.services.task_context import confirmed, context_id, payload_for


def context_for(db, task):
    snap = db.get(m.Snapshot, task.snapshot_id)
    review = db.get(m.Review, task.review_id)
    analysis = db.get(m.Analysis, review.analysis_id)
    return {
        "tool_name": snap.payload["metadata"]["full_name"].split("/")[-1],
        "fragments": snap.payload["fragments"],
        "metadata": snap.payload["metadata"],
        "missing_fields": snap.payload.get("missing_fields", []),
        "unknowns": analysis.payload.get("unknowns", []),
        "creator_tests": payload_for(db, task)["creator_tests"],
        "context_id": context_id(db, task.id),
        "audience": task.audience,
        "target_duration_ms": [30000, 35000],
        "highlights": [
            {**h, "role": role}
            for hid, role in [
                (review.primary_highlight_id, "primary"),
                (review.secondary_highlight_id, "secondary"),
            ]
            for h in analysis.payload["highlights"]
            if h["id"] == hid
        ],
    }


def check_revision(task, revision):
    if task.revision != revision:
        raise AppError("REVISION_CONFLICT", "任务已有新版本，编辑内容仍保留；请刷新版本后对照保存。")


def text_owned(db, id, task, kind=None):
    version = owned(db, m.TextVersion, id, task.owner_id, task.data_mode)
    if version.task_id != task.id or (kind and version.kind != kind):
        raise AppError("TEXT_REFERENCE_INVALID", "稿件不属于当前任务或类型不匹配。", 422)
    return version


def state_for(db, task):
    state = db.get(m.TaskTextState, task.id)
    if not state:
        state = m.TaskTextState(task_id=task.id)
        db.add(state)
        db.flush()
    return state


def version_view(v, db=None):
    effective = confirmed(db, v) if db else bool(v.confirmed_at)
    current = context_id(db, v.task_id) if db else v.payload.get("context_id")
    stale = v.payload.get("context_id") != current and not effective
    quality = current_quality(db, db.get(m.Task, v.task_id), v) if db and stale else {}
    return dict(
        id=v.id,
        kind=v.kind,
        task_id=v.task_id,
        script_id=v.script_id,
        generation_id=v.generation_id,
        parent_id=v.parent_id,
        **{
            **v.payload,
            **{k: quality[k] for k in ("valid", "issues") if k in quality},
            "platform_limits_saved": v.payload.get("platform_limits"),
            "platform_limits": platform_check(v.payload.get("content"), v.kind),
        },
        context_stale=stale,
        confirmed_at=iso(v.confirmed_at) if effective else None,
        created_at=iso(v.created_at),
    )


def report_for(raw, context, metrics=None):
    metrics = metrics or {}
    return {
        **inspect_writing(raw, context["tool_name"], context["fragments"], context),
        "raw_output": raw,
        "raw_text": metrics.get("raw_text", json.dumps(raw, ensure_ascii=False)),
        "metrics": {k: v for k, v in metrics.items() if k != "raw_text"},
        "prompt_version": metrics.get("prompt_version", "writing-v3"),
        "schema_version": 2,
        "context_id": context.get("context_id"),
    }


def part_payload(report, kind):
    relevant = {"script": {"evidence_map.feature", "evidence_map.example"}, "copy": {"evidence_map.copy"}}[
        kind
    ]
    issues = [
        i
        for i in report["issues"]
        if i["path"].startswith("$") or i["path"].startswith(kind) or i["path"] in relevant
    ]
    valid = report["checks"][kind] and not any(i["severity"] == "error" for i in issues)
    content = report["content"].get(kind)
    return {
        "content": content,
        "context_id": report.get("context_id"),
        "valid": valid,
        "issues": issues,
        "evidence_map": report["content"].get("evidence_map", {}),
        "schema_version": 2,
        "template_version": "C-09-v1",
        "character_count": len("".join(content.values())) if kind == "script" and content else None,
        "duration_note": "字数仅供参考，实际 30–35 秒需在配音后验证。",
        "platform_limits": platform_check(content, kind),
    }


def save_generation(db, job, result):
    group = db.scalar(select(m.Generation).where(m.Generation.job_id == job.id))
    raw = result["raw_output"]
    effective = raw
    context = result["quality_context"]
    section = job.input_json.get("rewrite_section", "all")
    if section != "all" and isinstance(raw, dict) and isinstance(raw.get("script"), dict):
        base = context.get("source_script") or {}
        evidence = dict(context.get("source_evidence_map") or {})
        if section in ("feature", "example"):
            evidence[section] = (
                (raw.get("evidence_map") or {}).get(section)
                if isinstance(raw.get("evidence_map"), dict)
                else None
            )
        effective = {**raw, "script": {**base, section: raw["script"].get(section)}, "evidence_map": evidence}
    report = report_for(effective, context, result.get("metrics"))
    report["raw_output"] = raw
    report["raw_text"] = result.get("metrics", {}).get("raw_text", json.dumps(raw, ensure_ascii=False))
    report["rewrite_section"] = section
    report["context_id"] = context.get("context_id")
    group.output = report
    mode = job.input_json["mode"]
    script_id = job.input_json.get("base_script_id")
    refs = {"generation_id": group.id}
    if mode != "copy":
        script = m.TextVersion(
            owner_id=job.owner_id,
            data_mode=job.data_mode,
            task_id=group.task_id,
            kind="script",
            generation_id=group.id,
            parent_id=script_id,
            payload=part_payload(report, "script"),
        )
        db.add(script)
        db.flush()
        script_id = script.id
        refs["script_id"] = script.id
    if mode != "script":
        copy = m.TextVersion(
            owner_id=job.owner_id,
            data_mode=job.data_mode,
            task_id=group.task_id,
            kind="copy",
            generation_id=group.id,
            script_id=script_id,
            payload=part_payload(report, "copy"),
        )
        db.add(copy)
        db.flush()
        refs["copy_id"] = copy.id
    # No current pointer is changed here, even if revision is unchanged.
    job.result_ref = refs


def edit_text(db, task, body):
    check_revision(task, body.expected_revision)
    if body.parent_id:
        text_owned(db, body.parent_id, task, body.kind)
    context = context_for(db, task)
    raw = {body.kind: body.content, "evidence_map": body.evidence_map}
    if body.kind == "copy":
        if not body.script_id:
            raise AppError("SCRIPT_REQUIRED", "文案必须关联一份口播稿。", 422)
        script = text_owned(db, body.script_id, task, "script")
        raw["script"] = script.payload["content"]
    payload = part_payload(report_for(raw, context), body.kind)
    # Repeated saves of the current content do not invalidate confirmation or references.
    state = state_for(db, task)
    current_id = getattr(state, body.kind + "_id")
    current = db.get(m.TextVersion, current_id) if current_id else None
    content = payload["content"] if payload["content"] is not None else body.content
    script_id = body.script_id if body.kind == "copy" else None
    if current and (
        current.payload["content"] == content
        and current.payload.get("evidence_map", {}) == payload["evidence_map"]
        and current.script_id == script_id
    ):
        return current, False
    # Invalid drafts are saved too, but can never be confirmed.
    version = m.TextVersion(
        owner_id=task.owner_id,
        data_mode=task.data_mode,
        task_id=task.id,
        kind=body.kind,
        parent_id=body.parent_id,
        script_id=body.script_id if body.kind == "copy" else None,
        payload=payload,
    )
    # Preserve invalid manual content, allowing correction without losing edits.
    version.payload = {
        **payload,
        "content": payload["content"] if payload["content"] is not None else body.content,
    }
    db.add(version)
    db.flush()
    state = state_for(db, task)
    setattr(state, body.kind + "_id", version.id)
    task.revision += 1
    return version, True


def current_quality(db, task, version):
    raw = {version.kind: version.payload["content"], "evidence_map": version.payload.get("evidence_map", {})}
    if version.kind == "copy" and version.script_id:
        script = db.get(m.TextVersion, version.script_id)
        if script:
            raw["script"] = script.payload["content"]
    return part_payload(report_for(raw, context_for(db, task)), version.kind)


def transition(db, task, version, revision, confirm=False):
    check_revision(task, revision)
    state = state_for(db, task)
    quality = current_quality(db, task, version)
    if not quality["valid"]:
        raise AppError("TEXT_QUALITY_INVALID", "此版本仍有检查问题，请先编辑修正。")
    if confirm:
        if getattr(state, version.kind + "_id") != version.id:
            raise AppError("TEXT_NOT_CURRENT", "请先采用此候选，再确认当前版本。")
        if version.kind == "copy":
            script = db.get(m.TextVersion, state.script_id) if state.script_id else None
            if not script or not confirmed(db, script) or version.script_id != script.id:
                raise AppError("COPY_STALE", "请先确认当前口播，再对照更新文案的关联稿件。")
        version.confirmed_at = time.time()
        version.payload = {
            **version.payload,
            "valid": quality["valid"],
            "issues": quality["issues"],
            "confirmed_context_id": context_id(db, task.id),
        }
        db.add(
            m.EvidenceEvent(
                owner_id=task.owner_id,
                data_mode=task.data_mode,
                event_type="text_facts_confirmed",
                object_id=version.id,
                details={"task_id": task.id, "revision": revision, "context_id": context_id(db, task.id)},
            )
        )
    else:
        setattr(state, version.kind + "_id", version.id)
    task.revision += 1
