"""local image attachments and ordered message ownership"""
from alembic import op
import sqlalchemy as sa

revision = "0013_attachments"
down_revision = "0012_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("storage_path", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum("ready", "deleting", "deleted", "failed", name="attachment_status"), nullable=False, server_default="ready"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("deleted_at", sa.DateTime()),
        sa.Column("delete_error", sa.Text()),
        sa.Column("delete_reason", sa.String(32)),
        sa.Column("delete_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delete_next_attempt_at", sa.DateTime()),
        sa.UniqueConstraint("storage_path", name="uq_attachments_storage_path"),
    )
    for column in ("user_id", "status", "created_at", "delete_next_attempt_at"):
        op.create_index(f"ix_attachments_{column}", "attachments", [column])
    op.create_table(
        "message_attachments",
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), sa.ForeignKey("attachments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("message_id", "attachment_id"),
        sa.UniqueConstraint("message_id", "sequence_index", name="uq_message_attachments_sequence"),
        sa.UniqueConstraint("attachment_id", name="uq_message_attachments_attachment"),
    )


def downgrade() -> None:
    op.drop_table("message_attachments")
    op.drop_table("attachments")
