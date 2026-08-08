from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import UTCResponseModel


class ProviderPresetResponse(UTCResponseModel):
    id: str
    display_name: str
    default_adapter_id: str
    adapters: list[str]


class ProviderCreateRequest(BaseModel):
    preset_id: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    default_adapter_id: str | None = Field(default=None, max_length=80)
    default_model_id: str | None = Field(default=None, max_length=150)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    settings: dict[str, object] | None = None


class ProviderUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    default_adapter_id: str | None = Field(default=None, max_length=80)
    default_model_id: str | None = Field(default=None, max_length=150)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    settings: dict[str, object] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ProviderResponse(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    preset_id: str
    display_name: str
    default_adapter_id: str
    default_model_id: str | None
    base_url: str | None
    credential_hint: str | None
    enabled: bool
    is_default: bool
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_message: str | None


class ProviderModelCreateRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=150)
    display_name_override: str | None = Field(default=None, max_length=255)
    adapter_override: str | None = Field(default=None, max_length=80)
    enabled: bool = True


class ProviderModelUpdateRequest(BaseModel):
    display_name_override: str | None = Field(default=None, max_length=255)
    adapter_override: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


class ProviderModelResponse(UTCResponseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_instance_id: int
    model_id: str
    remote_display_name: str | None
    display_name_override: str | None
    adapter_override: str | None
    is_manual: bool
    enabled: bool
    remote_available: bool
    metadata_json: str | None
    last_seen_at: datetime | None


class ProviderTestResponse(UTCResponseModel):
    success: bool
    message: str
    provider: ProviderResponse
