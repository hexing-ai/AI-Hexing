from backend.app.services.common import iso


def task_view(t, db=None, settings=None):
    from backend.app import models as m
    from backend.app.services.creation import version_view
    from backend.app.services.task_lifecycle import lifecycle_view, task_summary
    from backend.app.services.task_lifecycle import state as lifecycle_state

    if db and lifecycle_state(db, t.id) in ("trashed", "purged"):
        return task_summary(db, t)

    state = db.get(m.TaskTextState, t.id) if db else None
    script = db.get(m.TextVersion, state.script_id) if state and state.script_id else None
    copy = db.get(m.TextVersion, state.copy_id) if state and state.copy_id else None
    from backend.app.services.audio import material_state

    materials, _, _, _ = (
        material_state(db, t, settings)
        if db
        else ({"ready": False, "missing": [], "audio": None}, None, None, None)
    )
    from backend.app.services.task_context import confirmed, context_id

    return dict(
        context_id=context_id(db, t.id) if db else None,
        id=t.id,
        tool_id=t.tool_id,
        snapshot_id=t.snapshot_id,
        review_id=t.review_id,
        audience=t.audience,
        platform="douyin",
        target_duration_ms=[30000, 35000],
        revision=t.revision,
        status=("mock_ready" if t.data_mode == "mock" else "ready") if materials["ready"] else "in_progress",
        materials_ready=materials["ready"],
        audio_stale=bool(materials["audio"] and materials["audio"]["stale"]),
        missing_artifacts=[
            k for k, v in [("script", script), ("audio", materials["audio"]), ("copy", copy)] if not v
        ],
        current_script=version_view(script, db) if script else None,
        current_audio=materials["audio"],
        current_copy=version_view(copy, db) if copy else None,
        copy_stale=bool(copy and (not script or copy.script_id != script.id or not confirmed(db, copy))),
        data_mode=t.data_mode,
        lifecycle=lifecycle_view(db, t) if db else {"state": "active"},
    )


def asset_view(a):
    return dict(
        id=a.id,
        kind=a.kind,
        mime=a.mime,
        byte_size=a.byte_size,
        duration_ms=a.duration_ms,
        state=a.state,
        cleanup_after=iso(a.cleanup_after),
        consent_record_id=a.consent_record_id,
        download_path=f"/api/v1/assets/{a.id}/content",
        data_mode=a.data_mode,
    )


def voice_view(v):
    return dict(
        id=v.id,
        status=v.status,
        revision=v.revision,
        region=v.provider_scope.get("region"),
        target_model=v.provider_scope.get("model"),
        preview_confirmed_at=iso(v.preview_confirmed_at),
        cleanup_state=v.status if "delet" in v.status else "not_requested",
        data_mode=v.data_mode,
    )


def job_view(j):
    return dict(
        id=j.id,
        kind=j.kind,
        status=j.status,
        phase=j.phase,
        result_ref=j.result_ref,
        error=j.error,
        retryable=False,
        created_at=iso(j.created_at),
        updated_at=iso(j.updated_at),
        possible_charge=j.data_mode == "live" and j.kind not in ("import", "discovery_search", "export"),
        mode=j.data_mode,
    )


def validation_view(v):
    return dict(
        id=v.id,
        kind=v.kind,
        job_id=v.job_id,
        mode=v.data_mode,
        contract_version=v.contract_version,
        status=v.verdict,
        output=v.output,
        human_verdict=v.human_verdict,
    )
