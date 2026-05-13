"""Pydantic input models for MCP tool parameters.

Validators for MCP tool parameter wire compatibility.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from marivo.contracts.generated import OSIDocument
from marivo.contracts.generated.osi import (
    AIContext1,
    Dataset,
    Dimension,
    Expression,
    Metric,
    Relationship,
    SemanticModel,
)
from marivo.contracts.generated.osi import (
    FieldModel as OsiField,
)


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


class McpFieldUpdatePayload(BaseModel):
    """Payload for update_field MCP tool."""

    model_config = ConfigDict(extra="forbid")

    expression: Expression | None = Field(None, description="Multi-dialect field expression")
    dimension: Dimension | None = Field(None, description="Dimension metadata")
    label: str | None = Field(None, description="Label for categorization")
    description: str | None = Field(None, description="Human-readable description")
    ai_context: str | AIContext1 | None = Field(None, description="Additional context for AI tools")


# ---------------------------------------------------------------------------
# JSON string coercer for create payload parameters
# ---------------------------------------------------------------------------


def _coerce_json_string_to_dict(v: Any) -> Any:
    """Coerce JSON-encoded strings to dicts for create payload parameters.

    MCP clients (notably Claude Code) may pass complex nested objects as
    JSON strings rather than native dicts.  FastMCP's ``pre_parse_json``
    attempts ``json.loads`` but silently skips on failure, letting the raw
    string through to Pydantic which then rejects it with a cryptic
    ``model_type`` error.  This validator provides an explicit coercion
    path with a clear error message.
    """
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as err:
            raise ValueError(
                "payload must be a structured object or a valid JSON string, "
                f"but received an invalid JSON string: {v[:120]}..."
            ) from err
        if not isinstance(parsed, dict):
            raise ValueError(f"payload must decode to a JSON object, got {type(parsed).__name__}")
        return parsed
    return v


McpSemanticModelPayload = Annotated[SemanticModel, BeforeValidator(_coerce_json_string_to_dict)]
McpOsiDocumentPayload = Annotated[OSIDocument, BeforeValidator(_coerce_json_string_to_dict)]
McpDatasetPayload = Annotated[Dataset, BeforeValidator(_coerce_json_string_to_dict)]
McpFieldPayload = Annotated[OsiField, BeforeValidator(_coerce_json_string_to_dict)]
McpMetricPayload = Annotated[Metric, BeforeValidator(_coerce_json_string_to_dict)]
McpRelationshipPayload = Annotated[Relationship, BeforeValidator(_coerce_json_string_to_dict)]
