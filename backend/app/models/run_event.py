from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_run_events_event_id"),
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    assistant_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    step_id: Mapped[str | None] = mapped_column(String(80), index=True)
    tool_call_ref: Mapped[str | None] = mapped_column(String(80), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
