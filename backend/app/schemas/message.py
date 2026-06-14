from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import MessageRole, MessageStatus


class MessageCreateRequest(BaseModel):
    content: str
    parent_id: int | None = None
    branch_id: int | None = None
    provider: str | None = None
    model: str | None = None
    temperature: Decimal | None = None
    max_tokens: int | None = None
    activate_branch: bool = True
    context_mode: Literal["full", "root_only"] = "full"
    context_root_message_id: int | None = None


class MessageRegenerateRequest(BaseModel):
    branch_id: int | None = None
    provider: str | None = None
    model: str | None = None
    temperature: Decimal | None = None
    max_tokens: int | None = None
    activate_branch: bool = True
    context_mode: Literal["full", "root_only"] = "full"
    context_root_message_id: int | None = None


class MessageEditRequest(BaseModel):
    content: str
    mode: Literal["update", "branch"] = "update"
    branch_id: int | None = None
    context_mode: Literal["full", "root_only"] = "full"
    context_root_message_id: int | None = None


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


class MessageTreeBranchMarkerResponse(BaseModel):
    id: int
    title: str | None = None
    auto_title: str | None = None
    marker_type: Literal["fork", "leaf"]
    is_current_branch: bool = False


class MessageTreeNodeResponse(BaseModel):
    id: int
    conversation_id: int
    parent_id: int | None
    role: MessageRole
    preview: str
    status: MessageStatus
    error_message: str | None = None
    provider: str | None
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


class MessageTreeEdgeResponse(BaseModel):
    id: str
    source: int
    target: int
    is_active_path: bool = False


class ConversationMessageTreeResponse(BaseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None = None
    active_path: list[int]
    nodes: list[MessageTreeNodeResponse]
    edges: list[MessageTreeEdgeResponse]
    truncated: bool = False
    total_node_count: int = 0


class ConversationMessagesResponse(BaseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None
    items: list[MessageNodeResponse]


class MessageSendResponse(BaseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int
    user_message: MessageNodeResponse
    assistant_message: MessageNodeResponse


class MessageRegenerateResponse(BaseModel):
    conversation_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int
    replaced_message_id: int
    assistant_message: MessageNodeResponse


class MessageEditResponse(BaseModel):
    conversation_id: int
    message_id: int
    current_branch_id: int | None = None
    current_leaf_message_id: int | None
