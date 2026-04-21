from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolMeta(BaseModel):
    """Transport metadata attached to every MCP tool response."""

    model_config = ConfigDict(extra="forbid")

    marivo_path: str
    method: str
    request_url: str
    attempt_count: int = 1
    content_type: str | None = None


class ToolError(BaseModel):
    """Structured error details normalized from Marivo HTTP responses."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["validation", "not_found", "conflict", "transport", "server_error"]
    message: str
    code: str | None = None
    detail: object | None = None
    guidance: dict[str, object] | None = None
    remediation_hint: str | None = None
    raw_body: str | None = None


class ToolEnvelope(BaseModel):
    """Canonical result envelope returned by Marivo MCP tools."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status_code: int
    data: object | None = None
    error: ToolError | None = None
    meta: ToolMeta
