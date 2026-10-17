"""Relational identities and immutable product snapshots. JSON carries validated payloads."""

import time
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Private(Identity):
    owner_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), index=True)
    data_mode: Mapped[str] = mapped_column(String(8))


class Creator(Identity, Base):
    __tablename__ = "creators"
    login_name: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Identity, Base):
    __tablename__ = "sessions"
    owner_id: Mapped[str] = mapped_column(ForeignKey("creators.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(8), default="owner", server_default="owner")
    invite_id: Mapped[str | None] = mapped_column(ForeignKey("invites.id"), nullable=True)


class Invite(Identity, Base):
    """Multi-use code granting read-only guest sessions on the owner's workspace."""

    __tablename__ = "invites"
    owner_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class LoginAttempt(Identity, Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), index=True)


class Tool(Private, Base):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("owner_id", "data_mode", "github_repo_id"),)
    github_repo_id: Mapped[int] = mapped_column(Integer)
    canonical_url: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(300))
    metadata_json: Mapped[dict] = mapped_column(JSON)


class ToolAlias(Private, Base):
    __tablename__ = "tool_aliases"
    __table_args__ = (UniqueConstraint("owner_id", "data_mode", "url"),)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"))
    url: Mapped[str] = mapped_column(Text)


class PublicEntry(Private, Base):
    __tablename__ = "public_entries"
    __table_args__ = (
        UniqueConstraint("data_mode", "github_repo_id"),
        UniqueConstraint("data_mode", "tool_id"),
        CheckConstraint("state IN ('draft','published','withdrawn')"),
        CheckConstraint("state != 'published' OR published_revision_id IS NOT NULL"),
        ForeignKeyConstraint(
            ["id", "draft_revision_id"], ["public_revisions.entry_id", "public_revisions.id"]
        ),
        ForeignKeyConstraint(
            ["id", "published_revision_id"], ["public_revisions.entry_id", "public_revisions.id"]
        ),
    )
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"))
    github_repo_id: Mapped[int] = mapped_column(Integer)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, default=uid)
    state: Mapped[str] = mapped_column(String(16), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    draft_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    first_published_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    withdrawn_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class PublicRevision(Identity, Base):
    __tablename__ = "public_revisions"
    __table_args__ = (UniqueConstraint("entry_id", "version"), UniqueConstraint("entry_id", "id"))
    entry_id: Mapped[str] = mapped_column(ForeignKey("public_entries.id"))
    version: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("creators.id"), nullable=True)
    published_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class PublicEntryEvent(Identity, Base):
    __tablename__ = "public_entry_events"
    __table_args__ = (CheckConstraint("kind IN ('publish','update','withdraw')"),)
    entry_id: Mapped[str] = mapped_column(ForeignKey("public_entries.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("public_revisions.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("creators.id"))
    kind: Mapped[str] = mapped_column(String(16))


class PublicInteractionEvent(Identity, Base):
    __tablename__ = "public_interaction_events"
    __table_args__ = (CheckConstraint("kind IN ('intro_open','github_click')"),)
    event_id: Mapped[str] = mapped_column(String(36), unique=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("public_entries.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("public_revisions.id"))
    kind: Mapped[str] = mapped_column(String(16))


class Snapshot(Private, Base):
    __tablename__ = "source_snapshots"
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"))
    source_sha: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class Analysis(Private, Base):
    __tablename__ = "analyses"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    context_hash: Mapped[str] = mapped_column(String(64), index=True)
    prompt_version: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class Review(Private, Base):
    __tablename__ = "reviews"
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    primary_highlight_id: Mapped[str] = mapped_column(String(100))
    secondary_highlight_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Task(Private, Base):
    __tablename__ = "creation_tasks"
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    review_id: Mapped[str] = mapped_column(ForeignKey("reviews.id"))
    audience: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class TaskContextVersion(Private, Base):
    __tablename__ = "task_context_versions"
    __table_args__ = (UniqueConstraint("task_id", "revision"),)
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class TaskLifecycle(Base):
    __tablename__ = "task_lifecycles"
    __table_args__ = (
        CheckConstraint("state IN ('active','archived','trashed','purged')"),
        CheckConstraint("previous_state IN ('active','archived')"),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    previous_state: Mapped[str] = mapped_column(String(16), default="active")
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    purge_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    purged_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    cleanup_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cleanup_checked_at: Mapped[float] = mapped_column(Float, default=0)


class TaskAssetLink(Base):
    __tablename__ = "task_asset_links"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)


class Operation(Private, Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("owner_id", "data_mode", "scope", "key"),)
    scope: Mapped[str] = mapped_column(String(300))
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    status_code: Mapped[int] = mapped_column(Integer)


class CostPreview(Private, Base):
    __tablename__ = "cost_previews"
    action: Mapped[str] = mapped_column(String(60))
    input_hash: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float)


class CostAuthorization(Private, Base):
    __tablename__ = "cost_authorizations"
    preview_id: Mapped[str] = mapped_column(ForeignKey("cost_previews.id"), unique=True)
    action: Mapped[str] = mapped_column(String(60))
    input_hash: Mapped[str] = mapped_column(String(64))
    max_calls: Mapped[int] = mapped_column(Integer)
    used_calls: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Job(Private, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(60))
    input_json: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(60), default="queued")
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    result_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    authorization_id: Mapped[str | None] = mapped_column(ForeignKey("cost_authorizations.id"), nullable=True)


class Attempt(Identity, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no"),)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    dispatch_state: Mapped[str] = mapped_column(String(20), default="not_sent")
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    settings_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_status: Mapped[str] = mapped_column(String(20), default="unknown")
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class Asset(Private, Base):
    __tablename__ = "assets"
    kind: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(80))
    byte_size: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(String(40), default="ready")
    cleanup_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    consent_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class VoiceMetadata(Private, Base):
    __tablename__ = "voice_metadata"
    voice_id: Mapped[str] = mapped_column(ForeignKey("probe_voices.id"), unique=True)
    display_name: Mapped[str] = mapped_column(String(60))
    hidden_from_library: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class VoicePreference(Private, Base):
    __tablename__ = "voice_preferences"
    __table_args__ = (UniqueConstraint("owner_id", "data_mode"),)
    default_voice_id: Mapped[str | None] = mapped_column(ForeignKey("probe_voices.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ProbeVoice(Private, Base):
    __tablename__ = "probe_voices"
    provider_voice_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    provider_scope: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="cloning")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    preview_confirmed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ValidationRun(Private, Base):
    __tablename__ = "validation_runs"
    kind: Mapped[str] = mapped_column(String(50))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    contract_version: Mapped[str] = mapped_column(String(100))
    input_ref: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verdict: Mapped[str] = mapped_column(String(30), default="not_run")
    human_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)


class EvidenceEvent(Private, Base):
    __tablename__ = "evidence_events"
    event_type: Mapped[str] = mapped_column(String(60))
    object_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON)


