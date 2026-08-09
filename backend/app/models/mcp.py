from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("user_id", "server_name", name="uq_mcp_servers_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    server_name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    headers_encrypted_json: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    tested_config_version: Mapped[int | None] = mapped_column(Integer)
    last_test_status: Mapped[str | None] = mapped_column(String(20))
    last_test_message: Mapped[str | None] = mapped_column(String(500))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False)

    tools = relationship("McpTool", back_populates="server", cascade="all, delete-orphan")


class McpTool(Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "remote_tool_name", name="uq_mcp_tools_server_remote"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    remote_tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    input_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    annotations_json: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    remote_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    server = relationship("McpServer", back_populates="tools")
