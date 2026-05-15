"""Pydantic input models for MCP tool parameters.

Validators for MCP tool parameter wire compatibility.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _reject_time_scope_string(v: Any) -> Any:
    """Reject shorthand string time_scope; require canonical object form."""
    if isinstance(v, str):
        raise ValueError(
            "time_scope_canonical_required: "
            "time_scope must be a structured object with field, start, end "
            "(half-open interval [start, end) on the named time field). "
            "Shorthand strings like '2024-03-01~2024-03-31' are not accepted."
        )
    return v


def _reject_json_string(v: Any) -> Any:
    """Reject JSON-encoded strings; require structured object form."""
    if isinstance(v, str):
        raise ValueError(
            "mcp_structured_object_required: Pass a structured object, not a JSON-encoded string."
        )
    return v


JsonObject: TypeAlias = dict[str, object]  # noqa: UP040


class McpTimeScope(BaseModel):
    """AOI-aligned time_scope: half-open [start, end) on a named time field."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        ...,
        min_length=1,
        description="OSI dataset time field (e.g. 'log_time', 'event_time').",
    )
    start: str = Field(description="Inclusive start, ISO-8601 date or datetime.")
    end: str = Field(description="Exclusive end, ISO-8601 date or datetime.")

    @model_validator(mode="after")
    def _validate_start_before_end(self) -> McpTimeScope:
        if self.start.strip() >= self.end.strip():
            raise ValueError("time_scope.start must be strictly before time_scope.end")
        return self


# Wrap with the string-rejection validator for observe tool
McpTimeScopeValidated = Annotated[McpTimeScope, BeforeValidator(_reject_time_scope_string)]


McpStructuredObject = Annotated[
    JsonObject,
    BeforeValidator(_reject_json_string),
]


class McpObservationRef(BaseModel):
    """Legacy MCP-visible ref shape retained for derived compatibility callers."""

    model_config = ConfigDict(extra="allow")

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream observe step. Defaults to path session.",
    )
    step_id: str = Field(description='Required upstream observe step id, e.g. "step_obs_current".')
    step_type: Literal["observe"] = Field(
        description='Required literal "observe"; compare consumes observe step refs.',
    )


class McpArtifactRef(BaseModel):
    """MCP-visible generic artifact ref for downstream intent inputs."""

    model_config = ConfigDict(extra="allow")

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream step. Defaults to path session.",
    )
    step_id: str = Field(description='Required upstream step id, e.g. "step_compare_1".')
    step_type: str = Field(description='Required upstream step type, e.g. "compare".')


class McpCompareArtifactRef(McpArtifactRef):
    """MCP-visible ref for decompose inputs; step_type must be compare."""

    step_type: Literal["compare"] = Field(
        description='Required literal "compare"; decompose consumes compare step refs.',
    )


class ObserveScope(BaseModel):
    """Non-time population scope for observe and detect."""

    constraints: dict[str, Any] | None = None
    predicate_ref: str | None = None


class McpSliceRef(BaseModel):
    """AOI-aligned slice: time_scope + optional scope (mirrors AOI Slice)."""

    model_config = ConfigDict(extra="allow")

    time_scope: McpTimeScope
    scope: ObserveScope | None = None


class ObserveInput(BaseModel):
    """Input model for the observe MCP tool."""

    session_id: str
    metric: str
    time_scope: McpTimeScopeValidated
    granularity: str | None = None
    dimensions: list[str] | None = None
    scope: ObserveScope | None = None
    result_mode: str | None = None


McpOsiDocumentPayload = Annotated[dict[str, Any], BeforeValidator(_reject_json_string)]


class McpOsiDocumentInput(BaseModel):
    """Input for validating or importing an OSI-Marivo semantic document."""

    model_config = ConfigDict(extra="forbid")

    document: McpOsiDocumentPayload | None = Field(
        default=None,
        description="Inline OSI-Marivo semantic document JSON object.",
    )
    input_path: str | None = Field(
        default=None,
        description="Local JSON file path to read from the MCP stdio host.",
    )

    @model_validator(mode="after")
    def _require_exactly_one_source(self) -> McpOsiDocumentInput:
        if (self.document is None) == (self.input_path is None):
            raise ValueError("Provide exactly one of document or input_path.")
        return self
