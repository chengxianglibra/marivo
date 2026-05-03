from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from marivo_mcp.config import MarivoMcpConfig
from marivo_mcp.http_client import MarivoHttpClient
from marivo_mcp.openapi_cache import OpenApiResponseCache
from marivo_mcp.sdk import FastMcpServer

_ParamScalar = str | int | float | bool | None
_ParamList = list[_ParamScalar]
_ParamValue = _ParamScalar | _ParamList
_OBSERVE_TIME_SCOPE_CANONICAL_MESSAGE = (
    "observe.time_scope requires canonical object shape, not shorthand string. "
    'Use {"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}. '
    "Range is half-open [start, end) — end is EXCLUSIVE. "
    "If you want inclusive YYYY-MM-DD, pass the next day as end."
)
_STRUCTURED_OBJECT_MESSAGE_SUFFIX = "Pass a structured object, not a JSON-encoded string."
_ATTRIBUTE_OBSERVATION_INPUT_MESSAGE = (
    "attribute left/right.time_scope requires canonical object shape. "
    'Use {"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}. '
    "Range is half-open [start, end) — end is EXCLUSIVE. "
    "If you want inclusive YYYY-MM-DD, pass the next day as end."
)

type JsonObject = dict[str, object]


def _reject_observe_time_scope_string(value: object) -> object:
    if isinstance(value, str):
        raise PydanticCustomError(
            "observe_time_scope_canonical_required",
            _OBSERVE_TIME_SCOPE_CANONICAL_MESSAGE,
        )
    return value


def _reject_json_string(value: object) -> object:
    if isinstance(value, str):
        raise PydanticCustomError(
            "mcp_structured_object_required",
            _STRUCTURED_OBJECT_MESSAGE_SUFFIX,
        )
    return value


type McpObserveTimeScope = Annotated[
    JsonObject,
    BeforeValidator(_reject_observe_time_scope_string),
    Field(
        description=(
            "Canonical object only; shorthand strings are NOT accepted. "
            "Range is half-open [start, end) — end is EXCLUSIVE. "
            "Examples:\n"
            '  CORRECT: {"kind":"range","start":"2024-03-01","end":"2024-04-01"} '
            "(covers March 1-31 inclusive)\n"
            '  WRONG: "2024-03-01~2024-03-31" (string rejected)\n'
            '  WRONG: {"kind":"range","start":"2024-03-01","end":"2024-03-31"} '
            "(misses March 31 — end is exclusive)."
        ),
    ),
]

