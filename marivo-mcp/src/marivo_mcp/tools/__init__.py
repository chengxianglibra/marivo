from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator
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


class ObserveScope(BaseModel):
    """Non-time population scope accepted by typed intent MCP tools."""

    constraints: dict[str, Any] | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate_ref: str | None = Field(
        default=None,
        description=(
            "Reference to a governed predicate (predicate.*) declaring request_scope usage."
        ),
    )

    @field_validator("predicate_ref")
    @classmethod
    def _validate_predicate_ref_prefix(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("predicate."):
            raise ValueError("predicate_ref must start with 'predicate.'")
        return value


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

    # ------------------------------------------------------------------
    # Sessions & Intents
    # ------------------------------------------------------------------

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
    @_tool_metadata("GET", "/sessions")
    def list_sessions(
        status: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        """List investigation sessions via GET /sessions."""
        return client.request_envelope(
            "GET",
            "/sessions",
            params=_compact_params(
                status=status,
                session_id=session_id,
                limit=limit,
                page_token=page_token,
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

    # ------------------------------------------------------------------
    # Health & OpenAPI Discovery
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("GET", "/health")
    def health_check() -> dict[str, object]:
        """Check Marivo service health via GET /health using the shared MCP HTTP envelope."""
        return client.request_envelope("GET", "/health").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/catalog")
    def get_catalog() -> dict[str, object]:
        """Read the API catalog via GET /catalog."""
        return client.request_envelope("GET", "/catalog").model_dump()

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

    # ------------------------------------------------------------------
    # Semantic Models V2 (OSI-aligned)
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("POST", "/semantic-models")
    def create_semantic_model(
        payload: McpStructuredObject,
        session_id: str | None = None,
    ) -> dict[str, object]:
        """Create a semantic model via POST /semantic-models from an OSI document fragment."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            "/semantic-models",
            params=_compact_params(session_id=session_id),
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models")
    def list_semantic_models(
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """List semantic models via GET /semantic-models."""
        return client.request_envelope(
            "GET",
            "/semantic-models",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/semantic-models/import")
    def import_osi_document(
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Import an OSI document as the latest public layer via POST /semantic-models/import."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            "/semantic-models/import",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}")
    def get_semantic_model(
        model: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """Get a semantic model as an OSI document via GET /semantic-models/{model}."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/semantic-models/{model}")
    def update_semantic_model(
        model: str,
        description: str | None = None,
    ) -> dict[str, object]:
        """Update top-level fields of a semantic model via PUT /semantic-models/{model}."""
        return client.request_envelope(
            "PUT",
            f"/semantic-models/{model}",
            json_body=_compact_body(description=description),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/semantic-models/{model}")
    def delete_semantic_model(model: str) -> dict[str, object]:
        """Delete a semantic model via DELETE /semantic-models/{model}."""
        return client.request_envelope("DELETE", f"/semantic-models/{model}").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/readiness")
    def get_semantic_model_readiness(model: str) -> dict[str, object]:
        """Get readiness status for a semantic model via GET /semantic-models/{model}/readiness."""
        return client.request_envelope("GET", f"/semantic-models/{model}/readiness").model_dump()

    # -- Datasets within a model --

    @server.tool()
    @_tool_metadata("POST", "/semantic-models/{model}/datasets")
    def create_dataset(
        model: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Create a dataset within a model via POST /semantic-models/{model}/datasets."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            f"/semantic-models/{model}/datasets",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/datasets")
    def list_datasets(
        model: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """List datasets in a model via GET /semantic-models/{model}/datasets."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/datasets",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/datasets/{name}")
    def get_dataset(
        model: str,
        name: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """Get a dataset by name within a model via GET /semantic-models/{model}/datasets/{name}."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/datasets/{name}",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/semantic-models/{model}/datasets/{name}")
    def update_dataset(
        model: str,
        name: str,
        description: str | None = None,
    ) -> dict[str, object]:
        """Update a dataset's top-level fields via PUT /semantic-models/{model}/datasets/{name}."""
        return client.request_envelope(
            "PUT",
            f"/semantic-models/{model}/datasets/{name}",
            json_body=_compact_body(description=description),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/semantic-models/{model}/datasets/{name}")
    def delete_dataset(model: str, name: str) -> dict[str, object]:
        """Delete a dataset via DELETE /semantic-models/{model}/datasets/{name}."""
        return client.request_envelope(
            "DELETE", f"/semantic-models/{model}/datasets/{name}"
        ).model_dump()

    # -- Relationships within a model --

    @server.tool()
    @_tool_metadata("POST", "/semantic-models/{model}/relationships")
    def create_relationship(
        model: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Create a relationship within a model via POST /semantic-models/{model}/relationships."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            f"/semantic-models/{model}/relationships",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/relationships")
    def list_relationships(
        model: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """List relationships in a model via GET /semantic-models/{model}/relationships."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/relationships",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/relationships/{name}")
    def get_relationship(
        model: str,
        name: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """Get a relationship by name within a model via GET /semantic-models/{model}/relationships/{name}."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/relationships/{name}",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/semantic-models/{model}/relationships/{name}")
    def update_relationship(
        model: str,
        name: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Update a relationship's fields via PUT /semantic-models/{model}/relationships/{name}."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "PUT",
            f"/semantic-models/{model}/relationships/{name}",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/semantic-models/{model}/relationships/{name}")
    def delete_relationship(model: str, name: str) -> dict[str, object]:
        """Delete a relationship via DELETE /semantic-models/{model}/relationships/{name}."""
        return client.request_envelope(
            "DELETE", f"/semantic-models/{model}/relationships/{name}"
        ).model_dump()

    # -- Metrics within a model --

    @server.tool()
    @_tool_metadata("POST", "/semantic-models/{model}/metrics")
    def create_metric(
        model: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Create a metric within a model via POST /semantic-models/{model}/metrics."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            f"/semantic-models/{model}/metrics",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/metrics")
    def list_metrics(
        model: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """List metrics in a model via GET /semantic-models/{model}/metrics."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/metrics",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic-models/{model}/metrics/{name}")
    def get_metric(
        model: str,
        name: str,
        requesting_user: str | None = None,
    ) -> dict[str, object]:
        """Get a metric by name within a model via GET /semantic-models/{model}/metrics/{name}."""
        return client.request_envelope(
            "GET",
            f"/semantic-models/{model}/metrics/{name}",
            params=_compact_params(requesting_user=requesting_user),
        ).model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/semantic-models/{model}/metrics/{name}")
    def update_metric(
        model: str,
        name: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Update a metric's fields via PUT /semantic-models/{model}/metrics/{name}."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "PUT",
            f"/semantic-models/{model}/metrics/{name}",
            json_body=_compact_body(**payload),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/semantic-models/{model}/metrics/{name}")
    def delete_metric(model: str, name: str) -> dict[str, object]:
        """Delete a metric via DELETE /semantic-models/{model}/metrics/{name}."""
        return client.request_envelope(
            "DELETE", f"/semantic-models/{model}/metrics/{name}"
        ).model_dump()

    # ------------------------------------------------------------------
    # Governance
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("POST", "/policies")
    def create_policy(
        name: str,
        policy_type: str,
        definition: McpStructuredObject,
        scope: McpStructuredObject | None = None,
    ) -> dict[str, object]:
        """Create a governance policy via POST /policies."""
        _require_structured_object(definition, field_name="definition")
        return client.request_envelope(
            "POST",
            "/policies",
            json_body=_compact_body(
                name=name,
                policy_type=policy_type,
                definition=definition,
                scope=scope,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/policies")
    def list_policies() -> dict[str, object]:
        """List governance policies via GET /policies."""
        return client.request_envelope("GET", "/policies").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict[str, object]:
        """Read one governance policy via GET /policies/{policy_id}."""
        return client.request_envelope("GET", f"/policies/{policy_id}").model_dump()

    @server.tool()
    @_tool_metadata("PUT", "/policies/{policy_id}")
    def update_policy(
        policy_id: str,
        enabled: bool | None = None,
        definition: McpStructuredObject | None = None,
    ) -> dict[str, object]:
        """Update a governance policy via PUT /policies/{policy_id}."""
        if definition is not None:
            _require_structured_object(definition, field_name="definition")
        return client.request_envelope(
            "PUT",
            f"/policies/{policy_id}",
            json_body=_compact_body(enabled=enabled, definition=definition),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/policies/{policy_id}")
    def delete_policy(policy_id: str) -> dict[str, object]:
        """Delete a governance policy via DELETE /policies/{policy_id}."""
        return client.request_envelope("DELETE", f"/policies/{policy_id}").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/quality-rules")
    def create_quality_rule(
        name: str,
        rule_type: str,
        table_name: str,
        threshold: McpStructuredObject,
        severity: str = "warn",
    ) -> dict[str, object]:
        """Create a quality rule via POST /quality-rules."""
        _require_structured_object(threshold, field_name="threshold")
        return client.request_envelope(
            "POST",
            "/quality-rules",
            json_body=_compact_body(
                name=name,
                rule_type=rule_type,
                table_name=table_name,
                threshold=threshold,
                severity=severity,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/quality-rules")
    def list_quality_rules(table: str | None = None) -> dict[str, object]:
        """List quality rules via GET /quality-rules."""
        return client.request_envelope(
            "GET",
            "/quality-rules",
            params=_compact_params(table=table),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/quality-rules/{rule_id}")
    def delete_quality_rule(rule_id: str) -> dict[str, object]:
        """Delete a quality rule via DELETE /quality-rules/{rule_id}."""
        return client.request_envelope("DELETE", f"/quality-rules/{rule_id}").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/governance/check")
    def governance_check(
        session_id: str,
        step_type: str,
        params: McpStructuredObject | None = None,
    ) -> dict[str, object]:
        """Run a governance check via POST /governance/check."""
        if params is not None:
            _require_structured_object(params, field_name="params")
        return client.request_envelope(
            "POST",
            "/governance/check",
            json_body=_compact_body(
                session_id=session_id,
                step_type=step_type,
                params=params,
            ),
        ).model_dump()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("POST", "/jobs")
    def submit_job(
        session_id: str,
        job_type: str,
        payload: McpStructuredObject,
    ) -> dict[str, object]:
        """Submit a job via POST /jobs."""
        _require_structured_object(payload, field_name="payload")
        return client.request_envelope(
            "POST",
            "/jobs",
            json_body=_compact_body(
                session_id=session_id,
                job_type=job_type,
                payload=payload,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/jobs")
    def list_jobs(
        session_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        """List jobs via GET /jobs."""
        return client.request_envelope(
            "GET",
            "/jobs",
            params=_compact_params(session_id=session_id, status=status),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        """Read one job via GET /jobs/{job_id}."""
        return client.request_envelope("GET", f"/jobs/{job_id}").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        """Cancel a job via POST /jobs/{job_id}/cancel."""
        return client.request_envelope("POST", f"/jobs/{job_id}/cancel").model_dump()

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("POST", "/approvals")
    def create_approval(
        session_id: str,
        rec_id: str,
    ) -> dict[str, object]:
        """Create an approval request via POST /approvals."""
        return client.request_envelope(
            "POST",
            "/approvals",
            json_body=_compact_body(session_id=session_id, rec_id=rec_id),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/approvals")
    def list_approvals(
        session_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        """List approval requests via GET /approvals."""
        return client.request_envelope(
            "GET",
            "/approvals",
            params=_compact_params(session_id=session_id, status=status),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/approvals/{request_id}")
    def get_approval(request_id: str) -> dict[str, object]:
        """Read one approval request via GET /approvals/{request_id}."""
        return client.request_envelope("GET", f"/approvals/{request_id}").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/approvals/{request_id}/approve")
    def approve_request(
        request_id: str,
        reviewer: str,
        reason: str = "",
    ) -> dict[str, object]:
        """Approve an approval request via POST /approvals/{request_id}/approve."""
        return client.request_envelope(
            "POST",
            f"/approvals/{request_id}/approve",
            json_body=_compact_body(reviewer=reviewer, reason=reason),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/approvals/{request_id}/reject")
    def reject_request(
        request_id: str,
        reviewer: str,
        reason: str = "",
    ) -> dict[str, object]:
        """Reject an approval request via POST /approvals/{request_id}/reject."""
        return client.request_envelope(
            "POST",
            f"/approvals/{request_id}/reject",
            json_body=_compact_body(reviewer=reviewer, reason=reason),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sessions/{session_id}/approvals/auto-flag")
    def auto_flag_approvals(
        session_id: str,
        risk_threshold: str = "P0",
    ) -> dict[str, object]:
        """Auto-flag approvals for a session via POST /sessions/{session_id}/approvals/auto-flag."""
        return client.request_envelope(
            "POST",
            f"/sessions/{session_id}/approvals/auto-flag",
            json_body=_compact_body(risk_threshold=risk_threshold),
        ).model_dump()

    # ------------------------------------------------------------------
    # Datasources
    # ------------------------------------------------------------------

    @server.tool()
    @_tool_metadata("GET", "/datasources")
    def list_datasources() -> dict[str, object]:
        """List registered datasources via GET /datasources."""
        return client.request_envelope("GET", "/datasources").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/datasources")
    def create_datasource(
        datasource_type: str,
        display_name: str,
        connection: dict[str, object] | None = None,
        policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create one datasource via POST /datasources using the canonical datasource_type, display_name, connection, and policy fields."""
        return client.request_envelope(
            "POST",
            "/datasources",
            json_body=_compact_body(
                datasource_type=datasource_type,
                display_name=display_name,
                connection=connection,
                policy=policy,
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
        policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one datasource via PUT /datasources/{datasource_id}."""
        return client.request_envelope(
            "PUT",
            f"/datasources/{datasource_id}",
            json_body=_compact_body(
                display_name=display_name,
                connection=connection,
                policy=policy,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("DELETE", "/datasources/{datasource_id}")
    def delete_datasource(datasource_id: str) -> dict[str, object]:
        """Delete one datasource via DELETE /datasources/{datasource_id}."""
        return client.request_envelope("DELETE", f"/datasources/{datasource_id}").model_dump()

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
        schema_name: str | None = None,
    ) -> dict[str, object]:
        """Browse tables via GET /datasources/{datasource_id}/browse/tables."""
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/browse/tables",
            params=_compact_params(catalog=catalog, schema_name=schema_name),
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
    @_tool_metadata("GET", "/datasources/{datasource_id}/catalog/preview")
    def preview_table(
        datasource_id: str,
        schema: str,
        table: str,
        limit: int = 100,
        columns: str | None = None,
        filters: str | None = None,
    ) -> dict[str, object]:
        """Preview sample rows from a table via GET /datasources/{datasource_id}/catalog/preview.

        Use this to inspect actual data values when configuring dataset-native
        semantic models and field expressions.

        Args:
            datasource_id: Registered datasource identifier
            schema: Schema name containing the table
            table: Table name to preview
            limit: Max rows (default 100, max 1000)
            columns: Comma-separated column names (optional)
            filters: JSON object or array of {column,value} equality filters (as string)
        """
        return client.request_envelope(
            "GET",
            f"/datasources/{datasource_id}/catalog/preview",
            params=_compact_params(
                schema=schema,
                table=table,
                limit=limit,
                columns=columns,
                filters=filters,
            ),
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
