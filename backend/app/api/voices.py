from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.common import owned
from backend.app.services.voice_library import (
    eligible,
    metadata_for,
    named_voice_view,
    preference,
    preview_for,
)


class NameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    expected_revision: int = Field(ge=0)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称不能为空或包含控制字符")
        return value


class RemoveFromListIn(BaseModel):
    voice_ids: list[str] = Field(min_length=1, max_length=100)
    confirm: Literal[True]


class PreferenceIn(BaseModel):
    default_voice_id: str | None = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)


def install(app, settings, read, mutate, ok):
    mode = settings.provider_mode

    @app.get("/api/v1/voices")
    def listing(request: Request):
        with read(request) as (db, owner, _):
            voices = list(
                db.scalars(
                    select(m.ProbeVoice)
                    .where(
                        m.ProbeVoice.owner_id == owner,
                        m.ProbeVoice.data_mode == mode,
                        ~select(m.VoiceMetadata.id)
                        .where(
                            m.VoiceMetadata.voice_id == m.ProbeVoice.id,
                            m.VoiceMetadata.owner_id == owner,
                            m.VoiceMetadata.data_mode == mode,
                            m.VoiceMetadata.hidden_from_library.is_(True),
                        )
                        .exists(),
                    )
                    .order_by(m.ProbeVoice.created_at.desc())
                )
            )
            pref = preference(db, owner, mode)
            reference_counts = dict(
                db.execute(
                    select(m.AudioVersion.voice_id, func.count(func.distinct(m.AudioVersion.task_id)))
                    .where(m.AudioVersion.owner_id == owner, m.AudioVersion.data_mode == mode)
                    .group_by(m.AudioVersion.voice_id)
                ).all()
            )
            items = [
                {
                    **named_voice_view(db, v, settings),
                    "preview": preview_for(db, v, settings),
                    "referenced_task_count": reference_counts.get(v.id, 0),
                }
                for v in voices
            ]
            return ok(
                request,
                {
                    "items": items,
                    "settings_revision": pref.revision if pref else 0,
                    "default_voice_id": next((v["id"] for v in items if v["is_default"]), None),
                },
            )

    @app.post("/api/v1/voices/list-removals")
    def remove_from_list(request: Request, body: RemoveFromListIn):
        def run(db, owner):
            # A list preference only: never mutate the voice, default, files or provider.
            voices = [owned(db, m.ProbeVoice, id, owner, mode) for id in dict.fromkeys(body.voice_ids)]
            for voice in voices:
                meta = metadata_for(db, voice)
                if meta is None:
                    meta = m.VoiceMetadata(
                        owner_id=owner,
                        data_mode=mode,
                        voice_id=voice.id,
                        display_name=f"我的声音 · {voice.id[:8]}",
                        revision=0,
                    )
                    db.add(meta)
                meta.hidden_from_library = True
            db.flush()
            return {"removed_ids": [v.id for v in voices]}, 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/voices/{id}/metadata")
    def rename(id: str, request: Request, body: NameIn):
        def run(db, owner):
            voice = owned(db, m.ProbeVoice, id, owner, mode)
            meta = metadata_for(db, voice)
            if (meta.revision if meta else 0) != body.expected_revision:
                raise AppError("REVISION_CONFLICT", "声音名称已更新，请刷新后再修改。")
            if meta is None:
                meta = m.VoiceMetadata(
                    owner_id=owner, data_mode=mode, voice_id=id, display_name=body.display_name
                )
                db.add(meta)
            elif meta.display_name != body.display_name:
                meta.display_name = body.display_name
                meta.revision += 1
            db.flush()
            return named_voice_view(db, voice, settings), 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/voice-preferences")
    def set_default(request: Request, body: PreferenceIn):
        def run(db, owner):
            pref = preference(db, owner, mode)
            if (pref.revision if pref else 0) != body.expected_revision:
                raise AppError("REVISION_CONFLICT", "默认声音设置已更新，请刷新后再修改。")
            if body.default_voice_id:
                voice = owned(db, m.ProbeVoice, body.default_voice_id, owner, mode)
                if not eligible(voice, settings):
                    raise AppError(
                        "VOICE_NOT_READY", "只有已确认且与当前服务匹配的可用声音才能设为默认。", 422
                    )
            if pref is None:
                pref = m.VoicePreference(
                    owner_id=owner, data_mode=mode, default_voice_id=body.default_voice_id
                )
                db.add(pref)
            elif pref.default_voice_id != body.default_voice_id:
                pref.default_voice_id = body.default_voice_id
                pref.revision += 1
            db.flush()
            return {"default_voice_id": pref.default_voice_id, "settings_revision": pref.revision}, 200

        return mutate(request, body.model_dump(), run)
