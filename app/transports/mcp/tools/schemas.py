"""Pydantic input models for MCP tool parameters.

Validators preserve wire compatibility with the existing marivo-mcp package.
No refactoring allowed in Phase 5 — copy verbatim.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, model_validator


def _reject_observe_time_scope_string(v: Any) -> Any:
    """Reject shorthand string time_scope; require canonical object form."""
    if isinstance(v, str):
        raise ValueError(
            "observe_time_scope_canonical_required: "
            "time_scope must be a structured object with kind, start, end "
            "(half-open interval [start, end)). "
            "Shorthand strings like '2024-03-01~2024-03-31' are not accepted."
        )
    return v


class McpObserveTimeScope(BaseModel):
    """Canonical time_scope for observe: half-open range [start, end)."""

    kind: str = "range"
    start: str
    end: str

    @model_validator(mode="after")
    def _validate_kind(self) -> McpObserveTimeScope:
        if self.kind != "range":
            raise ValueError(f"time_scope.kind must be 'range', got {self.kind!r}")
        return self


# Wrap with the string-rejection validator
ObserveTimeScope = Annotated[
    McpObserveTimeScope, BeforeValidator(_reject_observe_time_scope_string)
]


class ObserveScope(BaseModel):
    """Non-time population scope for observe and detect."""

    constraints: dict[str, Any] | None = None
    predicate_ref: str | None = None


class ObserveInput(BaseModel):
    """Input model for the observe MCP tool."""

    session_id: str
    metric: str
    time_scope: ObserveTimeScope
    granularity: str | None = None
    dimensions: list[str] | None = None
    scope: ObserveScope | None = None
    result_mode: str | None = None
    calendar_policy_ref: str | None = None
