from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import UTCResponseModel


class McpHeaderInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=4000)
    delete: bool = False


class McpServerCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=1000)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    headers: list[McpHeaderInput] = Field(default_factory=list)
    enabled: bool = True


class McpServerUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    transport: Literal["streamable_http", "sse"] | None = None
    headers: list[McpHeaderInput] | None = None
    enabled: bool | None = None


class McpToolUpdateRequest(BaseModel):
    enabled: bool | None = None
    requires_approval: bool | None = None


class McpToolResponse(UTCResponseModel):
    id: int
    remote_tool_name: str
    model_tool_name: str
    description: str | None
    input_schema: dict[str, object]
    annotations: dict[str, object] | None
    enabled: bool
    requires_approval: bool
    remote_available: bool
    synced_at: datetime | None


class McpServerResponse(UTCResponseModel):
    id: int
    display_name: str
    server_name: str
    transport: Literal["streamable_http", "sse"]
    headers: list[dict[str, str]]
    enabled: bool
    config_version: int
    tested_config_version: int | None
    last_test_status: str | None
    last_test_message: str | None
    last_tested_at: datetime | None
    last_successful_sync_at: datetime | None
    tools: list[McpToolResponse]
