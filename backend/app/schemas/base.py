from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def serialize_utc_datetime(value: datetime) -> str:
    """Serialize database timestamps as unambiguous UTC ISO 8601 values."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class UTCResponseModel(BaseModel):
    """Base model for API responses containing UTC database timestamps."""

    model_config = ConfigDict(json_encoders={datetime: serialize_utc_datetime})
