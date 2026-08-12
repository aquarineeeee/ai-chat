"""add projects and project-scoped MCP configuration"""
from alembic import op
import sqlalchemy as sa

revision = "0012_projects"
down_revision = "0011_mcp_server_transport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("default_model_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])
    op.add_column("conversations", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    op.create_foreign_key("fk_conversations_project_id", "conversations", "projects", ["project_id"], ["id"], ondelete="CASCADE")
    for table, owner, target in (("project_mcp_tools", "project_id", "projects"), ("conversation_mcp_tools", "conversation_id", "conversations")):
        op.create_table(
            table,
            sa.Column(owner, sa.Integer(), sa.ForeignKey(f"{target}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("mcp_tool_id", sa.Integer(), sa.ForeignKey("mcp_tools.id", ondelete="CASCADE"), nullable=False),
            sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default="1"),
            sa.PrimaryKeyConstraint(owner, "mcp_tool_id"),
            sa.UniqueConstraint(owner, "mcp_tool_id", name=f"uq_{table[:-1]}"),
        )
        op.create_index(f"ix_{table}_{owner}", table, [owner])


def downgrade() -> None:
    op.drop_table("conversation_mcp_tools")
    op.drop_table("project_mcp_tools")
    op.drop_constraint("fk_conversations_project_id", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_column("conversations", "project_id")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
