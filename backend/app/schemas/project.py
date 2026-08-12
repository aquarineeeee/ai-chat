from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.base import UTCResponseModel


class ProjectToolInput(BaseModel):
    mcp_tool_id: int = Field(ge=1)
    requires_approval: bool = True


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str | None = None
    default_model_id: str | None = Field(default=None, max_length=100)
    tools: list[ProjectToolInput] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = None
    default_model_id: str | None = Field(default=None, max_length=100)


class ProjectToolUpdate(BaseModel):
    requires_approval: bool | None = None


class ProjectToolResponse(UTCResponseModel):
    mcp_tool_id: int
    requires_approval: bool
    model_tool_name: str
    remote_tool_name: str
    description: str | None
    enabled: bool
    remote_available: bool


class ProjectResponse(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    name: str
    system_prompt: str | None
    default_model_id: str | None
    conversation_count: int = 0
    tools: list[ProjectToolResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
