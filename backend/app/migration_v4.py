"""Frozen additive schema for voice display metadata and creator preferences."""

from sqlalchemy import Column, Float, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint

metadata = MetaData()
for name in ("creators", "probe_voices"):
    Table(name, metadata, Column("id", String(36), primary_key=True))


def private():
    return [
        Column("id", String(36), primary_key=True),
        Column("created_at", Float, nullable=False),
        Column("owner_id", String(36), ForeignKey("creators.id"), nullable=False, index=True),
        Column("data_mode", String(8), nullable=False),
    ]


Table(
    "voice_metadata",
    metadata,
    *private(),
    Column("voice_id", String(36), ForeignKey("probe_voices.id"), nullable=False, unique=True),
    Column("display_name", String(60), nullable=False),
    Column("revision", Integer, nullable=False),
)
Table(
    "voice_preferences",
    metadata,
    *private(),
    Column("default_voice_id", String(36), ForeignKey("probe_voices.id")),
    Column("revision", Integer, nullable=False),
    UniqueConstraint("owner_id", "data_mode"),
)
names = ["voice_metadata", "voice_preferences"]