class Discovery(Private, Base):
    __tablename__ = "discovery_batches"
    query: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Generation(Private, Base):
    __tablename__ = "generation_groups"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TextVersion(Private, Base):
    __tablename__ = "text_versions"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    generation_id: Mapped[str | None] = mapped_column(ForeignKey("generation_groups.id"), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    confirmed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskTextState(Base):
    __tablename__ = "task_text_states"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), primary_key=True)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)
    copy_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)


class AudioVersion(Private, Base):
    __tablename__ = "audio_versions"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), index=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("text_versions.id"))
    voice_id: Mapped[str] = mapped_column(ForeignKey("probe_voices.id"))
    voice_revision: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    confirmed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskAudioState(Base):
    __tablename__ = "task_audio_states"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), primary_key=True)
    audio_id: Mapped[str | None] = mapped_column(ForeignKey("audio_versions.id"), nullable=True)


class ExportManifest(Private, Base):
    __tablename__ = "export_manifests"
    task_id: Mapped[str] = mapped_column(ForeignKey("creation_tasks.id"), index=True)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)
    copy_id: Mapped[str | None] = mapped_column(ForeignKey("text_versions.id"), nullable=True)
    audio_id: Mapped[str | None] = mapped_column(ForeignKey("audio_versions.id"), nullable=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CostQuote(Private, Base):
    __tablename__ = "cost_quotes"
    preview_id: Mapped[str] = mapped_column(ForeignKey("cost_previews.id"), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CostLedger(Private, Base):
    __tablename__ = "cost_ledger"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("cost_quotes.id"))
    month: Mapped[str] = mapped_column(String(7))
    reserved_micros: Mapped[int] = mapped_column(Integer)
    priced_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class CostReconciliation(Private, Base):
    __tablename__ = "cost_reconciliations"
    __table_args__ = (
        UniqueConstraint("job_id", "revision"),
        CheckConstraint("resolution IN ('billed','not_charged','reopened')"),
        CheckConstraint("actual_micros IS NULL OR actual_micros >= 0"),
        CheckConstraint("hold_micros >= 0"),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("cost_reconciliations.id"), nullable=True)
    resolution: Mapped[str] = mapped_column(String(20))
    actual_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hold_micros: Mapped[int] = mapped_column(Integer, default=0)
    budget_month: Mapped[str] = mapped_column(String(7))
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
