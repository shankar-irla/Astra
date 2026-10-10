"""Persist bounded agent task plans and lifecycle events.

Revision ID: 0007_agent_plans
Revises: 0006_retain_mutation_audit
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_agent_plans"
down_revision = "0006_retain_mutation_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("github_account_id", sa.Uuid(), nullable=True),
        sa.Column("github_login", sa.String(length=100), nullable=False),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["github_account_id"], ["github_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tasks_user_id", "agent_tasks", ["user_id"])
    op.create_index("ix_agent_tasks_workspace_id", "agent_tasks", ["workspace_id"])
    op.create_index("ix_agent_tasks_github_account_id", "agent_tasks", ["github_account_id"])
    op.create_index("ix_agent_tasks_workspace_created", "agent_tasks", ["workspace_id", "created_at"])
    op.create_table(
        "agent_task_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_agent_task_event_sequence"),
    )
    op.create_index("ix_agent_task_events_task_id", "agent_task_events", ["task_id"])
    op.create_index("ix_agent_task_events_task_sequence", "agent_task_events", ["task_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_agent_task_events_task_sequence", table_name="agent_task_events")
    op.drop_index("ix_agent_task_events_task_id", table_name="agent_task_events")
    op.drop_table("agent_task_events")
    op.drop_index("ix_agent_tasks_workspace_created", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_github_account_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_workspace_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_user_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")
