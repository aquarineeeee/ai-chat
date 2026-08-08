"""use Responses API for official OpenAI provider instances

Revision ID: 0007_openai_responses_default
Revises: 0006_provider_instances
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_openai_responses_default"
down_revision = "0006_provider_instances"
branch_labels = None
depends_on = None


_OFFICIAL_OPENAI_URLS = ("https://api.openai.com", "https://api.openai.com/v1")


def _official_openai_predicate() -> sa.sql.elements.BooleanClauseList:
    instances = sa.table(
        "provider_instances",
        sa.column("preset_id", sa.String()),
        sa.column("base_url", sa.String()),
    )
    return sa.and_(
        instances.c.preset_id == "openai",
        sa.or_(
            instances.c.base_url.is_(None),
            sa.func.trim(instances.c.base_url) == "",
            sa.func.lower(sa.func.rtrim(instances.c.base_url, "/")).in_(_OFFICIAL_OPENAI_URLS),
        ),
    )


def upgrade() -> None:
    instances = sa.table("provider_instances", sa.column("default_adapter_id", sa.String()))
    op.execute(
        instances.update()
        .where(_official_openai_predicate())
        .values(default_adapter_id="openai_responses")
    )


def downgrade() -> None:
    instances = sa.table("provider_instances", sa.column("default_adapter_id", sa.String()))
    op.execute(
        instances.update()
        .where(_official_openai_predicate())
        .where(instances.c.default_adapter_id == "openai_responses")
        .values(default_adapter_id="openai_chat_completions")
    )
