from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunEventResponse(BaseModel):
    event_id: str
    type: str
    run_id: int
    sequence: int
    assistant_message_id: int | None = None
    step_id: str | None = None
    tool_call_ref: str | None = None
    created_at: datetime
    payload: dict[str, Any] = {}


class AgentRunResponse(BaseModel):
    id: int
    conversation_id: int
    user_message_id: int | None = None
    assistant_message_id: int | None = None
    provider: str
    provider_id: int | None = None
    adapter_id: str | None = None
    provider_name_snapshot: str | None = None
    model: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_sequence: int
    resume_token: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RunApprovalDecisionRequest(BaseModel):
    tool_call_ref: str
    comment: str | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]


class RunEventListResponse(BaseModel):
    run_id: int
    after_sequence: int
    items: list[RunEventResponse]


class RunViewResponse(BaseModel):
    run_id: int
    assistant_message_id: int | None = None
    status: str
    phase: str | None = None
    last_sequence: int
    pending_approval: dict[str, Any] | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
