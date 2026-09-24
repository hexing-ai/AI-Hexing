"""Formal audio versions and immutable export manifests."""

from alembic import op

from backend.app.migration_v3 import metadata, names

revision = "0003"
down_revision = "0002"


def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables[n] for n in names])


def downgrade():
    metadata.drop_all(op.get_bind(), tables=[metadata.tables[n] for n in names])
