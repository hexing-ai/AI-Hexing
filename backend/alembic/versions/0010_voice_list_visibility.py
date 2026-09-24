"""Hide voice cards without deleting voices, samples or generated audio."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade():
    if "hidden_from_library" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("voice_metadata")}:
        return
    op.add_column(
        "voice_metadata",
        sa.Column("hidden_from_library", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("voice_metadata", "hidden_from_library")
