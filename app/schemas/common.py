from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    message: str


def require_aware(value: datetime | None) -> datetime | None:
    """Reject naive datetimes: every timestamp must carry a timezone."""
    if value is not None and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None):
        raise ValueError("Datetime must include a timezone offset (e.g. 2026-10-20T10:00:00+05:30).")
    return value


class AwareDatetimeMixin(BaseModel):
    @field_validator("*", mode="after")
    @classmethod
    def _aware(cls, value):  # noqa: ANN001 - applies to every field, only datetimes are checked
        if isinstance(value, datetime):
            require_aware(value)
        return value
