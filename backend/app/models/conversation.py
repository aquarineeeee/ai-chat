from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新对话", server_default="新对话")
    system_prompt: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    current_leaf_message_id: Mapped[int | None] = mapped_column(Integer, index=True)
    current_branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_branches.id", ondelete="SET NULL", use_alter=True, name="fk_conversations_current_branch"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
