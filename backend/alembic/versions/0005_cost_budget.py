"""Versioned price quotes and atomic budget reservations."""

from alembic import op

from backend.app.migration_v5 import metadata, names

revision = "0005"
down_revision = "0004"


def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables[n] for n in names])


def downgrade():
    metadata.drop_all(op.get_bind(), tables=[metadata.tables[n] for n in names])
