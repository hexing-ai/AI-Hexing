"""Append-only invoice reconciliation, separate from provider usage."""

from alembic import op

from backend.app.migration_v9 import statements

revision = "0009"
down_revision = "0008"


def upgrade():
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Preserve billing audit; restore a verified backup instead of dropping tables.")
