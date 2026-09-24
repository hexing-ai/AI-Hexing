"""Frozen S3 additive schema."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table

metadata = MetaData()
for name in ("creators", "creation_tasks", "jobs", "assets", "text_versions", "probe_voices"):
    Table(name, metadata, Column("id", String(36), primary_key=True))


def private():
    return [
        Column("id", String(36), primary_key=True),
        Column("created_at", Float, nullable=False),
        Column("owner_id", String(36), ForeignKey("creators.id"), nullable=False, index=True),
        Column("data_mode", String(8), nullable=False),
    ]


Table(
    "audio_versions",
    metadata,
    *private(),
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), nullable=False, index=True),
    Column("script_id", String(36), ForeignKey("text_versions.id"), nullable=False),
    Column("voice_id", String(36), ForeignKey("probe_voices.id"), nullable=False),
    Column("voice_revision", Integer, nullable=False),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("asset_id", String(36), ForeignKey("assets.id")),
    Column("payload", JSON, nullable=False),
    Column("confirmed_at", Float),
)
Table(
    "task_audio_states",
    metadata,
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), primary_key=True),
    Column("audio_id", String(36), ForeignKey("audio_versions.id")),
)
Table(
    "export_manifests",
    metadata,
    *private(),
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), nullable=False, index=True),
    Column("script_id", String(36), ForeignKey("text_versions.id")),
    Column("copy_id", String(36), ForeignKey("text_versions.id")),
    Column("audio_id", String(36), ForeignKey("audio_versions.id")),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False),
    Column("asset_id", String(36), ForeignKey("assets.id")),
    Column("payload", JSON, nullable=False),
)
names = ["audio_versions", "task_audio_states", "export_manifests"]
