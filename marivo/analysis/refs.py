"""Typed refs for analysis public operators."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["ArtifactRef"]


class _RefBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str

    def __init__(self, ref: str | None = None, **data: object) -> None:
        if ref is not None:
            if "ref" in data:
                raise TypeError("ref value supplied both positionally and by keyword")
            data["ref"] = ref
        super().__init__(**data)

    @field_validator("ref")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ref value must be non-empty")
        return normalized

    def __str__(self) -> str:
        return self.ref


class ArtifactRef(_RefBase):
    """Session-local analysis artifact ref."""
