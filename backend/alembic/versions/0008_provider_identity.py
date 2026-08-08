"""persist provider preset IDs instead of adapter families

Revision ID: 0008_provider_identity
Revises: 0007_openai_responses_default
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_provider_identity"
down_revision = "0007_openai_responses_default"
branch_labels = None
depends_on = None


def _provider_identity_update(table_name: str) -> None:
    table = sa.table(
        table_name,
        sa.column("provider", sa.String()),
        sa.column("provider_instance_id", sa.Integer()),
    )
    instances = sa.table(
        "provider_instances",
        sa.column("id", sa.Integer()),
        sa.column("preset_id", sa.String()),
    )
    op.execute(
        table.update()
        .where(table.c.provider_instance_id.is_not(None))
        .values(
            provider=sa.select(instances.c.preset_id)
            .where(instances.c.id == table.c.provider_instance_id)
            .scalar_subquery()
        )
    )


def upgrade() -> None:
    for table_name in ("conversations", "messages", "agent_runs"):
        _provider_identity_update(table_name)


def downgrade() -> None:
    # The old value was derived from the adapter and cannot be restored
    # faithfully after a provider's adapter has changed.
    pass
