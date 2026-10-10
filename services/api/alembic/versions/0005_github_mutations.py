"""Add idempotent GitHub mutation audit records.

Revision ID: 0005_github_mutations
Revises: 0004_github_accounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_github_mutations"
down_revision = "0004_github_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_mutations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("github_account_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("external_resource_number", sa.Integer(), nullable=True),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["github_account_id"], ["github_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_github_mutation_user_idempotency"),
    )
    op.create_index("ix_github_mutations_user_id", "github_mutations", ["user_id"])
    op.create_index("ix_github_mutations_workspace_id", "github_mutations", ["workspace_id"])
    op.create_index("ix_github_mutations_github_account_id", "github_mutations", ["github_account_id"])
    op.create_index("ix_github_mutations_workspace_created", "github_mutations", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_github_mutations_workspace_created", table_name="github_mutations")
    op.drop_index("ix_github_mutations_github_account_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_workspace_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_user_id", table_name="github_mutations")
    op.drop_table("github_mutations")
