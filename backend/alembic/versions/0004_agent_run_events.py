"""add agent run execution tracing

Revision ID: 0004_agent_run_events
Revises: 0003_message_usage_tokens
Create Date: 2026-06-14 16:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_agent_run_events"
down_revision = "0003_message_usage_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("parts_json", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True))
    op.add_column("messages", sa.Column("parts_schema_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("messages", sa.Column("parts_updated_at", sa.DateTime(), nullable=True))

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_message_id", sa.Integer(), nullable=True),
        sa.Column("assistant_message_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resume_token", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("metadata_json", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"], unique=False)
    op.create_index("ix_agent_runs_user_message_id", "agent_runs", ["user_message_id"], unique=False)
    op.create_index("ix_agent_runs_assistant_message_id", "agent_runs", ["assistant_message_id"], unique=False)
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)

    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("assistant_message_id", sa.Integer(), nullable=True),
        sa.Column("tool_call_ref", sa.String(length=80), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_run_events_event_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"], unique=False)
    op.create_index("ix_run_events_assistant_message_id", "run_events", ["assistant_message_id"], unique=False)
    op.create_index("ix_run_events_tool_call_ref", "run_events", ["tool_call_ref"], unique=False)
    op.create_index("ix_run_events_event_type", "run_events", ["event_type"], unique=False)

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("assistant_message_id", sa.Integer(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=80), nullable=False),
        sa.Column("provider_tool_call_id", sa.String(length=120), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("input_for_model_json", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("display_input_preview", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("output_for_model_json", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("display_output_preview", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("audit_output_preview", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("output_blob_ref", sa.String(length=500), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text().with_variant(sa.dialects.mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("projection_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_tool_calls_run_tool_call_id"),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"], unique=False)
    op.create_index("ix_tool_calls_conversation_id", "tool_calls", ["conversation_id"], unique=False)
    op.create_index("ix_tool_calls_assistant_message_id", "tool_calls", ["assistant_message_id"], unique=False)
    op.create_index("ix_tool_calls_status", "tool_calls", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_calls_status", table_name="tool_calls")
    op.drop_index("ix_tool_calls_assistant_message_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_conversation_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_run_id", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("ix_run_events_event_type", table_name="run_events")
    op.drop_index("ix_run_events_tool_call_ref", table_name="run_events")
    op.drop_index("ix_run_events_assistant_message_id", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")

    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_assistant_message_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_message_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_column("messages", "parts_updated_at")
    op.drop_column("messages", "parts_schema_version")
    op.drop_column("messages", "parts_json")
