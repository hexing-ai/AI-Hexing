from sqlalchemy import select

from backend.app import models as m
from backend.app.services.assets import asset_path
from backend.app.services.providers import Providers
from backend.app.services.views import voice_view


def preference(db, owner, mode):
    return db.scalar(
        select(m.VoicePreference).where(
            m.VoicePreference.owner_id == owner, m.VoicePreference.data_mode == mode
        )
    )


def metadata_for(db, voice):
    return db.scalar(
        select(m.VoiceMetadata).where(
            m.VoiceMetadata.voice_id == voice.id,
            m.VoiceMetadata.owner_id == voice.owner_id,
            m.VoiceMetadata.data_mode == voice.data_mode,
        )
    )


def eligible(voice, settings):
    return bool(
        voice.status == "ready"
        and voice.preview_confirmed_at
        and voice.provider_scope == Providers(settings).scope()
    )


def clear_default(db, voice):
    pref = preference(db, voice.owner_id, voice.data_mode)
    if pref and pref.default_voice_id == voice.id:
        pref.default_voice_id = None
        pref.revision += 1


def named_voice_view(db, voice, settings):
    meta = metadata_for(db, voice)
    pref = preference(db, voice.owner_id, voice.data_mode)
    return {
        **voice_view(voice),
        "display_name": meta.display_name if meta else f"我的声音 · {voice.id[:8]}",
        "metadata_revision": meta.revision if meta else 0,
        "eligible": eligible(voice, settings),
        "is_default": bool(pref and pref.default_voice_id == voice.id and eligible(voice, settings)),
    }


def preview_for(db, voice, settings):
    # Prefer the newest formal result; even an out-of-duration draft can be auditioned.
    candidates = [
        (a.asset_id, "完整配音")
        for a in db.scalars(
            select(m.AudioVersion)
            .where(
                m.AudioVersion.voice_id == voice.id,
                m.AudioVersion.owner_id == voice.owner_id,
                m.AudioVersion.data_mode == voice.data_mode,
            )
            .order_by(m.AudioVersion.created_at.desc())
        )
        if a.asset_id
    ]
    for run in db.scalars(
        select(m.ValidationRun)
        .where(
            m.ValidationRun.owner_id == voice.owner_id,
            m.ValidationRun.data_mode == voice.data_mode,
            m.ValidationRun.kind.in_(["voice_preview", "voice_reuse"]),
        )
        .order_by(m.ValidationRun.created_at.desc())
    ):
        if run.input_ref.get("probe_voice_id") == voice.id and run.output and run.output.get("asset_id"):
            candidates.append((run.output["asset_id"], "短句试听"))
    for id, kind in candidates:
        asset = db.get(m.Asset, id)
        if (
            asset
            and asset.owner_id == voice.owner_id
            and asset.data_mode == voice.data_mode
            and asset.state == "ready"
            and asset_path(settings, asset.storage_key).is_file()
        ):
            return {
                "download_path": f"/api/v1/assets/{asset.id}/content",
                "duration_ms": asset.duration_ms,
                "kind": kind,
            }
    return None