type McpStructuredObject = Annotated[
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


class McpMetricHeader(BaseModel):
    """MCP-side early validation for metric header fields."""

    model_config = ConfigDict(extra="forbid")

    metric_ref: str
    display_name: str | None = None
    description: str | None = None
    metric_family: str
    population_subject_ref: str | None = None
    observed_entity_ref: str
    observation_grain_ref: str
    sample_kind: str
    value_semantics: str
    aggregation_scope: str | None = None
    primary_time_ref: str | None = None
    additivity_constraints: dict[str, object]
    default_predicate_refs: list[str] | None = None
    metric_contract_version: str

    @field_validator("metric_ref")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        if not value.startswith("metric."):
            raise ValueError("metric_ref must start with 'metric.'")
        return value

    @field_validator("observed_entity_ref")
    @classmethod
    def _validate_observed_entity_ref(cls, value: str) -> str:
        if not value.startswith("entity."):
            raise ValueError("observed_entity_ref must start with 'entity.'")
        return value

    @field_validator("observation_grain_ref")
    @classmethod
    def _validate_observation_grain_ref(cls, value: str) -> str:
        if not value.startswith("grain."):
            raise ValueError("observation_grain_ref must start with 'grain.'")
        return value

    @field_validator("metric_contract_version")
    @classmethod
    def _validate_metric_contract_version(cls, value: str) -> str:
        if not value.startswith("metric."):
            raise ValueError("metric_contract_version must start with 'metric.'")
        return value


class McpEnumSetHeader(BaseModel):
    """MCP-side early validation for enum set header fields."""

    model_config = ConfigDict(extra="forbid")

    enum_set_ref: str
    value_type: str

    @field_validator("enum_set_ref")
    @classmethod
    def _validate_enum_set_ref(cls, value: str) -> str:
        if not value.startswith("enum."):
            raise ValueError("enum_set_ref must start with 'enum.'")
        return value


class ObserveScope(BaseModel):
    """Non-time population scope accepted by typed intent MCP tools."""

    constraints: dict[str, Any] | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate: dict[str, Any] | None = Field(
        default=None,
        description=(
            "DEPRECATED: Use predicate_ref instead. Structured non-time predicate AST. "
            "Must not contain time conditions."
        ),
    )
    predicate_ref: str | None = Field(
        default=None,
        description=(
            "Reference to a governed predicate (predicate.*) declaring request_scope usage. "
            "Mutually exclusive with predicate."
        ),
    )

    @field_validator("predicate_ref")
    @classmethod
    def _validate_predicate_ref_prefix(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("predicate."):
            raise ValueError("predicate_ref must start with 'predicate.'")
        return value

    @model_validator(mode="after")
    def _validate_mutual_exclusion(self) -> ObserveScope:
        if self.predicate is not None and self.predicate_ref is not None:
            raise ValueError("predicate and predicate_ref are mutually exclusive")
        return self


def _tool_metadata(
    method: str, path: str
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        func._marivo_http_method = method  # type: ignore[attr-defined]
        func._marivo_http_path = path  # type: ignore[attr-defined]
        return func

    return decorator


def _encode_openapi_path(path: str) -> str:
    """Encode an OpenAPI path to unpadded base64url for the path fragment endpoint."""
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _require_structured_object(value: object, *, field_name: str) -> object:
    try:
        return _reject_json_string(value)
    except PydanticCustomError as error:
        raise ValueError(f"{field_name}: {error.message()}") from error


def _require_observe_time_scope_object(value: object) -> object:
    try:
        return _reject_observe_time_scope_string(value)
    except PydanticCustomError as error:
        raise ValueError(error.message()) from error


def _coerce_mcp_model[T: BaseModel](
    value: object,
    model_type: type[T],
    *,
    field_name: str,
) -> T:
    _require_structured_object(value, field_name=field_name)
    if isinstance(value, model_type):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        return model_type.model_validate(value)
    except ValueError as error:
        raise ValueError(f"{field_name}: {error}") from error


def register_tools(
    server: FastMcpServer,
    config: MarivoMcpConfig,
    *,
    client_factory: Callable[[MarivoMcpConfig], MarivoHttpClient] | None = None,
    openapi_cache: OpenApiResponseCache | None = None,
) -> None:
    """Register the HTTP-backed MCP tools over the canonical Marivo API."""
    resolved_client_factory = client_factory or MarivoHttpClient
    client = resolved_client_factory(config)
    discovery_cache = openapi_cache or OpenApiResponseCache(config.openapi_cache_ttl_sec)

    @server.tool()
    @_tool_metadata("POST", "/sessions")
    def create_session(
        goal: str,
        budget: dict[str, object] | None = None,
        policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create an investigation session via POST /sessions using the canonical session root request fields."""
        return client.request_envelope(
            "POST",
            "/sessions",
            json_body=_compact_body(
                goal=goal,
                budget=budget,
                policy=policy,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, object]:
        """Read one canonical session root via GET /sessions/{session_id} without inlining state or proposition context."""
        return client.request_envelope("GET", f"/sessions/{session_id}").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/terminate")
    def terminate_session(
        session_id: str,
        terminal_reason: str = "user_closed",
    ) -> dict[str, object]:
        """Explicitly terminate one session via POST /sessions/{session_id}/terminate using the canonical session lifecycle contract."""
        return client.request_envelope(
            "POST",
            f"/sessions/{session_id}/terminate",
            json_body=_compact_body(terminal_reason=terminal_reason),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/sessions/{session_id}/state")
    def get_session_state(
        session_id: str,
        metric: str | None = None,
        entity: str | None = None,
        proposition_type: list[str] | None = None,
        origin_kind: list[str] | None = None,
        assessment_presence: str | None = None,
        assessment_status: list[str] | None = None,
        has_blocking_gaps: bool | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        """Read the session-level canonical decision surface via GET /sessions/{session_id}/state; use query_session_state when slice filtering is required."""
        return client.request_envelope(
            "GET",
            f"/sessions/{session_id}/state",
            params=_compact_params(
                metric=metric,
                entity=entity,
                proposition_type=_normalize_multi_param(proposition_type),
                origin_kind=_normalize_multi_param(origin_kind),
                assessment_presence=assessment_presence,
                assessment_status=_normalize_multi_param(assessment_status),
                has_blocking_gaps=has_blocking_gaps,
                limit=limit,
                page_token=page_token,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/state/query")
    def query_session_state(
        session_id: str,
        metric: str | None = None,
        entity: str | None = None,
        slice: dict[str, object] | None = None,
        proposition_types: list[str] | None = None,
        origin_kinds: list[str] | None = None,
        assessment_presence: str | None = None,
        assessment_statuses: list[str] | None = None,
        has_blocking_gaps: bool | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        """Read the canonical session state via POST /sessions/{session_id}/state/query when structured filters such as slice are needed."""
        return client.request_envelope(
            "POST",
            f"/sessions/{session_id}/state/query",
            params=_compact_params(page_token=page_token),
            json_body=_compact_body(
                metric=metric,
                entity=entity,
                slice=slice,
                proposition_types=proposition_types,
                origin_kinds=origin_kinds,
                assessment_presence=assessment_presence,
                assessment_statuses=assessment_statuses,
                has_blocking_gaps=has_blocking_gaps,
                limit=limit,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/sessions/{session_id}/propositions/{proposition_id}/context")
    def get_proposition_context(session_id: str, proposition_id: str) -> dict[str, object]:
        """Read the proposition-level canonical minimal closure via GET /sessions/{session_id}/propositions/{proposition_id}/context."""
        return client.request_envelope(
            "GET",
            f"/sessions/{session_id}/propositions/{proposition_id}/context",
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/observe")
    def observe(
        session_id: str,
        metric: str,
        time_scope: McpObserveTimeScope,
        result_mode: Literal["standard", "numeric_sample_summary", "rate_sample_summary"] = (
            "standard"
        ),
        calendar_policy_ref: str | None = None,
        scope: ObserveScope | None = None,
        granularity: Literal["hour", "day", "week", "month"] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/observe using the canonical ObserveRequest body.

        time_scope MUST be a structured object (strings rejected). Range is half-open [start, end).
        Examples:
          CORRECT: {"kind":"range","start":"2024-03-01","end":"2024-04-01"} (covers March 1-31)
          WRONG: "2024-03-01~2024-03-31" (shorthand string rejected)
          WRONG: {"kind":"range","start":"2024-03-01","end":"2024-03-31"} (misses March 31)

        On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_observe_time_scope_object(time_scope)
        return _intent_request(
            client,
            session_id,
            "observe",
            metric=metric,
            result_mode=result_mode,
            time_scope=time_scope,
            calendar_policy_ref=calendar_policy_ref,
            scope=scope,
            granularity=granularity,
            dimensions=dimensions,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/compare")
    def compare(
        session_id: str,
        left_ref: McpObservationRef,
        right_ref: McpObservationRef,
        mode: Literal["auto", "scalar", "segmented", "time_series"] = "auto",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/compare using CompareRequest.

        left_ref/right_ref are structured observe refs:
          {"step_id":"step_obs_current","step_type":"observe"}
          {"step_id":"step_obs_baseline","step_type":"observe"}

        Use get_openapi_fragment(path="/sessions/{session_id}/intents/compare",
        operation="post", expand=["request","schemas"], depth=2) for the canonical contract.
        On 422, follow error.guidance.contract_url, schema_url, and examples."""
        left_ref = _coerce_mcp_model(left_ref, McpObservationRef, field_name="left_ref")
        right_ref = _coerce_mcp_model(right_ref, McpObservationRef, field_name="right_ref")
        return _intent_request(
            client,
            session_id,
            "compare",
            left_ref=left_ref,
            right_ref=right_ref,
            mode=mode,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/decompose")
    def decompose(
        session_id: str,
        compare_ref: McpCompareArtifactRef,
        dimension: str,
        method: str = "delta_share",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/decompose using DecomposeRequest.

        compare_ref is a structured compare ref:
          {"step_id":"step_compare_1","step_type":"compare"}

        On 422, follow error.guidance.contract_url, schema_url, and examples."""
        compare_ref = _coerce_mcp_model(
            compare_ref,
            McpCompareArtifactRef,
            field_name="compare_ref",
        )
        return _intent_request(
            client,
            session_id,
            "decompose",
            compare_ref=compare_ref,
            dimension=dimension,
            method=method,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/correlate")
    def correlate(
        session_id: str,
        left_ref: McpStructuredObject,
        right_ref: McpStructuredObject,
        method: str = "spearman",
        min_pairs: int = 5,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/correlate using the canonical CorrelateRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_structured_object(left_ref, field_name="left_ref")
        _require_structured_object(right_ref, field_name="right_ref")
        return _intent_request(
            client,
            session_id,
            "correlate",
            left_ref=left_ref,
            right_ref=right_ref,
            method=method,
            min_pairs=min_pairs,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/detect")
    def detect(
        session_id: str,
        metric: str,
        time_scope: McpDetectTimeScope,
        granularity: Literal["hour", "day", "week", "month"],
        scope: ObserveScope | None = None,
        split_by: str | None = None,
        profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = "auto",
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "balanced",
        limit: int | None = None,
        max_series: int | None = None,
        patterns: list[Literal["point_anomaly", "period_shift"]] | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/detect using DetectRequest.

        time_scope uses observe's range form, with scan granularity as a top-level field:
          {"time_scope":{"kind":"range","start":"2026-04-01","end":"2026-04-08"},"granularity":"day"}

        Use get_openapi_fragment(path="/sessions/{session_id}/intents/detect",
        operation="post", expand=["request","schemas"], depth=2) for the canonical contract.
        On 422, follow error.guidance.contract_url, schema_url, and examples."""
        time_scope = _coerce_mcp_model(
            time_scope,
            McpDetectTimeScope,
            field_name="time_scope",
        )
        return _intent_request(
            client,
            session_id,
            "detect",
            metric=metric,
            time_scope=time_scope,
            granularity=granularity,
            scope=scope,
            split_by=split_by,
            profile=profile,
            sensitivity=sensitivity,
            limit=limit,
            max_series=max_series,
            patterns=patterns,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/test")
    def test_intent(
        session_id: str,
        left_ref: McpStructuredObject,
        right_ref: McpStructuredObject,
        hypothesis: McpStructuredObject,
        method: str = "auto",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/test using the canonical IntentTestRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_structured_object(left_ref, field_name="left_ref")
        _require_structured_object(right_ref, field_name="right_ref")
        _require_structured_object(hypothesis, field_name="hypothesis")
        return _intent_request(
            client,
            session_id,
            "test",
            left_ref=left_ref,
            right_ref=right_ref,
            hypothesis=hypothesis,
            method=method,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/forecast")
    def forecast(
        session_id: str,
        source_ref: McpStructuredObject,
        horizon: int,
        profile: str = "auto",
        interval_level: float | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/forecast using the canonical ForecastRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_structured_object(source_ref, field_name="source_ref")
        return _intent_request(
            client,
            session_id,
            "forecast",
            source_ref=source_ref,
            horizon=horizon,
            profile=profile,
            interval_level=interval_level,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/attribute")
    def attribute(
        session_id: str,
        metric: str,
        left: McpStructuredObject,
        right: McpStructuredObject,
        dimensions: list[str],
        decomposition_method: str = "delta_share",
        decomposition_limit: int = 5,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/attribute using the canonical AttributeRequest body.

        left/right MUST be structured objects containing time_scope. Range is half-open [start, end).
        Examples:
          CORRECT: {"time_scope":{"kind":"range","start":"2024-03-01","end":"2024-04-01"}}
          WRONG: {"time_scope":"2024-03-01~2024-03-31"} (string rejected)
          WRONG: {"time_scope":{"kind":"range","start":"2024-03-01","end":"2024-03-31"}}
                  (misses March 31 — end is exclusive)

        On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_structured_object(left, field_name="left")
        _require_structured_object(right, field_name="right")
        return _intent_request(
            client,
            session_id,
            "attribute",
            metric=metric,
            left=left,
            right=right,
            dimensions=dimensions,
            decomposition_method=decomposition_method,
            decomposition_limit=decomposition_limit,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/diagnose")
    def diagnose(
        session_id: str,
        metric: str,
        candidate_dimensions: list[str],
        mode: Literal["auto_detect", "explicit_compare"] = "auto_detect",
        time_scope: McpStructuredObject | None = None,
        granularity: Literal["hour", "day", "week", "month"] | None = None,
        current: McpStructuredObject | None = None,
        baseline: McpStructuredObject | None = None,
        scope: ObserveScope | None = None,
        detect_split_by: str | None = None,
        profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = "auto",
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "balanced",
        candidate_limit: int | None = None,
        followup_limit: int | None = 3,
        decomposition_limit: int | None = 5,
        patterns: list[Literal["point_anomaly", "period_shift"]] | None = None,
        baseline_policy: Literal[
            "previous_adjacent_equal_length"
        ] = "previous_adjacent_equal_length",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/diagnose using the canonical DiagnoseRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        if time_scope is not None:
            _require_structured_object(time_scope, field_name="time_scope")
        if current is not None:
            _require_structured_object(current, field_name="current")
        if baseline is not None:
            _require_structured_object(baseline, field_name="baseline")
        return _intent_request(
            client,
            session_id,
            "diagnose",
            mode=mode,
            metric=metric,
            time_scope=time_scope,
            granularity=granularity,
            current=current,
            baseline=baseline,
            candidate_dimensions=candidate_dimensions,
            scope=scope,
            detect_split_by=detect_split_by,
            profile=profile,
            sensitivity=sensitivity,
            candidate_limit=candidate_limit,
            followup_limit=followup_limit,
            decomposition_limit=decomposition_limit,
            patterns=patterns,
            baseline_policy=baseline_policy,
        )

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/intents/validate")
    def validate(
        session_id: str,
        metric: str,
        left: McpStructuredObject,
        right: McpStructuredObject,
        sample_kind: Literal["auto", "numeric", "rate"] | None = None,
        hypothesis: McpStructuredObject | None = None,
        method: Literal["auto", "welch_t", "two_proportion_z"] | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/validate using the canonical ValidateRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        _require_structured_object(left, field_name="left")
        _require_structured_object(right, field_name="right")
        if hypothesis is not None:
            _require_structured_object(hypothesis, field_name="hypothesis")
        return _intent_request(
            client,
            session_id,
            "validate",
            metric=metric,
            left=left,
            right=right,
            sample_kind=sample_kind,
            hypothesis=hypothesis,
            method=method,
        )

    @server.tool()
    @_tool_metadata("GET", "/health")
    def health_check() -> dict[str, object]:
        """Check Marivo service health via GET /health using the shared MCP HTTP envelope."""
        return client.request_envelope("GET", "/health").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/openapi/index")
    def list_openapi_paths() -> dict[str, object]:
        """List canonical OpenAPI paths and schema names via GET /openapi/index for low-cost contract discovery."""
        return _openapi_cached_request(
            client,
            discovery_cache,
            ("openapi_index",),
            "/openapi/index",
        )

    @server.tool()
    @_tool_metadata("GET", "/openapi/schemas/{schema_name}")
    def get_openapi_schema(schema_name: str, depth: int = 1) -> dict[str, object]:
        """Read one canonical component schema via GET /openapi/schemas/{schema_name}."""
        return _openapi_cached_request(
            client,
            discovery_cache,
            ("openapi_schema", schema_name, depth),
            f"/openapi/schemas/{schema_name}",
            params={"depth": depth},
        )

    @server.tool()
    @_tool_metadata("GET", "/openapi/fragment")
    def get_openapi_fragment(
        path: str,
        operation: str | None = None,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Read a canonical OpenAPI fragment via GET /openapi/fragment without consulting a local schema copy."""
        normalized_expand = _normalize_string_multi_param(expand)
        request_expand = _normalize_multi_param(expand)
        return _openapi_cached_request(
            client,
            discovery_cache,
            ("openapi_fragment", path, operation, tuple(sorted(normalized_expand or [])), depth),
            "/openapi/fragment",
            params=_compact_params(
                path=path,
                operation=operation,
                expand=request_expand,
                depth=depth,
            ),
        )

    @server.tool()
    @_tool_metadata("GET", "/openapi/paths/{encoded_path}")
    def get_openapi_path_fragment(
        path: str,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Read one canonical OpenAPI path item via GET /openapi/paths/{encoded_path}. Accepts the raw path (e.g., '/sessions'); the tool automatically encodes it as unpadded base64url. Use list_openapi_paths to discover available paths and their encoded forms."""
        encoded_path = _encode_openapi_path(path)
        normalized_expand = _normalize_string_multi_param(expand)
        request_expand = _normalize_multi_param(expand)
        return _openapi_cached_request(
            client,
            discovery_cache,
            (
                "openapi_path_fragment",
                path,
                tuple(sorted(normalized_expand or [])),
                depth,
            ),
            f"/openapi/paths/{encoded_path}",
            params=_compact_params(expand=request_expand, depth=depth),
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/entities")
    def create_entity(
        header: dict[str, object],
        interface_contract: dict[str, object],
    ) -> dict[str, object]:
        """Create one draft entity via POST /semantic/entities using the canonical TypedEntityCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/entities",
            header=header,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/entities")
    def list_entities(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List entities via GET /semantic/entities; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/entities",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/entities/{entity_id}")
    def get_entity(object_id: str | None = None, entity_id: str | None = None) -> dict[str, object]:
        """Read one entity via GET /semantic/entities/{entity_id}; accepts an internal contract id or canonical entity ref, and prefers object_id over the legacy entity_id name."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_read_request(client, f"/semantic/entities/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/entities/{entity_id}")
    def update_entity(
        object_id: str | None = None,
        entity_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        interface_contract: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft entity via PUT /semantic/entities/{entity_id} using the canonical TypedEntityUpdateRequest fields."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/entities/{resolved_id}",
            display_name=display_name,
            description=description,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/entities/{entity_id}/validate")
    def validate_entity(
        object_id: str | None = None, entity_id: str | None = None
    ) -> dict[str, object]:
        """Validate one entity via POST /semantic/entities/{entity_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_action_request(client, f"/semantic/entities/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/entities/{entity_id}/activate")
    def activate_entity(
        object_id: str | None = None, entity_id: str | None = None
    ) -> dict[str, object]:
        """Activate one entity via POST /semantic/entities/{entity_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_action_request(client, f"/semantic/entities/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/entities/{entity_id}/deprecate")
    def deprecate_entity(
        object_id: str | None = None, entity_id: str | None = None
    ) -> dict[str, object]:
        """Deprecate one entity via POST /semantic/entities/{entity_id}/deprecate."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_action_request(client, f"/semantic/entities/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/entities/{entity_id}/publish")
    def publish_entity(
        object_id: str | None = None, entity_id: str | None = None
    ) -> dict[str, object]:
        """Compatibility alias for activate_entity via POST /semantic/entities/{entity_id}/publish."""
        resolved_id = _resolve_object_id(object_id, entity_id, legacy_name="entity_id")
        return _semantic_publish_request(client, f"/semantic/entities/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/semantic/batch")
    def semantic_batch(request: McpStructuredObject) -> dict[str, object]:
        """Run semantic authoring operations via POST /semantic/batch."""
        return client.request_envelope(
            "POST",
            "/semantic/batch",
            json_body=_compact_body(**request),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic/grains")
    def list_grains() -> dict[str, object]:
        """List grain refs observed in metric headers and process objects."""
        return client.request_envelope("GET", "/semantic/grains").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/semantic/metrics")
    def create_metric(
        header: McpMetricHeader,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Create one draft metric via POST /semantic/metrics using the canonical TypedMetricCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/metrics",
            header=header,
            payload=payload,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/metrics")
    def list_metrics(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List metrics via GET /semantic/metrics; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/metrics",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/metrics/{metric_id}")
    def get_metric(object_id: str | None = None, metric_id: str | None = None) -> dict[str, object]:
        """Read one metric via GET /semantic/metrics/{metric_id}; accepts an internal contract id or canonical metric ref, and prefers object_id over the legacy metric_id name."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_read_request(client, f"/semantic/metrics/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/metrics/{metric_id}")
    def update_metric(
        object_id: str | None = None,
        metric_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft metric via PUT /semantic/metrics/{metric_id} using the canonical TypedMetricUpdateRequest fields."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/metrics/{resolved_id}",
            display_name=display_name,
            description=description,
            payload=payload,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/metrics/{metric_id}/validate")
    def validate_metric(
        object_id: str | None = None, metric_id: str | None = None
    ) -> dict[str, object]:
        """Validate one metric via POST /semantic/metrics/{metric_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_action_request(client, f"/semantic/metrics/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/metrics/{metric_id}/activate")
    def activate_metric(
        object_id: str | None = None, metric_id: str | None = None
    ) -> dict[str, object]:
        """Activate one metric via POST /semantic/metrics/{metric_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_action_request(client, f"/semantic/metrics/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/metrics/{metric_id}/deprecate")
    def deprecate_metric(
        object_id: str | None = None, metric_id: str | None = None
    ) -> dict[str, object]:
        """Deprecate one metric via POST /semantic/metrics/{metric_id}/deprecate."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_action_request(client, f"/semantic/metrics/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/metrics/{metric_id}/publish")
    def publish_metric(
        object_id: str | None = None, metric_id: str | None = None
    ) -> dict[str, object]:
        """Compatibility alias for activate_metric via POST /semantic/metrics/{metric_id}/publish."""
        resolved_id = _resolve_object_id(object_id, metric_id, legacy_name="metric_id")
        return _semantic_publish_request(client, f"/semantic/metrics/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/semantic/process-objects")
    def create_process_object(
        header: dict[str, object],
        interface_contract: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Create one draft process object via POST /semantic/process-objects using the canonical ProcessObjectCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/process-objects",
            header=header,
            interface_contract=interface_contract,
            payload=payload,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/process-objects")
    def list_process_objects(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List process objects via GET /semantic/process-objects; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/process-objects",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/process-objects/{process_contract_id}")
    def get_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Read one process object via GET /semantic/process-objects/{process_contract_id}; prefer object_id over the legacy process_contract_id name."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_read_request(
            client,
            f"/semantic/process-objects/{resolved_id}",
        )

    @server.tool()
    @_tool_metadata("PUT", "/semantic/process-objects/{process_contract_id}")
    def update_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        interface_contract: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft process object via PUT /semantic/process-objects/{process_contract_id} using the canonical ProcessObjectUpdateRequest fields."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/process-objects/{resolved_id}",
            display_name=display_name,
            description=description,
            interface_contract=interface_contract,
            payload=payload,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/process-objects/{process_contract_id}/validate")
    def validate_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one process object via POST /semantic/process-objects/{process_contract_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/process-objects/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/process-objects/{process_contract_id}/activate")
    def activate_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one process object via POST /semantic/process-objects/{process_contract_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/process-objects/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/process-objects/{process_contract_id}/deprecate")
    def deprecate_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one process object via POST /semantic/process-objects/{process_contract_id}/deprecate."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_action_request(
            client, f"/semantic/process-objects/{resolved_id}/deprecate"
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/process-objects/{process_contract_id}/publish")
    def publish_process_object(
        object_id: str | None = None,
        process_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_process_object via POST /semantic/process-objects/{process_contract_id}/publish."""
        resolved_id = _resolve_object_id(
            object_id, process_contract_id, legacy_name="process_contract_id"
        )
        return _semantic_publish_request(
            client,
            f"/semantic/process-objects/{resolved_id}/publish",
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/dimensions")
    def create_dimension(
        header: dict[str, object],
        interface_contract: dict[str, object],
    ) -> dict[str, object]:
        """Create one draft dimension via POST /semantic/dimensions using the canonical DimensionCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/dimensions",
            header=header,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/dimensions")
    def list_dimensions(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List dimensions via GET /semantic/dimensions; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/dimensions",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/dimensions/{dimension_contract_id}")
    def get_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Read one dimension via GET /semantic/dimensions/{dimension_contract_id}; prefer object_id over the legacy dimension_contract_id name."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_read_request(client, f"/semantic/dimensions/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/dimensions/{dimension_contract_id}")
    def update_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        interface_contract: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft dimension via PUT /semantic/dimensions/{dimension_contract_id} using the canonical DimensionUpdateRequest fields."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/dimensions/{resolved_id}",
            display_name=display_name,
            description=description,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/dimensions/{dimension_contract_id}/validate")
    def validate_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one dimension via POST /semantic/dimensions/{dimension_contract_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/dimensions/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/dimensions/{dimension_contract_id}/activate")
    def activate_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one dimension via POST /semantic/dimensions/{dimension_contract_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/dimensions/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/dimensions/{dimension_contract_id}/deprecate")
    def deprecate_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one dimension via POST /semantic/dimensions/{dimension_contract_id}/deprecate."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/dimensions/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/dimensions/{dimension_contract_id}/publish")
    def publish_dimension(
        object_id: str | None = None,
        dimension_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_dimension via POST /semantic/dimensions/{dimension_contract_id}/publish."""
        resolved_id = _resolve_object_id(
            object_id, dimension_contract_id, legacy_name="dimension_contract_id"
        )
        return _semantic_publish_request(client, f"/semantic/dimensions/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/semantic/time")
    def create_time_semantic(header: dict[str, object]) -> dict[str, object]:
        """Create one draft time semantic via POST /semantic/time using the canonical TimeCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/time",
            header=header,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/time")
    def list_time_semantics(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List time semantics via GET /semantic/time; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/time",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/time/{time_contract_id}")
    def get_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Read one time semantic via GET /semantic/time/{time_contract_id}; prefer object_id over the legacy time_contract_id name."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_read_request(client, f"/semantic/time/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/time/{time_contract_id}")
    def update_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        semantic_roles: list[str] | None = None,
    ) -> dict[str, object]:
        """Update one draft time semantic via PUT /semantic/time/{time_contract_id} using the canonical TimeUpdateRequest fields."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/time/{resolved_id}",
            display_name=display_name,
            description=description,
            semantic_roles=semantic_roles,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/time/{time_contract_id}/validate")
    def validate_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one time semantic via POST /semantic/time/{time_contract_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/time/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/time/{time_contract_id}/activate")
    def activate_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one time semantic via POST /semantic/time/{time_contract_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/time/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/time/{time_contract_id}/deprecate")
    def deprecate_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one time semantic via POST /semantic/time/{time_contract_id}/deprecate."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/time/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/time/{time_contract_id}/publish")
    def publish_time_semantic(
        object_id: str | None = None,
        time_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_time_semantic via POST /semantic/time/{time_contract_id}/publish."""
        resolved_id = _resolve_object_id(
            object_id, time_contract_id, legacy_name="time_contract_id"
        )
        return _semantic_publish_request(client, f"/semantic/time/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/semantic/enum-sets")
    def create_enum_set(
        header: McpEnumSetHeader,
        display_name: str,
        versions: list[dict[str, object]],
        description: str | None = None,
    ) -> dict[str, object]:
        """Create one draft enum set via POST /semantic/enum-sets using the canonical EnumSetCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/enum-sets",
            header=header,
            display_name=display_name,
            description=description,
            versions=versions,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/enum-sets")
    def list_enum_sets(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List enum sets via GET /semantic/enum-sets; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/enum-sets",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/enum-sets/{enum_set_contract_id}")
    def get_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Read one enum set via GET /semantic/enum-sets/{enum_set_contract_id}; prefer object_id over the legacy enum_set_contract_id name."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_read_request(client, f"/semantic/enum-sets/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/enum-sets/{enum_set_contract_id}")
    def update_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        versions: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Update one draft enum set via PUT /semantic/enum-sets/{enum_set_contract_id} using the canonical EnumSetUpdateRequest fields."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/enum-sets/{resolved_id}",
            display_name=display_name,
            description=description,
            versions=versions,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/enum-sets/{enum_set_contract_id}/validate")
    def validate_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one enum set via POST /semantic/enum-sets/{enum_set_contract_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/enum-sets/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/enum-sets/{enum_set_contract_id}/activate")
    def activate_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one enum set via POST /semantic/enum-sets/{enum_set_contract_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/enum-sets/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/enum-sets/{enum_set_contract_id}/deprecate")
    def deprecate_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one enum set via POST /semantic/enum-sets/{enum_set_contract_id}/deprecate."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_action_request(client, f"/semantic/enum-sets/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/enum-sets/{enum_set_contract_id}/publish")
    def publish_enum_set(
        object_id: str | None = None,
        enum_set_contract_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_enum_set via POST /semantic/enum-sets/{enum_set_contract_id}/publish."""
        resolved_id = _resolve_object_id(
            object_id, enum_set_contract_id, legacy_name="enum_set_contract_id"
        )
        return _semantic_publish_request(client, f"/semantic/enum-sets/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/semantic/relationships")
    def create_relationship(
        relationship_ref: str,
        left_entity_ref: str,
        right_entity_ref: str,
        key_alignment: dict[str, object],
        cardinality: str,
        display_name: str | None = None,
        description: str | None = None,
        time_alignment: dict[str, object] | None = None,
        grain_compatibility: dict[str, object] | None = None,
        snapshot_effective_window_alignment: dict[str, object] | None = None,
        catalog_metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create one draft entity relationship via POST /semantic/relationships."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/relationships",
            relationship_ref=relationship_ref,
            display_name=display_name,
            description=description,
            left_entity_ref=left_entity_ref,
            right_entity_ref=right_entity_ref,
            key_alignment=key_alignment,
            time_alignment=time_alignment,
            cardinality=cardinality,
            grain_compatibility=grain_compatibility,
            snapshot_effective_window_alignment=snapshot_effective_window_alignment,
            catalog_metadata=catalog_metadata,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/relationships")
    def list_relationships(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
        left_entity_ref: str | None = None,
        right_entity_ref: str | None = None,
    ) -> dict[str, object]:
        """List entity relationships via GET /semantic/relationships."""
        return _semantic_read_request(
            client,
            "/semantic/relationships",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
            extra_params={
                "left_entity_ref": left_entity_ref,
                "right_entity_ref": right_entity_ref,
            },
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/relationships/{relationship_id}")
    def get_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, object]:
        """Read one entity relationship via GET /semantic/relationships/{relationship_id}."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_read_request(client, f"/semantic/relationships/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/relationships/{relationship_id}")
    def update_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        key_alignment: dict[str, object] | None = None,
        time_alignment: dict[str, object] | None = None,
        cardinality: str | None = None,
        grain_compatibility: dict[str, object] | None = None,
        snapshot_effective_window_alignment: dict[str, object] | None = None,
        catalog_metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft entity relationship via PUT /semantic/relationships/{relationship_id}."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/relationships/{resolved_id}",
            display_name=display_name,
            description=description,
            key_alignment=key_alignment,
            time_alignment=time_alignment,
            cardinality=cardinality,
            grain_compatibility=grain_compatibility,
            snapshot_effective_window_alignment=snapshot_effective_window_alignment,
            catalog_metadata=catalog_metadata,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/relationships/{relationship_id}/validate")
    def validate_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one entity relationship via POST /semantic/relationships/{relationship_id}/validate."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_action_request(client, f"/semantic/relationships/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/relationships/{relationship_id}/activate")
    def activate_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one entity relationship via POST /semantic/relationships/{relationship_id}/activate."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_action_request(client, f"/semantic/relationships/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/relationships/{relationship_id}/deprecate")
    def deprecate_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one entity relationship via POST /semantic/relationships/{relationship_id}/deprecate."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_action_request(client, f"/semantic/relationships/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/relationships/{relationship_id}/publish")
    def publish_relationship(
        object_id: str | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_relationship via POST /semantic/relationships/{relationship_id}/publish."""
        resolved_id = _resolve_object_id(object_id, relationship_id, legacy_name="relationship_id")
        return _semantic_publish_request(client, f"/semantic/relationships/{resolved_id}/publish")

    @server.tool()
    @_tool_metadata("POST", "/compiler/compatibility-profiles")
    def create_compatibility_profile(
        profile_ref: str,
        profile_kind: str,
        subject_kind: str,
        subject_ref: str,
        schema_version: str = "v1",
        requirement: dict[str, object] | None = None,
        capability: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create one draft compatibility profile via POST /compiler/compatibility-profiles using the canonical CompatibilityProfileCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/compiler/compatibility-profiles",
            profile_ref=profile_ref,
            profile_kind=profile_kind,
            schema_version=schema_version,
            subject_kind=subject_kind,
            subject_ref=subject_ref,
            requirement=requirement,
            capability=capability,
        )

    @server.tool()
    @_tool_metadata("GET", "/compiler/compatibility-profiles")
    def list_compatibility_profiles(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
        subject_kind: str | None = None,
        subject_ref: str | None = None,
        left_entity_ref: str | None = None,
        right_entity_ref: str | None = None,
    ) -> dict[str, object]:
        """List compatibility profiles via GET /compiler/compatibility-profiles; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/compiler/compatibility-profiles",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
            extra_params={
                "subject_kind": subject_kind,
                "subject_ref": subject_ref,
                "left_entity_ref": left_entity_ref,
                "right_entity_ref": right_entity_ref,
            },
        )

    @server.tool()
    @_tool_metadata("GET", "/compiler/compatibility-profiles/{profile_id}")
    def get_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Read one compatibility profile via GET /compiler/compatibility-profiles/{profile_id}; accepts an internal profile id or canonical compiler_profile ref, and prefers object_id over the legacy profile_id name."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_read_request(client, f"/compiler/compatibility-profiles/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/compiler/compatibility-profiles/{profile_id}")
    def update_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
        requirement: dict[str, object] | None = None,
        capability: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft compatibility profile via PUT /compiler/compatibility-profiles/{profile_id} using the canonical CompatibilityProfileUpdateRequest fields."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_write_request(
            client,
            "PUT",
            f"/compiler/compatibility-profiles/{resolved_id}",
            requirement=requirement,
            capability=capability,
        )

    @server.tool()
    @_tool_metadata("POST", "/compiler/compatibility-profiles/{profile_id}/validate")
    def validate_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Validate one compatibility profile via POST /compiler/compatibility-profiles/{profile_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_action_request(
            client, f"/compiler/compatibility-profiles/{resolved_id}/validate"
        )

    @server.tool()
    @_tool_metadata("POST", "/compiler/compatibility-profiles/{profile_id}/activate")
    def activate_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Activate one compatibility profile via POST /compiler/compatibility-profiles/{profile_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_action_request(
            client, f"/compiler/compatibility-profiles/{resolved_id}/activate"
        )

    @server.tool()
    @_tool_metadata("POST", "/compiler/compatibility-profiles/{profile_id}/deprecate")
    def deprecate_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Deprecate one compatibility profile via POST /compiler/compatibility-profiles/{profile_id}/deprecate."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_action_request(
            client, f"/compiler/compatibility-profiles/{resolved_id}/deprecate"
        )

    @server.tool()
    @_tool_metadata("POST", "/compiler/compatibility-profiles/{profile_id}/publish")
    def publish_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility alias for activate_compatibility_profile via POST /compiler/compatibility-profiles/{profile_id}/publish."""
        resolved_id = _resolve_object_id(object_id, profile_id, legacy_name="profile_id")
        return _semantic_publish_request(
            client, f"/compiler/compatibility-profiles/{resolved_id}/publish"
        )

    @server.tool()
    @_tool_metadata("GET", "/datasources")
    def list_datasources() -> dict[str, object]:
        """List registered datasources via GET /datasources without adding MCP-only filtering semantics."""
        return client.request_envelope("GET", "/datasources").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/datasources")
    def create_datasource(
        datasource_type: str,
        display_name: str,
        connection: dict[str, object] | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create one datasource via POST /datasources using the canonical datasource_type, display_name, connection, and capabilities fields."""
        return client.request_envelope(
            "POST",
            "/datasources",
            json_body=_compact_body(
                datasource_type=datasource_type,
                display_name=display_name,
                connection=connection,
                capabilities=capabilities,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/datasources/{datasource_id}")
    def get_datasource(datasource_id: str) -> dict[str, object]:
        """Read one datasource via GET /datasources/{datasource_id}."""
        return client.request_envelope("GET", f"/datasources/{datasource_id}").model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/datasources/{datasource_id}")
    def update_datasource(
        datasource_id: str,
        display_name: str | None = None,
        connection: dict[str, object] | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one datasource via PUT /datasources/{datasource_id}."""
        return client.request_envelope(
            "PUT",
            f"/datasources/{datasource_id}",
            json_body=_compact_body(
                display_name=display_name,
                connection=connection,
                capabilities=capabilities,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/datasources/{datasource_id}")
    def delete_datasource(datasource_id: str) -> dict[str, object]:
        """Delete one datasource via DELETE /datasources/{datasource_id}."""
        return client.request_envelope("DELETE", f"/datasources/{datasource_id}").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/datasources/{datasource_id}/browse/catalogs")
    def browse_catalogs(
        datasource_id: str,
    ) -> dict[str, object]:
        """Browse available catalogs via GET /datasources/{datasource_id}/browse/catalogs."""
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/browse/catalogs",
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/datasources/{datasource_id}/browse/schemas")
    def browse_schemas(
        datasource_id: str,
        catalog: str | None = None,
    ) -> dict[str, object]:
        """Browse schemas via GET /datasources/{datasource_id}/browse/schemas."""
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/browse/schemas",
            params=_compact_params(catalog=catalog),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/datasources/{datasource_id}/browse/tables")
    def browse_tables(
        datasource_id: str,
        catalog: str | None = None,
        schema: str | None = None,
    ) -> dict[str, object]:
        """Browse tables via GET /datasources/{datasource_id}/browse/tables."""
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/browse/tables",
            params=_compact_params(catalog=catalog, schema=schema),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/datasources/{datasource_id}/browse/columns")
    def browse_columns(
        datasource_id: str,
        schema_name: str,
        table_name: str,
    ) -> dict[str, object]:
        """Browse live table columns via GET /datasources/{datasource_id}/browse/columns."""
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/browse/columns",
            params=_compact_params(schema_name=schema_name, table_name=table_name),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/datasources/{datasource_id}/preview")
    def preview_table(
        datasource_id: str,
        schema: str,
        table: str,
        limit: int = 100,
        columns: str | None = None,
        filters: dict[str, object] | list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Preview sample rows from a table via POST /datasources/{datasource_id}/preview.

        Use this to inspect actual data values when configuring dataset-native
        semantic models and field expressions.

        Args:
            datasource_id: Registered datasource identifier
            schema: Schema name containing the table
            table: Table name to preview
            limit: Max rows (default 100, max 1000)
            columns: Comma-separated column names (optional)
            filters: Equality filters as a JSON-like object, e.g. {"query_state":"FAILED"}
        """
        return client.request_envelope(
            "POST",
            f"/datasources/{datasource_id}/preview",
            json_body=_compact_body(
                schema=schema,
                table=table,
                limit=limit,
                columns=columns,
                filters=filters,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/routing/resolve")
    def resolve_routing(
        table_names: list[str],
        routing_intent: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Resolve table routing via POST /routing/resolve using the canonical nested routing_intent object when hints are needed."""
        return client.request_envelope(
            "POST",
            "/routing/resolve",
            json_body=_compact_body(table_names=table_names, routing_intent=routing_intent),
        ).model_dump()


def _compact_params(
    **params: _ParamValue,
) -> dict[str, _ParamValue]:
    return {
        key: value
        for key, value in params.items()
        if value is not None and not (isinstance(value, list) and len(value) == 0)
    }


def _compact_body(**params: object) -> dict[str, object]:
    return {key: _normalize_json_value(value) for key, value in params.items() if value is not None}


def _model_json_body(
    value: BaseModel,
    *,
    exclude: set[str] | None = None,
) -> dict[str, object]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True, exclude=exclude or set())


def _normalize_json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normalize_json_value(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise TypeError(f"Unsupported JSON value for Marivo MCP request body: {type(value).__name__}")


def _intent_request(
    client: MarivoHttpClient,
    session_id: str,
    intent_name: str,
    **params: object,
) -> dict[str, object]:
    return client.request_envelope(
        "POST",
        f"/sessions/{session_id}/intents/{intent_name}",
        json_body=_compact_body(**params),
    ).model_dump()


def _normalize_multi_param(values: list[str] | None) -> _ParamList | None:
    if values is None:
        return None
    return list(values)


def _normalize_string_multi_param(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return list(values)


def _semantic_read_request(
    client: MarivoHttpClient,
    path: str,
    *,
    status: str | None = None,
    lifecycle_status: str | None = None,
    readiness_status: str | None = None,
    detail: bool | None = None,
    extra_params: dict[str, str | bool | None] | None = None,
) -> dict[str, object]:
    params = {
        "status": status,
        "lifecycle_status": lifecycle_status,
        "readiness_status": readiness_status,
        "detail": detail,
    }
    if extra_params:
        params.update(extra_params)
    return client.request_envelope(
        "GET",
        path,
        params=_compact_params(**params),
    ).model_dump()


def _semantic_write_request(
    client: MarivoHttpClient,
    method: str,
    path: str,
    **params: object,
) -> dict[str, object]:
    return client.request_envelope(
        method,
        path,
        json_body=_compact_body(**params),
    ).model_dump()


def _semantic_action_request(
    client: MarivoHttpClient,
    path: str,
) -> dict[str, object]:
    return client.request_envelope("POST", path).model_dump()


def _semantic_publish_request(
    client: MarivoHttpClient,
    path: str,
) -> dict[str, object]:
    return _semantic_action_request(client, path)


def _resolve_object_id(
    object_id: str | None,
    legacy_id: str | None,
    *,
    legacy_name: str,
) -> str:
    if object_id and legacy_id and object_id != legacy_id:
        raise ValueError(
            f"object_id and {legacy_name} both provided but differ: {object_id!r} != {legacy_id!r}"
        )
    resolved = object_id or legacy_id
    if not resolved:
        raise ValueError(f"Missing required object identifier. Provide object_id or {legacy_name}.")
    return resolved


def _openapi_cached_request(
    client: MarivoHttpClient,
    cache: OpenApiResponseCache,
    key: tuple[object, ...],
    path: str,
    *,
    params: dict[str, _ParamValue] | None = None,
) -> dict[str, object]:
    cached = cache.get(key)
    if cached is not None:
        return cached
    envelope = client.request_envelope("GET", path, params=params).model_dump()
    if envelope.get("ok") is True:
        cache.set(key, envelope)
    return deepcopy(envelope)
