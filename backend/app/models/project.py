from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    default_model_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False)

    tools = relationship("ProjectMcpTool", back_populates="project", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="project", passive_deletes=True)


class ProjectMcpTool(Base):
    __tablename__ = "project_mcp_tools"
    __table_args__ = (UniqueConstraint("project_id", "mcp_tool_id", name="uq_project_mcp_tool"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    mcp_tool_id: Mapped[int] = mapped_column(ForeignKey("mcp_tools.id", ondelete="CASCADE"), primary_key=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    project = relationship("Project", back_populates="tools")
    mcp_tool = relationship("McpTool")


class ConversationMcpTool(Base):
    __tablename__ = "conversation_mcp_tools"
    __table_args__ = (UniqueConstraint("conversation_id", "mcp_tool_id", name="uq_conversation_mcp_tool"),)

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    mcp_tool_id: Mapped[int] = mapped_column(ForeignKey("mcp_tools.id", ondelete="CASCADE"), primary_key=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    conversation = relationship("Conversation", back_populates="mcp_tools")
    mcp_tool = relationship("McpTool")
