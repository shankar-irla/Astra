"""Add workspaces, membership roles, and bind sessions to workspaces.

Revision ID: 0002_workspaces
Revises: 0001_identity
"""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4

revision = "0002_workspaces"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)
    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="owner", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )
    op.create_index("ix_workspace_memberships_workspace_id", "workspace_memberships", ["workspace_id"])
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Uuid()))
    workspaces = sa.table("workspaces", sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("slug", sa.String()))
    memberships = sa.table("workspace_memberships", sa.column("id", sa.Uuid()), sa.column("workspace_id", sa.Uuid()), sa.column("user_id", sa.Uuid()), sa.column("role", sa.String()))
    existing_sessions = sa.table("sessions", sa.column("user_id", sa.Uuid()), sa.column("workspace_id", sa.Uuid()))
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("workspace_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_sessions_workspace_id", ["workspace_id"])
        batch.create_foreign_key("fk_sessions_workspace_id_workspaces", "workspaces", ["workspace_id"], ["id"], ondelete="CASCADE")
    for (user_id,) in bind.execute(sa.select(users.c.id)):
        workspace_id = uuid4()
        bind.execute(workspaces.insert().values(id=workspace_id, name="Personal workspace", slug=f"personal-{user_id.hex[:16]}"))
        bind.execute(memberships.insert().values(id=uuid4(), workspace_id=workspace_id, user_id=user_id, role="owner"))
        bind.execute(existing_sessions.update().where(existing_sessions.c.user_id == user_id).values(workspace_id=workspace_id))
    with op.batch_alter_table("sessions") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_constraint("fk_sessions_workspace_id_workspaces", type_="foreignkey")
        batch.drop_index("ix_sessions_workspace_id")
        batch.drop_column("workspace_id")
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_index("ix_workspace_memberships_workspace_id", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_table("workspaces")
