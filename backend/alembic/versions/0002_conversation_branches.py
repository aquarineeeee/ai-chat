"""add conversation branches

Revision ID: 0002_conversation_branches
Revises: 0001_initial_schema
Create Date: 2026-05-29 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_conversation_branches"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_branches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("parent_branch_id", sa.Integer(), nullable=True),
        sa.Column("forked_from_message_id", sa.Integer(), nullable=True),
        sa.Column("current_leaf_message_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("auto_title", sa.String(length=255), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_leaf_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["forked_from_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_branch_id"], ["conversation_branches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conversation_branches_conversation", "conversation_branches", ["conversation_id"], unique=False)
    op.create_index(
        "idx_conversation_branches_conversation_parent",
        "conversation_branches",
        ["conversation_id", "parent_branch_id"],
        unique=False,
    )
    op.create_index(
        "idx_conversation_branches_conversation_updated",
        "conversation_branches",
        ["conversation_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_conversation_branches_current_leaf",
        "conversation_branches",
        ["current_leaf_message_id"],
        unique=False,
    )
    op.create_index(
        "idx_conversation_branches_forked_from",
        "conversation_branches",
        ["forked_from_message_id"],
        unique=False,
    )
    op.create_index(
        "idx_conversation_branches_parent",
        "conversation_branches",
        ["parent_branch_id"],
        unique=False,
    )

    op.add_column("conversations", sa.Column("current_branch_id", sa.Integer(), nullable=True))
    op.create_index("idx_conversations_current_branch", "conversations", ["current_branch_id"], unique=False)
    op.create_foreign_key(
        "fk_conversations_current_branch",
        "conversations",
        "conversation_branches",
        ["current_branch_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _backfill_main_branches()


def downgrade() -> None:
    op.drop_constraint("fk_conversations_current_branch", "conversations", type_="foreignkey")
    op.drop_index("idx_conversations_current_branch", table_name="conversations")
    op.drop_column("conversations", "current_branch_id")

    op.drop_index("idx_conversation_branches_parent", table_name="conversation_branches")
    op.drop_index("idx_conversation_branches_forked_from", table_name="conversation_branches")
    op.drop_index("idx_conversation_branches_current_leaf", table_name="conversation_branches")
    op.drop_index("idx_conversation_branches_conversation_updated", table_name="conversation_branches")
    op.drop_index("idx_conversation_branches_conversation_parent", table_name="conversation_branches")
    op.drop_index("idx_conversation_branches_conversation", table_name="conversation_branches")
    op.drop_table("conversation_branches")


def _backfill_main_branches() -> None:
    connection = op.get_bind()
    branch_table = sa.Table(
        "conversation_branches",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer()),
        sa.Column("parent_branch_id", sa.Integer()),
        sa.Column("forked_from_message_id", sa.Integer()),
        sa.Column("current_leaf_message_id", sa.Integer()),
        sa.Column("title", sa.String(length=255)),
        sa.Column("auto_title", sa.String(length=255)),
    )

    conversations = connection.execute(
        sa.text("SELECT id, current_leaf_message_id FROM conversations ORDER BY id ASC")
    ).mappings()
    for conversation in conversations:
        conversation_id = conversation["id"]
        leaf_id = _valid_leaf_id(
            connection=connection,
            conversation_id=conversation_id,
            current_leaf_message_id=conversation["current_leaf_message_id"],
        )
        if leaf_id is None:
            leaf_id = _latest_leaf_id(connection=connection, conversation_id=conversation_id)

        result = connection.execute(
            branch_table.insert().values(
                conversation_id=conversation_id,
                parent_branch_id=None,
                forked_from_message_id=None,
                current_leaf_message_id=leaf_id,
                title=None,
                auto_title="主分支",
            )
        )
        branch_id = result.inserted_primary_key[0]
        connection.execute(
            sa.text(
                """
                UPDATE conversations
                SET current_branch_id = :branch_id,
                    current_leaf_message_id = :leaf_id
                WHERE id = :conversation_id
                """
            ),
            {"branch_id": branch_id, "leaf_id": leaf_id, "conversation_id": conversation_id},
        )


def _valid_leaf_id(
    *,
    connection,
    conversation_id: int,
    current_leaf_message_id: int | None,
) -> int | None:
    if current_leaf_message_id is None:
        return None

    row = connection.execute(
        sa.text(
            """
            SELECT id
            FROM messages
            WHERE id = :message_id AND conversation_id = :conversation_id
            LIMIT 1
            """
        ),
        {"message_id": current_leaf_message_id, "conversation_id": conversation_id},
    ).first()
    return current_leaf_message_id if row else None


def _latest_leaf_id(*, connection, conversation_id: int) -> int | None:
    row = connection.execute(
        sa.text(
            """
            SELECT m.id
            FROM messages m
            WHERE m.conversation_id = :conversation_id
              AND NOT EXISTS (
                SELECT 1
                FROM messages child
                WHERE child.parent_id = m.id
              )
            ORDER BY m.updated_at DESC, m.created_at DESC, m.id DESC
            LIMIT 1
            """
        ),
        {"conversation_id": conversation_id},
    ).first()
    return row[0] if row else None
