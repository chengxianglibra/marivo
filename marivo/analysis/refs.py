"""Typed refs for analysis public operators."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["ArtifactRef", "CalendarRef"]


class _RefBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str

    def __init__(self, id: str | None = None, **data: object) -> None:
        if id is not None:
            if "id" in data:
                raise TypeError("ref id supplied both positionally and by keyword")
            data["id"] = id
        super().__init__(**data)

    @field_validator("id")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ref id must be non-empty")
        return normalized

    def __str__(self) -> str:
        return self.id


class CalendarRef(_RefBase):
    """Calendar provider ref."""


class ArtifactRef(_RefBase):
    """Session-local analysis artifact ref."""
