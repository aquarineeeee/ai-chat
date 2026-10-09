from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AttachmentStatus(str, Enum):
    READY = "ready"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AttachmentStatus] = mapped_column(
        SqlEnum(AttachmentStatus, name="attachment_status", values_callable=lambda cls: [v.value for v in cls]),
        nullable=False, default=AttachmentStatus.READY, server_default="ready", index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    delete_error: Mapped[str | None] = mapped_column(Text)
    delete_reason: Mapped[str | None] = mapped_column(String(32))
    delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class MessageAttachment(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "sequence_index", name="uq_message_attachments_sequence"),
        UniqueConstraint("attachment_id", name="uq_message_attachments_attachment"),
    )

    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    attachment_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), ForeignKey("attachments.id", ondelete="RESTRICT"), primary_key=True)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
