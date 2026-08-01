"""add run timeline step ids

Revision ID: 0005_run_timeline_step_id
Revises: 0004_agent_run_events
Create Date: 2026-07-07 20:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_run_timeline_step_id"
down_revision = "0004_agent_run_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_events", sa.Column("step_id", sa.String(length=80), nullable=True))
    op.create_index("ix_run_events_step_id", "run_events", ["step_id"], unique=False)
    op.create_index("ix_run_events_run_id_step_id_sequence", "run_events", ["run_id", "step_id", "sequence"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_run_events_run_id_step_id_sequence", table_name="run_events")
    op.drop_index("ix_run_events_step_id", table_name="run_events")
    op.drop_column("run_events", "step_id")
