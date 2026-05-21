from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=255)
    api_key: str = Field(min_length=1, max_length=1000)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    provider: str
    display_name: str
    base_url: str | None
    key_last_four: str | None
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_message: str | None
    created_at: datetime
    updated_at: datetime


class ApiKeyTestResponse(BaseModel):
    success: bool
    message: str
    api_key: ApiKeyResponse


class ProviderModelResponse(BaseModel):
    id: str
    owned_by: str | None = None
