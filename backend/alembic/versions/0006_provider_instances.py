"""provider instances and model catalog

Revision ID: 0006_provider_instances
Revises: 0005_run_timeline_step_id
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_provider_instances"
down_revision = "0005_run_timeline_step_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("preset_id", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("default_adapter_id", sa.String(length=80), nullable=False),
        sa.Column("default_model_id", sa.String(length=150)),
        sa.Column("base_url", sa.String(length=500)),
        sa.Column("credentials_encrypted_json", sa.Text()),
        sa.Column("credential_hint", sa.String(length=16)),
        sa.Column("settings_json", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_tested_at", sa.DateTime()),
        sa.Column("last_test_status", sa.String(length=20)),
        sa.Column("last_test_message", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "display_name", name="uq_provider_instances_user_name"),
    )
    op.create_index("idx_provider_instances_user", "provider_instances", ["user_id"], unique=False)
    op.create_table(
        "provider_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_instance_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=150), nullable=False),
        sa.Column("remote_display_name", sa.String(length=255)),
        sa.Column("display_name_override", sa.String(length=255)),
        sa.Column("adapter_override", sa.String(length=80)),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("remote_available", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["provider_instance_id"], ["provider_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_instance_id", "model_id", name="uq_provider_models_instance_model"),
    )
    op.create_index("idx_provider_models_instance", "provider_models", ["provider_instance_id"], unique=False)
    op.add_column("conversations", sa.Column("provider_instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_conversations_provider_instance", "conversations", "provider_instances", ["provider_instance_id"], ["id"], ondelete="SET NULL")
    op.add_column("messages", sa.Column("provider_instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_messages_provider_instance", "messages", "provider_instances", ["provider_instance_id"], ["id"], ondelete="SET NULL")
    op.add_column("messages", sa.Column("adapter_id", sa.String(length=80), nullable=True))
    op.add_column("messages", sa.Column("provider_name_snapshot", sa.String(length=100), nullable=True))
    op.add_column("agent_runs", sa.Column("provider_instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_agent_runs_provider_instance", "agent_runs", "provider_instances", ["provider_instance_id"], ["id"], ondelete="SET NULL")
    op.add_column("agent_runs", sa.Column("adapter_id", sa.String(length=80), nullable=True))
    op.add_column("agent_runs", sa.Column("provider_name_snapshot", sa.String(length=100), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, user_id, provider, display_name, base_url, key_encrypted, key_last_four, updated_at FROM api_keys ORDER BY user_id, updated_at DESC, id DESC")).mappings().all()
    defaults: dict[int, int] = {}
    for row in rows:
        provider = str(row["provider"]).lower()
        preset = provider if provider in {"anthropic", "openai"} else "custom"
        adapter = "anthropic_messages" if provider == "anthropic" else "openai_chat_completions"
        result = bind.execute(sa.text("INSERT INTO provider_instances (user_id,preset_id,display_name,default_adapter_id,base_url,credentials_encrypted_json,credential_hint,is_default) VALUES (:user_id,:preset,:display,:adapter,:base_url,:credentials,:hint,:default)"), {"user_id": row["user_id"], "preset": preset, "display": row["display_name"], "adapter": adapter, "base_url": row["base_url"], "credentials": row["key_encrypted"], "hint": row["key_last_four"], "default": 0})
        instance_id = result.lastrowid
        if row["user_id"] not in defaults:
            defaults[row["user_id"]] = int(instance_id)
            bind.execute(sa.text("UPDATE provider_instances SET is_default=1 WHERE id=:id"), {"id": instance_id})
    for user_id, instance_id in defaults.items():
        bind.execute(sa.text("UPDATE conversations SET provider_instance_id=:instance_id WHERE user_id=:user_id"), {"instance_id": instance_id, "user_id": user_id})


def downgrade() -> None:
    op.drop_column("agent_runs", "provider_name_snapshot")
    op.drop_column("agent_runs", "adapter_id")
    op.drop_constraint("fk_agent_runs_provider_instance", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "provider_instance_id")
    op.drop_column("messages", "provider_name_snapshot")
    op.drop_column("messages", "adapter_id")
    op.drop_constraint("fk_messages_provider_instance", "messages", type_="foreignkey")
    op.drop_column("messages", "provider_instance_id")
    op.drop_constraint("fk_conversations_provider_instance", "conversations", type_="foreignkey")
    op.drop_column("conversations", "provider_instance_id")
    op.drop_index("idx_provider_models_instance", table_name="provider_models")
    op.drop_table("provider_models")
    op.drop_index("idx_provider_instances_user", table_name="provider_instances")
    op.drop_table("provider_instances")
