"""Add encrypted GitHub account connections and OAuth state.

Revision ID: 0004_github_accounts
Revises: 0003_auth_attempts
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_github_accounts"
down_revision = "0003_auth_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_oauth_states",
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("code_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state_digest"),
    )
    op.create_index("ix_github_oauth_states_user_id", "github_oauth_states", ["user_id"])
    op.create_index("ix_github_oauth_states_session_id", "github_oauth_states", ["session_id"])
    op.create_index("ix_github_oauth_states_expires_at", "github_oauth_states", ["expires_at"])
    op.create_table(
        "github_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "github_user_id", name="uq_github_user_per_astra_user"),
    )
    op.create_index("ix_github_accounts_user_id", "github_accounts", ["user_id"])
    op.create_index("ix_github_accounts_workspace_id", "github_accounts", ["workspace_id"])
    op.create_index("ix_github_accounts_workspace_user", "github_accounts", ["workspace_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_github_accounts_workspace_user", table_name="github_accounts")
    op.drop_index("ix_github_accounts_workspace_id", table_name="github_accounts")
    op.drop_index("ix_github_accounts_user_id", table_name="github_accounts")
    op.drop_table("github_accounts")
    op.drop_index("ix_github_oauth_states_expires_at", table_name="github_oauth_states")
    op.drop_index("ix_github_oauth_states_session_id", table_name="github_oauth_states")
    op.drop_index("ix_github_oauth_states_user_id", table_name="github_oauth_states")
    op.drop_table("github_oauth_states")
