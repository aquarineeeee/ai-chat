"""persist configured provider display names

Revision ID: 0009_provider_display_name
Revises: 0008_provider_identity
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_provider_display_name"
down_revision = "0008_provider_identity"
branch_labels = None
depends_on = None


def _provider_display_name_update(table_name: str) -> None:
    table = sa.table(
        table_name,
        sa.column("provider", sa.String(length=100)),
        sa.column("provider_instance_id", sa.Integer()),
    )
    instances = sa.table(
        "provider_instances",
        sa.column("id", sa.Integer()),
        sa.column("display_name", sa.String(length=100)),
    )
    op.execute(
        table.update()
        .where(table.c.provider_instance_id.is_not(None))
        .values(
            provider=sa.select(instances.c.display_name)
            .where(instances.c.id == table.c.provider_instance_id)
            .scalar_subquery()
        )
    )


def upgrade() -> None:
    op.alter_column("conversations", "provider", existing_type=sa.String(length=50), type_=sa.String(length=100), existing_nullable=True)
    op.alter_column("messages", "provider", existing_type=sa.String(length=50), type_=sa.String(length=100), existing_nullable=True)
    op.alter_column("agent_runs", "provider", existing_type=sa.String(length=50), type_=sa.String(length=100), existing_nullable=False)
    for table_name in ("conversations", "messages", "agent_runs"):
        _provider_display_name_update(table_name)


def downgrade() -> None:
    op.alter_column("agent_runs", "provider", existing_type=sa.String(length=100), type_=sa.String(length=50), existing_nullable=False)
    op.alter_column("messages", "provider", existing_type=sa.String(length=100), type_=sa.String(length=50), existing_nullable=True)
    op.alter_column("conversations", "provider", existing_type=sa.String(length=100), type_=sa.String(length=50), existing_nullable=True)
