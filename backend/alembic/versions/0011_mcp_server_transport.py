"""add MCP transport selection

Revision ID: 0011_mcp_server_transport
Revises: 0010_mcp_registry
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_mcp_server_transport"
down_revision = "0010_mcp_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="streamable_http"),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "transport")
