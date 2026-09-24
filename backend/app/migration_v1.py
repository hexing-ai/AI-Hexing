# Frozen S1 schema; do not update when current models change.
"""Relational identities and immutable product snapshots. JSON carries validated payloads."""

import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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


metadata = Base.metadata
