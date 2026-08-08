from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import UTCResponseModel


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=255)
    system_prompt: str | None = None
    provider: str | None = Field(default=None, max_length=100)
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=100)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    system_prompt: str | None = None
    provider: str | None = Field(default=None, max_length=100)
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=100)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    current_leaf_message_id: int | None = Field(default=None, ge=1)
    current_branch_id: int | None = Field(default=None, ge=1)


class ConversationResponse(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    system_prompt: str | None
    provider: str | None
    provider_id: int | None
    model: str | None
    temperature: Decimal | None
    max_tokens: int | None
    current_leaf_message_id: int | None
    current_branch_id: int | None
    created_at: datetime
    updated_at: datetime


class ImportedConversationSummary(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str


class ConversationImportResponse(UTCResponseModel):
    conversation: ImportedConversationSummary
    message_count: int
    ignored_count: int
    warnings: list[str]
