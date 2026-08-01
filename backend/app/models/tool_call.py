from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_tool_calls_run_tool_call_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    assistant_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_tool_call_id: Mapped[str | None] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    input_for_model_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    display_input_preview: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    output_for_model_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    display_output_preview: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    audit_output_preview: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    output_blob_ref: Mapped[str | None] = mapped_column(String(500))
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )
