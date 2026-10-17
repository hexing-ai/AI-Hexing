"""Multi-use invite codes that open read-only guest sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "invites" not in inspector.get_table_names():
        op.execute(
            """CREATE TABLE invites (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                created_at FLOAT NOT NULL,
                owner_id VARCHAR(36) NOT NULL REFERENCES creators(id),
                code_hash VARCHAR(64) NOT NULL UNIQUE,
                label VARCHAR(100) NOT NULL DEFAULT '',
                expires_at FLOAT NOT NULL,
                revoked BOOLEAN NOT NULL DEFAULT 0,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at FLOAT
            )"""
        )
        op.execute("CREATE INDEX ix_invites_owner_id ON invites (owner_id)")
    columns = {c["name"] for c in inspector.get_columns("sessions")}
    if "role" not in columns:
        op.execute("ALTER TABLE sessions ADD COLUMN role VARCHAR(8) NOT NULL DEFAULT 'owner'")
    if "invite_id" not in columns:
        op.execute("ALTER TABLE sessions ADD COLUMN invite_id VARCHAR(36) REFERENCES invites(id)")


def downgrade():
    raise RuntimeError("Restore a verified backup instead of dropping guest access records.")
