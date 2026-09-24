"""Frozen S2 schema, never import runtime models in historical migrations."""

from sqlalchemy import JSON, Column, Float, ForeignKey, MetaData, String, Table, Text

metadata = MetaData()
for name in ("creators", "creation_tasks", "jobs"):
    Table(name, metadata, Column("id", String(36), primary_key=True))


def private():
    return [
        Column("id", String(36), primary_key=True),
        Column("created_at", Float, nullable=False),
        Column("owner_id", String(36), ForeignKey("creators.id"), nullable=False, index=True),
        Column("data_mode", String(8), nullable=False),
    ]


Table(
    "discovery_batches",
    metadata,
    *private(),
    Column("query", Text, nullable=False),
    Column("payload", JSON, nullable=False),
)
Table(
    "generation_groups",
    metadata,
    *private(),
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), nullable=False, index=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("output", JSON),
)
Table(
    "text_versions",
    metadata,
    *private(),
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), nullable=False, index=True),
    Column("kind", String(10), nullable=False),
    Column("generation_id", String(36), ForeignKey("generation_groups.id")),
    Column("parent_id", String(36), ForeignKey("text_versions.id")),
    Column("script_id", String(36), ForeignKey("text_versions.id")),
    Column("payload", JSON, nullable=False),
    Column("confirmed_at", Float),
)
Table(
    "task_text_states",
    metadata,
    Column("task_id", String(36), ForeignKey("creation_tasks.id"), primary_key=True),
    Column("script_id", String(36), ForeignKey("text_versions.id")),
    Column("copy_id", String(36), ForeignKey("text_versions.id")),
)
names = ["discovery_batches", "generation_groups", "text_versions", "task_text_states"]
