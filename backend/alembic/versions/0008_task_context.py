"""Task source changes and creator test notes."""

from alembic import op

from backend.app.migration_v8 import statements

revision = "0008"
down_revision = "0007"


def upgrade():
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Preserve context history; restore a verified backup instead of dropping tables.")
