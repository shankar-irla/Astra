"""Add database-backed authentication throttling.

Revision ID: 0003_auth_attempts
Revises: 0002_workspaces
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_auth_attempts"
down_revision = "0002_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ip_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_attempts_ip_action_time", "auth_attempts", ["ip_fingerprint", "action", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_attempts_ip_action_time", table_name="auth_attempts")
    op.drop_table("auth_attempts")
