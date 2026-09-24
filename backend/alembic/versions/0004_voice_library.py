"""Private voice names and default voice preferences."""

from alembic import op

from backend.app.migration_v4 import metadata, names

revision = "0004"
down_revision = "0003"


def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables[n] for n in names])


def downgrade():
    metadata.drop_all(op.get_bind(), tables=[metadata.tables[n] for n in names])
