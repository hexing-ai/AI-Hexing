"""S2 text versions and discovery batches."""

from alembic import op

from backend.app.migration_v2 import metadata, names

revision = "0002"
down_revision = "0001"


def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables[n] for n in names])


def downgrade():
    metadata.drop_all(op.get_bind(), tables=[metadata.tables[n] for n in names])
