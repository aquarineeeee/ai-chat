from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.attachment import AttachmentResponse

from app.models.message import MessageRole, MessageStatus
from app.schemas.base import UTCResponseModel


class MessageCreateRequest(BaseModel):
    content: str = ""
    attachment_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_content_and_attachments(self):
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("消息必须包含文字或图片")
        if len(set(self.attachment_ids)) != len(self.attachment_ids) or any(i <= 0 for i in self.attachment_ids):
            raise ValueError("附件 ID 必须为不重复的正整数")
        return self
    parent_id: int | None = None
    branch_id: int | None = None
    provider: str | None = None
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = None
    temperature: Decimal | None = None
    max_tokens: int | None = None
    activate_branch: bool = True
    context_mode: Literal["full", "root_only", "last_n"] = "full"
    context_root_message_id: int | None = None
    context_message_count: int | None = Field(default=None, ge=1)


class MessageRegenerateRequest(BaseModel):
    branch_id: int | None = None
    provider: str | None = None
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = None
    temperature: Decimal | None = None
    max_tokens: int | None = None
    activate_branch: bool = True
    context_mode: Literal["full", "root_only", "last_n"] = "full"
    context_root_message_id: int | None = None
    context_message_count: int | None = Field(default=None, ge=1)


class MessageEditRequest(BaseModel):
    content: str = ""
    attachment_ids: list[int] | None = None

    @model_validator(mode="after")
    def validate_attachment_ids(self):
        if self.attachment_ids is not None and (len(set(self.attachment_ids)) != len(self.attachment_ids) or any(i <= 0 for i in self.attachment_ids)):
            raise ValueError("附件 ID 必须为不重复的正整数")
        return self
    mode: Literal["update", "branch"] = "update"
    branch_id: int | None = None
    context_mode: Literal["full", "root_only", "last_n"] = "full"
    context_root_message_id: int | None = None
    context_message_count: int | None = Field(default=None, ge=1)


class MessageNodeResponse(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    parent_id: int | None
    role: MessageRole
    content: str
    attachments: list[AttachmentResponse] = Field(default_factory=list)
    provider: str | None
    provider_id: int | None = None
    adapter_id: str | None = None
    provider_name_snapshot: str | None = None
    model: str | None
    temperature: Decimal | None
    max_tokens: int | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    parts: list[dict[str, Any]] | None = None
    parts_schema_version: int = 1
    status: MessageStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    sibling_index: int = 1
    sibling_count: int = 1
    previous_sibling_id: int | None = None
    next_sibling_id: int | None = None


class MessageTreeBranchMarkerResponse(UTCResponseModel):
    id: int
    title: str | None = None
    auto_title: str | None = None
    marker_type: Literal["fork", "leaf"]
    is_current_branch: bool = False


class MessageTreeNodeResponse(UTCResponseModel):
    id: int
    conversation_id: int
    parent_id: int | None
    role: MessageRole
    preview: str
    attachment_count: int = 0
    status: MessageStatus
    error_message: str | None = None
    provider: str | None
    provider_id: int | None = None
    adapter_id: str | None = None
    provider_name_snapshot: str | None = None
    model: str | None
    created_at: datetime
    updated_at: datetime
    sibling_index: int = 1
    sibling_count: int = 1
    child_count: int = 0
    is_leaf: bool = True
    is_active_path: bool = False
    is_current_leaf: bool = False
    branch_markers: list[MessageTreeBranchMarkerResponse] = Field(default_factory=list)


class MessageTreeEdgeResponse(UTCResponseModel):
    id: str
    source: int
    target: int
    is_active_path: bool = False


class ConversationMessageTreeResponse(UTCResponseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None = None
    active_path: list[int]
    nodes: list[MessageTreeNodeResponse]
    edges: list[MessageTreeEdgeResponse]
    truncated: bool = False
    total_node_count: int = 0


class ConversationMessagesResponse(UTCResponseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None
    items: list[MessageNodeResponse]
    has_more: bool = False
    next_before_message_id: int | None = None


class MessageSendResponse(UTCResponseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int
    user_message: MessageNodeResponse
    assistant_message: MessageNodeResponse


class MessageRegenerateResponse(UTCResponseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int
    replaced_message_id: int
    assistant_message: MessageNodeResponse


class MessageEditResponse(UTCResponseModel):
    conversation_id: int
    message_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None
