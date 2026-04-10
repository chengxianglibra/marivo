from __future__ import annotations

from factum_mcp.config import FactumMcpConfig
from factum_mcp.http_client import FactumHttpClient
from factum_mcp.sdk import FastMcpServer

_ParamScalar = str | int | float | bool | None
_ParamList = list[_ParamScalar]
_ParamValue = _ParamScalar | _ParamList


def register_tools(server: FastMcpServer, config: FactumMcpConfig) -> None:
    """Register the HTTP-backed MCP tools over the canonical Factum API."""
    client = FactumHttpClient(config)

    @server.tool()
    def create_session(
        goal: str,
        constraints: dict[str, object] | None = None,
        raw_filter: str | None = None,
        budget: dict[str, object] | None = None,
        policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create an investigation session via POST /sessions using the canonical session root request fields."""
        return client.request_envelope(
            "POST",
            "/sessions",
            json_body=_compact_body(
                goal=goal,
                constraints=constraints,
                raw_filter=raw_filter,
                budget=budget,
                policy=policy,
            ),
        ).model_dump()

    @server.tool()
    def get_session(session_id: str) -> dict[str, object]:
        """Read one canonical session root via GET /sessions/{session_id} without inlining state or proposition context."""
        return client.request_envelope("GET", f"/sessions/{session_id}").model_dump()

    @server.tool()
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
    def get_proposition_context(session_id: str, proposition_id: str) -> dict[str, object]:
        """Read the proposition-level canonical minimal closure via GET /sessions/{session_id}/propositions/{proposition_id}/context."""
        return client.request_envelope(
            "GET",
            f"/sessions/{session_id}/propositions/{proposition_id}/context",
        ).model_dump()

    @server.tool()
    def observe(
        session_id: str,
        metric: str,
        time_scope: dict[str, object],
        result_mode: str = "standard",
        scope: dict[str, object] | None = None,
        granularity: str | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/observe using the canonical ObserveRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        return _intent_request(
            client,
            session_id,
            "observe",
            metric=metric,
            time_scope=time_scope,
            result_mode=result_mode,
            scope=scope,
            granularity=granularity,
            dimensions=dimensions,
        )

    @server.tool()
    def compare(
        session_id: str,
        left_ref: dict[str, object],
        right_ref: dict[str, object],
        mode: str = "auto",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/compare using the canonical CompareRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        return _intent_request(
            client,
            session_id,
            "compare",
            left_ref=left_ref,
            right_ref=right_ref,
            mode=mode,
        )

    @server.tool()
    def decompose(
        session_id: str,
        compare_ref: dict[str, object],
        dimension: str,
        method: str = "delta_share",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/decompose using the canonical DecomposeRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        return _intent_request(
            client,
            session_id,
            "decompose",
            compare_ref=compare_ref,
            dimension=dimension,
            method=method,
        )

    @server.tool()
    def correlate(
        session_id: str,
        left_ref: dict[str, object],
        right_ref: dict[str, object],
        method: str = "spearman",
        min_pairs: int = 5,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/correlate using the canonical CorrelateRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
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
    def detect(
        session_id: str,
        metric: str,
        time_scope: dict[str, object],
        scope: dict[str, object] | None = None,
        split_by: str | None = None,
        profile: str = "auto",
        sensitivity: str = "balanced",
        limit: int | None = None,
        max_series: int | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/detect using the canonical DetectRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        return _intent_request(
            client,
            session_id,
            "detect",
            metric=metric,
            time_scope=time_scope,
            scope=scope,
            split_by=split_by,
            profile=profile,
            sensitivity=sensitivity,
            limit=limit,
            max_series=max_series,
        )

    @server.tool()
    def test_intent(
        session_id: str,
        left_ref: dict[str, object],
        right_ref: dict[str, object],
        hypothesis: dict[str, object],
        method: str = "auto",
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/test using the canonical IntentTestRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
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
    def forecast(
        session_id: str,
        source_ref: dict[str, object],
        horizon: int,
        profile: str = "auto",
        interval_level: float | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/forecast using the canonical ForecastRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
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
    def attribute(
        session_id: str,
        metric: str,
        left: dict[str, object],
        right: dict[str, object],
        dimensions: list[str],
        decomposition_method: str = "delta_share",
        decomposition_limit: int = 5,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/attribute using the canonical AttributeRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
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
    def diagnose(
        session_id: str,
        metric: str,
        time_scope: dict[str, object],
        candidate_dimensions: list[str],
        scope: dict[str, object] | None = None,
        detect_split_by: str | None = None,
        profile: str = "auto",
        sensitivity: str = "balanced",
        candidate_limit: int | None = None,
        followup_limit: int = 3,
        decomposition_limit: int | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/diagnose using the canonical DiagnoseRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
        return _intent_request(
            client,
            session_id,
            "diagnose",
            metric=metric,
            time_scope=time_scope,
            candidate_dimensions=candidate_dimensions,
            scope=scope,
            detect_split_by=detect_split_by,
            profile=profile,
            sensitivity=sensitivity,
            candidate_limit=candidate_limit,
            followup_limit=followup_limit,
            decomposition_limit=decomposition_limit,
        )

    @server.tool()
    def validate(
        session_id: str,
        metric: str,
        left: dict[str, object],
        right: dict[str, object],
        sample_kind: str | None = None,
        hypothesis: dict[str, object] | None = None,
        method: str | None = None,
    ) -> dict[str, object]:
        """Submit POST /sessions/{session_id}/intents/validate using the canonical ValidateRequest body; this path selects the intent, so do not add an extra intent field. On 422, follow error.guidance.contract_url, schema_url, and examples."""
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
    def health_check() -> dict[str, object]:
        """Check Factum service health via GET /health using the shared MCP HTTP envelope."""
        return client.request_envelope("GET", "/health").model_dump()

    @server.tool()
    def list_openapi_paths() -> dict[str, object]:
        """List canonical OpenAPI paths and schema names via GET /openapi/index for low-cost contract discovery."""
        return client.request_envelope("GET", "/openapi/index").model_dump()

    @server.tool()
    def get_openapi_schema(schema_name: str, depth: int = 1) -> dict[str, object]:
        """Read one canonical component schema via GET /openapi/schemas/{schema_name}."""
        return client.request_envelope(
            "GET",
            f"/openapi/schemas/{schema_name}",
            params={"depth": depth},
        ).model_dump()

    @server.tool()
    def get_openapi_fragment(
        path: str,
        operation: str | None = None,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Read a canonical OpenAPI fragment via GET /openapi/fragment without consulting a local schema copy."""
        return client.request_envelope(
            "GET",
            "/openapi/fragment",
            params=_compact_params(
                path=path,
                operation=operation,
                expand=_normalize_multi_param(expand),
                depth=depth,
            ),
        ).model_dump()

    @server.tool()
    def get_openapi_path_fragment(
        encoded_path: str,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Read one canonical OpenAPI path item via GET /openapi/paths/{encoded_path}; use this to follow guidance.contract_url."""
        return client.request_envelope(
            "GET",
            f"/openapi/paths/{encoded_path}",
            params=_compact_params(expand=_normalize_multi_param(expand), depth=depth),
        ).model_dump()

    @server.tool()
    def search_catalog(q: str, type: str | None = None) -> dict[str, object]:
        """Search published semantic objects and synced assets via GET /catalog/search using the HTTP query contract directly."""
        return client.request_envelope(
            "GET",
            "/catalog/search",
            params=_compact_params(q=q, type=type),
        ).model_dump()

    @server.tool()
    def resolve_typed_ref(ref: str) -> dict[str, object]:
        """Resolve one typed semantic ref via GET /semantic/resolve/{ref}; this does not create new object families."""
        return client.request_envelope("GET", f"/semantic/resolve/{ref}").model_dump()


def _compact_params(
    **params: _ParamValue,
) -> dict[str, _ParamValue]:
    return {
        key: value
        for key, value in params.items()
        if value is not None and not (isinstance(value, list) and len(value) == 0)
    }


def _compact_body(**params: object) -> dict[str, object]:
    return {key: value for key, value in params.items() if value is not None}


def _intent_request(
    client: FactumHttpClient,
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
