"""Reusable JSON contract models for OpenAPI-safe schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel

JsonScalar = str | int | float | bool | None
ScalarMap = dict[str, JsonScalar]
JsonObject = dict[str, JsonValue]


class JsonScalarMap(RootModel[ScalarMap]):
    """JSON object whose values are restricted to scalar JSON values."""


class JsonObjectMap(RootModel[JsonObject]):
    """JSON object with typed recursive JSON values."""


class EmptyObject(BaseModel):
    """Explicit empty object schema."""

    model_config = ConfigDict(extra="forbid")
