"""Task archive, trash, recovery and private asset ownership."""

from alembic import op

from backend.app.migration_v7 import statements

revision = "0007"
down_revision = "0006"


def upgrade():
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Preserve lifecycle history; restore a verified backup instead of dropping tables.")
