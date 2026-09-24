"""Private ranking revisions and public snapshots; no initial publications."""

from alembic import op

from backend.app.migration_v6 import statements

revision = "0006"
down_revision = "0005"


def upgrade():
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("S4 publishing data must be preserved; restore a verified backup instead of dropping tables.")
