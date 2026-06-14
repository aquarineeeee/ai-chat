from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(str, Enum):
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


def enum_values(enum_cls: type[Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    role: Mapped[MessageRole] = mapped_column(
        SqlEnum(MessageRole, name="message_role", values_callable=enum_values),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    parts_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    parts_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parts_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[MessageStatus] = mapped_column(
        SqlEnum(MessageStatus, name="message_status", values_callable=enum_values),
        nullable=False,
        default=MessageStatus.COMPLETED,
        server_default=MessageStatus.COMPLETED.value,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    conversation = relationship("Conversation", back_populates="messages")
