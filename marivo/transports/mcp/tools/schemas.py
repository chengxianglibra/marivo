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


class McpDialect(BaseModel):
    """One SQL expression dialect alternative for AOI Expression."""

    model_config = ConfigDict(extra="forbid")

    dialect: str = Field(
        default="ANSI_SQL",
        description="Expression dialect identifier. Defaults to ANSI_SQL.",
    )
    expression: str = Field(
        ...,
        min_length=1,
        description="Predicate expression text in the declared dialect.",
    )


class McpExpression(BaseModel):
    """AOI Expression object used by MCP intent filters."""

    model_config = ConfigDict(extra="forbid")

    dialects: list[McpDialect] = Field(
        ...,
        min_length=1,
        description=(
            "One or more dialect-specific predicate expressions, e.g. "
            "[{'dialect': 'ANSI_SQL', 'expression': \"region = 'US'\"}]."
        ),
    )


McpStructuredObject = Annotated[JsonObject, BeforeValidator(_reject_json_string)]


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
    """Derived-intent slice: time_scope + optional population scope."""

    model_config = ConfigDict(extra="allow")

    time_scope: McpTimeScope
    scope: ObserveScope | None = None


class McpAoiSliceRef(BaseModel):
    """AOI-aligned slice for source-type atomic intents."""

    model_config = ConfigDict(extra="forbid")

    time_scope: McpTimeScope
    filter: McpExpression | None = Field(
        default=None,
        description="Optional AOI Expression filter for this time slice.",
    )


class McpTestHypothesis(BaseModel):
    """MCP-visible test hypothesis choices for the fixed AOI test family."""

    model_config = ConfigDict(extra="forbid")

    alternative: Literal["two_sided", "greater", "less"]
    significance: Literal["conservative", "balanced", "aggressive"] = Field(
        ...,
        description=(
            "Significance preset for the hypothesis test: conservative=0.01 "
            "uses a stricter threshold to reduce false positives; balanced=0.05 "
            "is the default statistical threshold; aggressive=0.10 is more "
            "exploratory and more likely to reject the null hypothesis."
        ),
    )


class McpValidateHypothesis(BaseModel):
    """MCP-visible validate hypothesis choices; family is fixed internally."""

    model_config = ConfigDict(extra="forbid")

    alternative: Literal["two_sided", "greater", "less"] | None = None
    significance: Literal["conservative", "balanced", "aggressive"] | None = Field(
        default=None,
        description=(
            "Significance preset for the validation test: conservative=0.01, "
            "balanced=0.05, aggressive=0.10."
        ),
    )


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
