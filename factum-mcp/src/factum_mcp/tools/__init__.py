from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from factum_mcp.config import FactumMcpConfig
from factum_mcp.http_client import FactumHttpClient
from factum_mcp.openapi_cache import OpenApiResponseCache
from factum_mcp.sdk import FastMcpServer

_ParamScalar = str | int | float | bool | None
_ParamList = list[_ParamScalar]
_ParamValue = _ParamScalar | _ParamList


def _tool_metadata(
    method: str, path: str
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        func._factum_http_method = method  # type: ignore[attr-defined]
        func._factum_http_path = path  # type: ignore[attr-defined]
        return func

    return decorator


def register_tools(
    server: FastMcpServer,
    config: FactumMcpConfig,
    *,
    client_factory: Callable[[FactumMcpConfig], FactumHttpClient] | None = None,
    openapi_cache: OpenApiResponseCache | None = None,
) -> None:
    """Register the HTTP-backed MCP tools over the canonical Factum API."""
    resolved_client_factory = client_factory or FactumHttpClient
    client = resolved_client_factory(config)
    discovery_cache = openapi_cache or OpenApiResponseCache(config.openapi_cache_ttl_sec)

    @server.tool()
    @_tool_metadata("POST", "/sessions")
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
    @_tool_metadata("GET", "/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, object]:
        """Read one canonical session root via GET /sessions/{session_id} without inlining state or proposition context."""
        return client.request_envelope("GET", f"/sessions/{session_id}").model_dump()

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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/compare")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/decompose")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/correlate")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/detect")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/test")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/forecast")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/attribute")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/diagnose")
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
    @_tool_metadata("POST", "/sessions/{session_id}/intents/validate")
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
    @_tool_metadata("GET", "/health")
    def health_check() -> dict[str, object]:
        """Check Factum service health via GET /health using the shared MCP HTTP envelope."""
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
        encoded_path: str,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Read one canonical OpenAPI path item via GET /openapi/paths/{encoded_path}; use this to follow guidance.contract_url."""
        normalized_expand = _normalize_string_multi_param(expand)
        request_expand = _normalize_multi_param(expand)
        return _openapi_cached_request(
            client,
            discovery_cache,
            (
                "openapi_path_fragment",
                encoded_path,
                tuple(sorted(normalized_expand or [])),
                depth,
            ),
            f"/openapi/paths/{encoded_path}",
            params=_compact_params(expand=request_expand, depth=depth),
        )

    @server.tool()
    @_tool_metadata("GET", "/catalog/search")
    def search_catalog(q: str, type: str | None = None) -> dict[str, object]:
        """Search published semantic objects and synced assets via GET /catalog/search using the HTTP query contract directly."""
        return client.request_envelope(
            "GET",
            "/catalog/search",
            params=_compact_params(q=q, type=type),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/semantic/resolve/{ref}")
    def resolve_typed_ref(ref: str) -> dict[str, object]:
        """Resolve one typed semantic ref via GET /semantic/resolve/{ref}; this does not create new object families."""
        return client.request_envelope("GET", f"/semantic/resolve/{ref}").model_dump()

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
        """Read one entity via GET /semantic/entities/{entity_id}; prefer object_id over the legacy entity_id name."""
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
    @_tool_metadata("POST", "/semantic/metrics")
    def create_metric(
        header: dict[str, object],
        payload: dict[str, object],
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
        """Read one metric via GET /semantic/metrics/{metric_id}; prefer object_id over the legacy metric_id name."""
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
        header: dict[str, object],
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
    @_tool_metadata("POST", "/semantic/bindings")
    def create_binding(
        header: dict[str, object],
        interface_contract: dict[str, object],
    ) -> dict[str, object]:
        """Create one draft binding via POST /semantic/bindings using the canonical TypedBindingCreateRequest fields."""
        return _semantic_write_request(
            client,
            "POST",
            "/semantic/bindings",
            header=header,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/bindings")
    def list_bindings(
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool | None = None,
    ) -> dict[str, object]:
        """List bindings via GET /semantic/bindings; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/semantic/bindings",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/semantic/bindings/{binding_id}")
    def get_binding(
        object_id: str | None = None, binding_id: str | None = None
    ) -> dict[str, object]:
        """Read one binding via GET /semantic/bindings/{binding_id}; prefer object_id over the legacy binding_id name."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_read_request(client, f"/semantic/bindings/{resolved_id}")

    @server.tool()
    @_tool_metadata("PUT", "/semantic/bindings/{binding_id}")
    def update_binding(
        object_id: str | None = None,
        binding_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        interface_contract: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Update one draft binding via PUT /semantic/bindings/{binding_id} using the canonical TypedBindingUpdateRequest fields."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_write_request(
            client,
            "PUT",
            f"/semantic/bindings/{resolved_id}",
            display_name=display_name,
            description=description,
            interface_contract=interface_contract,
        )

    @server.tool()
    @_tool_metadata("POST", "/semantic/bindings/{binding_id}/validate")
    def validate_binding(
        object_id: str | None = None, binding_id: str | None = None
    ) -> dict[str, object]:
        """Validate one binding via POST /semantic/bindings/{binding_id}/validate without changing stored lifecycle state."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_action_request(client, f"/semantic/bindings/{resolved_id}/validate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/bindings/{binding_id}/activate")
    def activate_binding(
        object_id: str | None = None, binding_id: str | None = None
    ) -> dict[str, object]:
        """Activate one binding via POST /semantic/bindings/{binding_id}/activate; activation adds it to the formal catalog but does not imply ready."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_action_request(client, f"/semantic/bindings/{resolved_id}/activate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/bindings/{binding_id}/deprecate")
    def deprecate_binding(
        object_id: str | None = None, binding_id: str | None = None
    ) -> dict[str, object]:
        """Deprecate one binding via POST /semantic/bindings/{binding_id}/deprecate."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_action_request(client, f"/semantic/bindings/{resolved_id}/deprecate")

    @server.tool()
    @_tool_metadata("POST", "/semantic/bindings/{binding_id}/publish")
    def publish_binding(
        object_id: str | None = None, binding_id: str | None = None
    ) -> dict[str, object]:
        """Compatibility alias for activate_binding via POST /semantic/bindings/{binding_id}/publish."""
        resolved_id = _resolve_object_id(object_id, binding_id, legacy_name="binding_id")
        return _semantic_publish_request(client, f"/semantic/bindings/{resolved_id}/publish")

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
    ) -> dict[str, object]:
        """List compatibility profiles via GET /compiler/compatibility-profiles; prefer lifecycle_status/readiness_status over legacy status."""
        return _semantic_read_request(
            client,
            "/compiler/compatibility-profiles",
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )

    @server.tool()
    @_tool_metadata("GET", "/compiler/compatibility-profiles/{profile_id}")
    def get_compatibility_profile(
        object_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Read one compatibility profile via GET /compiler/compatibility-profiles/{profile_id}; prefer object_id over the legacy profile_id name."""
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
    @_tool_metadata("GET", "/sources")
    def list_sources() -> dict[str, object]:
        """List registered sources via GET /sources without adding MCP-only filtering semantics."""
        return client.request_envelope("GET", "/sources").model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sources")
    def register_source(
        source_type: str,
        display_name: str,
        connection: dict[str, object] | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Register one source via POST /sources using the canonical source_type, display_name, connection, and capabilities fields."""
        return client.request_envelope(
            "POST",
            "/sources",
            json_body=_compact_body(
                source_type=source_type,
                display_name=display_name,
                connection=connection,
                capabilities=capabilities,
            ),
        ).model_dump()

    @server.tool()
    @_tool_metadata("POST", "/sources/{source_id}/sync")
    def sync_source(source_id: str) -> dict[str, object]:
        """Trigger synced metadata refresh via POST /sources/{source_id}/sync; this operates on stored source metadata, not live catalog browse endpoints."""
        return client.request_envelope("POST", f"/sources/{source_id}/sync").model_dump()

    @server.tool()
    @_tool_metadata("GET", "/sources/{source_id}/objects")
    def get_source_objects(
        source_id: str,
        type: str | None = None,
        schema: str | None = None,
    ) -> dict[str, object]:
        """Read synced source metadata via GET /sources/{source_id}/objects using only the canonical type and schema filters; for live external catalog browse, use /sources/{source_id}/catalog/* instead."""
        return client.request_envelope(
            "GET",
            f"/sources/{source_id}/objects",
            params=_compact_params(type=type, schema=schema),
        ).model_dump()

    @server.tool()
    @_tool_metadata("GET", "/sources/{source_id}/objects/{object_id}")
    def get_source_object(source_id: str, object_id: str) -> dict[str, object]:
        """Read one synced source object via GET /sources/{source_id}/objects/{object_id}; this mirrors Factum's stored metadata detail and does not browse live external catalogs."""
        return client.request_envelope(
            "GET",
            f"/sources/{source_id}/objects/{object_id}",
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


def _normalize_string_multi_param(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return list(values)


def _semantic_read_request(
    client: FactumHttpClient,
    path: str,
    *,
    status: str | None = None,
    lifecycle_status: str | None = None,
    readiness_status: str | None = None,
    detail: bool | None = None,
) -> dict[str, object]:
    return client.request_envelope(
        "GET",
        path,
        params=_compact_params(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        ),
    ).model_dump()


def _semantic_write_request(
    client: FactumHttpClient,
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
    client: FactumHttpClient,
    path: str,
) -> dict[str, object]:
    return client.request_envelope("POST", path).model_dump()


def _semantic_publish_request(
    client: FactumHttpClient,
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
    client: FactumHttpClient,
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
