"""Retain issue mutation audit records when a GitHub account is disconnected.

Revision ID: 0006_retain_mutation_audit
Revises: 0005_github_mutations
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_retain_mutation_audit"
down_revision = "0005_github_mutations"
branch_labels = None
depends_on = None


def _copy_legacy_rows() -> None:
    op.execute(sa.text("""
        INSERT INTO github_mutations (
            id, user_id, workspace_id, github_account_id, github_login,
            idempotency_key, request_hash, operation, repository, status,
            external_resource_number, external_url, created_at, updated_at
        )
        SELECT m.id, m.user_id, m.workspace_id, m.github_account_id, COALESCE(a.login, 'disconnected'),
            m.idempotency_key, m.request_hash, m.operation, m.repository, m.status,
            m.external_resource_number, m.external_url, m.created_at, m.updated_at
        FROM github_mutations_legacy AS m
        LEFT JOIN github_accounts AS a ON a.id = m.github_account_id
    """))


def upgrade() -> None:
    op.drop_index("ix_github_mutations_workspace_created", table_name="github_mutations")
    op.drop_index("ix_github_mutations_github_account_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_workspace_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_user_id", table_name="github_mutations")
    op.rename_table("github_mutations", "github_mutations_legacy")
    op.create_table(
        "github_mutations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("github_account_id", sa.Uuid(), nullable=True),
        sa.Column("github_login", sa.String(length=100), nullable=False),
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
        sa.ForeignKeyConstraint(["github_account_id"], ["github_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_github_mutation_workspace_idempotency"),
    )
    _copy_legacy_rows()
    op.drop_table("github_mutations_legacy")
    op.create_index("ix_github_mutations_user_id", "github_mutations", ["user_id"])
    op.create_index("ix_github_mutations_workspace_id", "github_mutations", ["workspace_id"])
    op.create_index("ix_github_mutations_github_account_id", "github_mutations", ["github_account_id"])
    op.create_index("ix_github_mutations_workspace_created", "github_mutations", ["workspace_id", "created_at"])


def downgrade() -> None:
    connection = op.get_bind()
    orphaned = connection.execute(sa.text(
        "SELECT count(*) FROM github_mutations WHERE github_account_id IS NULL"
    )).scalar_one()
    if orphaned:
        raise RuntimeError("Cannot restore the old audit schema after its GitHub accounts were disconnected.")
    op.drop_index("ix_github_mutations_workspace_created", table_name="github_mutations")
    op.drop_index("ix_github_mutations_github_account_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_workspace_id", table_name="github_mutations")
    op.drop_index("ix_github_mutations_user_id", table_name="github_mutations")
    op.rename_table("github_mutations", "github_mutations_legacy")
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
    op.execute(sa.text("""
        INSERT INTO github_mutations (
            id, user_id, workspace_id, github_account_id, idempotency_key,
            request_hash, operation, repository, status, external_resource_number,
            external_url, created_at, updated_at
        )
        SELECT id, user_id, workspace_id, github_account_id, idempotency_key,
            request_hash, operation, repository, status, external_resource_number,
            external_url, created_at, updated_at
        FROM github_mutations_legacy
    """))
    op.drop_table("github_mutations_legacy")
    op.create_index("ix_github_mutations_user_id", "github_mutations", ["user_id"])
    op.create_index("ix_github_mutations_workspace_id", "github_mutations", ["workspace_id"])
    op.create_index("ix_github_mutations_github_account_id", "github_mutations", ["github_account_id"])
    op.create_index("ix_github_mutations_workspace_created", "github_mutations", ["workspace_id", "created_at"])
