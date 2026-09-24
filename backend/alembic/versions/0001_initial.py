"""S1 initial schema. Frozen table definitions are captured by this migration."""

from alembic import op

from backend.app.migration_v1 import metadata

revision = "0001"
down_revision = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    metadata.drop_all(op.get_bind())
