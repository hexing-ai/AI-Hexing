"""Frozen additive budget quote and reservation schema."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table

metadata = MetaData()
for name in ("creators", "cost_previews", "jobs"):
    Table(name, metadata, Column("id", String(36), primary_key=True))


def private():
    return [
        Column("id", String(36), primary_key=True),
        Column("created_at", Float, nullable=False),
        Column("owner_id", String(36), ForeignKey("creators.id"), nullable=False, index=True),
        Column("data_mode", String(8), nullable=False),
    ]


Table(
    "cost_quotes",
    metadata,
    *private(),
    Column("preview_id", String(36), ForeignKey("cost_previews.id"), nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
)
Table(
    "cost_ledger",
    metadata,
    *private(),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("quote_id", String(36), ForeignKey("cost_quotes.id"), nullable=False),
    Column("month", String(7), nullable=False),
    Column("reserved_micros", Integer, nullable=False),
    Column("priced_micros", Integer),
    Column("status", String(24), nullable=False),
    Column("updated_at", Float, nullable=False),
)
names = ["cost_quotes", "cost_ledger"]
