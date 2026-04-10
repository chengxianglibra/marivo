from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolMeta(BaseModel):
    """Transport metadata reserved for later tool responses."""

    model_config = ConfigDict(extra="forbid")

    factum_path: str
    method: str


class ToolError(BaseModel):
    """Placeholder structured error shape for later transport integration."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ToolEnvelope(BaseModel):
    """Placeholder result envelope for future direct-tool implementations."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status_code: int
    data: dict[str, object] | None = None
    error: ToolError | None = None
    meta: ToolMeta
