"""add message usage tokens

Revision ID: 0003_message_usage_tokens
Revises: 0002_conversation_branches
Create Date: 2026-06-13 15:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_message_usage_tokens"
down_revision = "0002_conversation_branches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("total_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "total_tokens")
    op.drop_column("messages", "completion_tokens")
    op.drop_column("messages", "prompt_tokens")
