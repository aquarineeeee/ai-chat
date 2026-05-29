from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BranchCreate(BaseModel):
    parent_branch_id: int | None = Field(default=None, ge=1)
    forked_from_message_id: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)


class BranchUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    parent_branch_id: int | None
    forked_from_message_id: int | None
    current_leaf_message_id: int | None
    title: str | None
    auto_title: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
