"""add per-user MCP server and tool registry

Revision ID: 0010_mcp_registry
Revises: 0009_provider_display_name
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_mcp_registry"
down_revision = "0009_provider_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("server_name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("headers_encrypted_json", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tested_config_version", sa.Integer()),
        sa.Column("last_test_status", sa.String(20)),
        sa.Column("last_test_message", sa.String(500)),
        sa.Column("last_tested_at", sa.DateTime()),
        sa.Column("last_successful_sync_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("user_id", "server_name", name="uq_mcp_servers_user_name"),
    )
    op.create_index("ix_mcp_servers_user_id", "mcp_servers", ["user_id"])
    op.create_table(
        "mcp_tools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remote_tool_name", sa.String(255), nullable=False),
        sa.Column("model_tool_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("annotations_json", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("remote_available", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("synced_at", sa.DateTime()),
        sa.UniqueConstraint("server_id", "remote_tool_name", name="uq_mcp_tools_server_remote"),
    )
    op.create_index("ix_mcp_tools_server_id", "mcp_tools", ["server_id"])


def downgrade() -> None:
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")
