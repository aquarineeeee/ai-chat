from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.message import MessageRole, MessageStatus


class MessageNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    parent_id: int | None
    role: MessageRole
    content: str
    provider: str | None
    model: str | None
    temperature: Decimal | None
    max_tokens: int | None
    status: MessageStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ConversationMessagesResponse(BaseModel):
    conversation_id: int
    current_leaf_message_id: int | None
    items: list[MessageNodeResponse]
