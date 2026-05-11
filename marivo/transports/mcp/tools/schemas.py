"""Pydantic input models for MCP tool parameters.

Validators for MCP tool parameter wire compatibility.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from marivo.contracts.generated.osi import AIContext1, Expression


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


def _reject_json_string(v: Any) -> Any:
    """Reject JSON-encoded strings; require structured object form."""
    if isinstance(v, str):
        raise ValueError(
            "mcp_structured_object_required: Pass a structured object, not a JSON-encoded string."
        )
    return v


JsonObject: TypeAlias = dict[str, object]  # noqa: UP040


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


McpStructuredObject = Annotated[
    JsonObject,
    BeforeValidator(_reject_json_string),
]


class McpObservationRef(BaseModel):
    """MCP-visible ref for compare inputs; mirrors CompareRequest ObservationRef."""

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


class McpDetectTimeScope(BaseModel):
    """MCP-visible detect time_scope contract."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["range"] = Field(description='Required literal "range".')
    start: str = Field(description="Inclusive start of the range, ISO-8601 date or datetime.")
    end: str = Field(description="Exclusive end of the range, ISO-8601 date or datetime.")


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


class McpMetricUpdatePayload(BaseModel):
    """Payload for update_metric MCP tool — only the fields the service allows."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(
        None, description="Human-readable description of what the metric measures"
    )
    ai_context: str | AIContext1 | None = Field(None, description="Additional context for AI tools")
    additive_dimensions: list[str] | None = Field(
        None,
        description="Field names across which the metric is additive",
        min_length=1,
    )
    expression: Expression | None = Field(None, description="Multi-dialect expression definition")


class McpRelationshipUpdatePayload(BaseModel):
    """Payload for update_relationship MCP tool — only the fields the service allows."""

    model_config = ConfigDict(extra="forbid")

    cardinality: str | None = Field(None, description="Relationship cardinality (e.g. many_to_one)")
    ai_context: str | AIContext1 | None = Field(None, description="Additional context for AI tools")
