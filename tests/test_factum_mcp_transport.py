from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from app.api.models._legacy import (
    DetectTimeScope,
    DetectTimeScopeCurrentWindow,
    HypothesisContract,
    ObserveTimeScopeAsOf,
    ObserveTimeScopeRange,
    TestObservationRef,
    ValidateHypothesis,
    ValidateObservationInput,
)

FACTUM_MCP_SRC = Path(__file__).resolve().parents[1] / "factum-mcp" / "src"
sys.path.insert(0, str(FACTUM_MCP_SRC))

tools_module = import_module("factum_mcp.tools")
config_module = import_module("factum_mcp.config")
http_client_module = import_module("factum_mcp.http_client")
openapi_cache_module = import_module("factum_mcp.openapi_cache")

FactumMcpConfig = config_module.FactumMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
FactumHttpClient = http_client_module.FactumHttpClient
register_tools = tools_module.register_tools
OpenApiResponseCache = openapi_cache_module.OpenApiResponseCache


class _FakeServerSettings:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 8000
        self.streamable_http_path = "/mcp"


class _FakeServer:
    def __init__(self) -> None:
        self.settings = _FakeServerSettings()
        self.tools: dict[str, Any] = {}

    def tool(self) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, uri: str) -> Any:
        raise AssertionError(f"Unexpected resource registration for {uri}")

    def run(self, transport: str | None = None) -> None:
        raise AssertionError(f"Unexpected run({transport!r}) during unit tests")


def _build_config(*, api_token: str | None = None) -> Any:
    return FactumMcpConfig(
        base_url="http://factum.test",
        api_token=api_token,
        timeout_ms=1500,
        openapi_cache_ttl_sec=300,
        default_source_id=None,
        transport="stdio",
        http=HttpTransportConfig(),
    )


def test_success_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200,
            json={"status": "ok"},
            headers={"content-type": "application/json"},
            request=request,
        )

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/health")

    assert envelope.ok is True
    assert envelope.status_code == 200
    assert envelope.data == {"status": "ok"}
    assert envelope.error is None
    assert envelope.meta.factum_path == "/health"
    assert envelope.meta.method == "GET"
    assert envelope.meta.request_url == "http://factum.test/health"
    assert envelope.meta.attempt_count == 1
    assert envelope.meta.content_type == "application/json"
    client.close()


def test_non_json_success_response_is_wrapped_as_raw_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="ok",
            headers={"content-type": "text/plain"},
            request=request,
        )

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/health")

    assert envelope.ok is True
    assert envelope.data == {"raw_text": "ok"}
    assert envelope.meta.content_type == "text/plain"
    client.close()


def test_injects_bearer_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client = FactumHttpClient(
        _build_config(api_token="secret-token"),
        transport=httpx.MockTransport(handler),
    )
    envelope = client.request_envelope("GET", "/health")

    assert envelope.ok is True
    client.close()


def test_validation_error_preserves_guidance_and_adds_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "header"],
                        "msg": "field required",
                        "type": "value_error.missing",
                    }
                ],
                "error": {
                    "code": "request_validation_error",
                    "message": "Request validation failed. Use the guided example and contract links.",
                },
                "guidance": {
                    "schema_url": "/openapi/schemas/TypedEntityCreateRequest?depth=6",
                    "contract_url": "/openapi/paths/L3NlbWFudGljL2VudGl0aWVz",
                    "examples": [{"summary": "Minimal payload", "payload": {"header": {}}}],
                    "next_action": "Start with guidance.examples.",
                },
            },
            request=request,
        )

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("POST", "/semantic/entities", json_body={})

    assert envelope.ok is False
    assert envelope.status_code == 422
    assert envelope.error is not None
    assert envelope.error.category == "validation"
    assert envelope.error.code == "request_validation_error"
    assert envelope.error.detail == [
        {
            "loc": ["body", "header"],
            "msg": "field required",
            "type": "value_error.missing",
        }
    ]
    assert envelope.error.guidance == {
        "schema_url": "/openapi/schemas/TypedEntityCreateRequest?depth=6",
        "contract_url": "/openapi/paths/L3NlbWFudGljL2VudGl0aWVz",
        "examples": [{"summary": "Minimal payload", "payload": {"header": {}}}],
        "next_action": "Start with guidance.examples.",
    }
    assert envelope.error.remediation_hint is not None
    assert "guidance.examples" in envelope.error.remediation_hint
    client.close()


def test_not_found_and_conflict_are_distinguished() -> None:
    responses = {
        "/sessions/missing": httpx.Response(
            404,
            json={"detail": "Session sess_missing not found"},
        ),
        "/semantic/bindings": httpx.Response(
            409,
            json={"detail": "Binding already exists"},
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses[request.url.path]
        return httpx.Response(
            response.status_code,
            json=response.json(),
            headers=response.headers,
            request=request,
        )

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    not_found = client.request_envelope("GET", "/sessions/missing")
    conflict = client.request_envelope("POST", "/semantic/bindings", json_body={})

    assert not_found.error is not None
    assert not_found.error.category == "not_found"
    assert conflict.error is not None
    assert conflict.error.category == "conflict"
    client.close()


def test_server_error_with_text_body_preserves_raw_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="Query execution failed",
            headers={"content-type": "text/plain"},
            request=request,
        )

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/jobs")

    assert envelope.error is not None
    assert envelope.error.category == "server_error"
    assert envelope.error.raw_body == "Query execution failed"
    assert envelope.error.message == "Query execution failed"
    client.close()


def test_get_retries_once_on_timeout() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/health")

    assert attempts["count"] == 2
    assert envelope.ok is True
    assert envelope.meta.attempt_count == 2
    client.close()


def test_post_does_not_retry_on_timeout() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client = FactumHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("POST", "/semantic/entities", json_body={})

    assert attempts["count"] == 1
    assert envelope.ok is False
    assert envelope.status_code == 504
    assert envelope.error is not None
    assert envelope.error.category == "transport"
    client.close()


def test_health_check_uses_shared_http_client_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"}, request=request)

    tools_module_any = cast("Any", tools_module)
    original_client = tools_module_any.FactumHttpClient

    def build_client(config: Any) -> Any:
        return FactumHttpClient(config, transport=httpx.MockTransport(handler))

    tools_module_any.FactumHttpClient = build_client
    try:
        server = cast("Any", _FakeServer())
        register_tools(server, _build_config())
        result = server.tools["health_check"]()
    finally:
        tools_module_any.FactumHttpClient = original_client

    assert result == {
        "ok": True,
        "status_code": 200,
        "data": {"status": "ok"},
        "error": None,
        "meta": {
            "factum_path": "/health",
            "method": "GET",
            "request_url": "http://factum.test/health",
            "attempt_count": 1,
            "content_type": "application/json",
        },
    }


def test_registers_t4_discovery_and_catalog_tools() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    assert set(server.tools) >= {
        "create_session",
        "get_session",
        "get_session_state",
        "query_session_state",
        "get_proposition_context",
        "health_check",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
        "search_catalog",
        "resolve_typed_ref",
    }
    assert set(server.tools) >= {
        "observe",
        "compare",
        "decompose",
        "correlate",
        "detect",
        "test_intent",
        "forecast",
        "attribute",
        "diagnose",
        "validate",
    }
    assert set(server.tools) >= {
        "create_entity",
        "list_entities",
        "get_entity",
        "update_entity",
        "validate_entity",
        "activate_entity",
        "deprecate_entity",
        "publish_entity",
        "create_metric",
        "list_metrics",
        "get_metric",
        "update_metric",
        "validate_metric",
        "activate_metric",
        "deprecate_metric",
        "publish_metric",
        "create_process_object",
        "list_process_objects",
        "get_process_object",
        "update_process_object",
        "validate_process_object",
        "activate_process_object",
        "deprecate_process_object",
        "publish_process_object",
        "create_dimension",
        "list_dimensions",
        "get_dimension",
        "update_dimension",
        "validate_dimension",
        "activate_dimension",
        "deprecate_dimension",
        "publish_dimension",
        "create_time_semantic",
        "list_time_semantics",
        "get_time_semantic",
        "update_time_semantic",
        "validate_time_semantic",
        "activate_time_semantic",
        "deprecate_time_semantic",
        "publish_time_semantic",
        "create_enum_set",
        "list_enum_sets",
        "get_enum_set",
        "update_enum_set",
        "validate_enum_set",
        "activate_enum_set",
        "deprecate_enum_set",
        "publish_enum_set",
        "create_binding",
        "list_bindings",
        "get_binding",
        "update_binding",
        "validate_binding",
        "activate_binding",
        "deprecate_binding",
        "publish_binding",
        "create_compatibility_profile",
        "list_compatibility_profiles",
        "get_compatibility_profile",
        "update_compatibility_profile",
        "validate_compatibility_profile",
        "activate_compatibility_profile",
        "deprecate_compatibility_profile",
        "publish_compatibility_profile",
    }
    assert set(server.tools) >= {
        "list_sources",
        "register_source",
        "sync_source",
        "get_source_objects",
        "resolve_routing",
    }


def test_list_openapi_paths_uses_openapi_index_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/index"
        assert request.url.query == b""
        return httpx.Response(200, json={"paths": [], "schemas": []}, request=request)

    result = _invoke_registered_tool("list_openapi_paths", handler)

    assert result["ok"] is True
    assert result["data"] == {"paths": [], "schemas": []}
    assert result["meta"]["factum_path"] == "/openapi/index"


def test_get_openapi_schema_uses_schema_route_and_default_depth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/schemas/SessionCreateRequest"
        assert dict(request.url.params) == {"depth": "1"}
        return httpx.Response(
            200,
            json={"schema_name": "SessionCreateRequest", "depth": 1},
            request=request,
        )

    result = _invoke_registered_tool(
        "get_openapi_schema",
        handler,
        schema_name="SessionCreateRequest",
    )

    assert result["ok"] is True
    assert result["data"] == {"schema_name": "SessionCreateRequest", "depth": 1}


def test_get_openapi_fragment_forwards_operation_expand_and_depth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/fragment"
        params = request.url.params
        assert params.get("path") == "/sessions"
        assert params.get("operation") == "post"
        assert params.get_list("expand") == ["request", "response", "schemas"]
        assert params.get("depth") == "2"
        return httpx.Response(
            200, json={"fragment": {"operation": {"summary": "Create"}}}, request=request
        )

    result = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        path="/sessions",
        operation="post",
        expand=["request", "response", "schemas"],
        depth=2,
    )

    assert result["ok"] is True
    assert result["meta"]["factum_path"] == "/openapi/fragment"


def test_get_openapi_fragment_omits_optional_query_params_when_not_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/fragment"
        assert dict(request.url.params) == {"path": "/sessions", "depth": "1"}
        return httpx.Response(200, json={"fragment": {"path_item": {}}}, request=request)

    result = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        path="/sessions",
    )

    assert result["ok"] is True


def test_get_openapi_fragment_preserves_http_400_for_missing_operation_when_request_expand_used() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "detail": "'operation' is required when expand includes 'request' or 'response'."
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        path="/sessions",
        expand=["request"],
    )

    assert result["ok"] is False
    assert result["status_code"] == 400
    assert result["error"]["category"] == "server_error"


def test_get_openapi_path_fragment_uses_encoded_path_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/paths/L3Nlc3Npb25z"
        assert request.url.params.get_list("expand") == ["schemas"]
        assert request.url.params.get("depth") == "1"
        return httpx.Response(200, json={"path": "/sessions"}, request=request)

    result = _invoke_registered_tool(
        "get_openapi_path_fragment",
        handler,
        encoded_path="L3Nlc3Npb25z",
        expand=["schemas"],
    )

    assert result["ok"] is True
    assert result["data"] == {"path": "/sessions"}


def test_get_openapi_path_fragment_preserves_http_400_for_invalid_encoded_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "detail": "Invalid encoded path. Use unpadded base64url for the raw OpenAPI path."
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "get_openapi_path_fragment",
        handler,
        encoded_path="not-valid@@@",
    )

    assert result["ok"] is False
    assert result["status_code"] == 400
    assert result["error"]["message"] == (
        "Invalid encoded path. Use unpadded base64url for the raw OpenAPI path."
    )


def test_openapi_index_hits_cache_until_ttl_expires() -> None:
    attempts = {"count": 0}
    now = {"value": 100.0}
    cache = OpenApiResponseCache(30, time_fn=lambda: now["value"])

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            200,
            json={"revision": f"rev-{attempts['count']}", "paths": [], "schemas": []},
            request=request,
        )

    first = _invoke_registered_tool("list_openapi_paths", handler, _openapi_cache=cache)
    second = _invoke_registered_tool("list_openapi_paths", handler, _openapi_cache=cache)
    now["value"] = 131.0
    third = _invoke_registered_tool("list_openapi_paths", handler, _openapi_cache=cache)

    assert attempts["count"] == 2
    assert first["data"]["revision"] == "rev-1"
    assert second["data"]["revision"] == "rev-1"
    assert third["data"]["revision"] == "rev-2"


def test_openapi_cache_key_includes_expand_and_depth() -> None:
    attempts = {"count": 0}
    cache = OpenApiResponseCache(60, time_fn=lambda: 100.0)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        params = request.url.params
        return httpx.Response(
            200,
            json={
                "path": params.get("path"),
                "expand": params.get_list("expand"),
                "depth": params.get("depth"),
                "attempt": attempts["count"],
            },
            request=request,
        )

    first = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        _openapi_cache=cache,
        path="/sessions",
        expand=["schemas"],
        depth=1,
    )
    second = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        _openapi_cache=cache,
        path="/sessions",
        expand=["schemas"],
        depth=1,
    )
    third = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        _openapi_cache=cache,
        path="/sessions",
        expand=["request", "schemas"],
        depth=1,
    )
    fourth = _invoke_registered_tool(
        "get_openapi_fragment",
        handler,
        _openapi_cache=cache,
        path="/sessions",
        expand=["schemas"],
        depth=2,
    )

    assert attempts["count"] == 3
    assert first["data"]["attempt"] == 1
    assert second["data"]["attempt"] == 1
    assert third["data"]["attempt"] == 2
    assert fourth["data"]["attempt"] == 3


def test_openapi_cache_does_not_store_errors() -> None:
    attempts = {"count": 0}
    cache = OpenApiResponseCache(60, time_fn=lambda: 100.0)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404, json={"detail": "missing schema"}, request=request)

    first = _invoke_registered_tool(
        "get_openapi_schema",
        handler,
        _openapi_cache=cache,
        schema_name="MissingSchema",
    )
    second = _invoke_registered_tool(
        "get_openapi_schema",
        handler,
        _openapi_cache=cache,
        schema_name="MissingSchema",
    )

    assert attempts["count"] == 2
    assert first["ok"] is False
    assert second["ok"] is False


def test_openapi_cache_can_be_disabled_with_zero_ttl() -> None:
    attempts = {"count": 0}
    cache = OpenApiResponseCache(0, time_fn=lambda: 100.0)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, json={"attempt": attempts["count"]}, request=request)

    first = _invoke_registered_tool("list_openapi_paths", handler, _openapi_cache=cache)
    second = _invoke_registered_tool("list_openapi_paths", handler, _openapi_cache=cache)

    assert attempts["count"] == 2
    assert first["data"]["attempt"] == 1
    assert second["data"]["attempt"] == 2


def test_search_catalog_forwards_query_and_type_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/catalog/search"
        assert dict(request.url.params) == {"q": "watch", "type": "metric"}
        return httpx.Response(
            200, json=[{"object_kind": "metric", "ref": "metric.watch_time"}], request=request
        )

    result = _invoke_registered_tool(
        "search_catalog",
        handler,
        q="watch",
        type="metric",
    )

    assert result["ok"] is True
    assert result["data"] == [{"object_kind": "metric", "ref": "metric.watch_time"}]


def test_search_catalog_rejects_invalid_type_filter_before_http_request() -> None:
    with pytest.raises(ValueError, match="search_catalog type must be one of"):
        _invoke_registered_tool(
            "search_catalog",
            lambda request: httpx.Response(200, json=[], request=request),
            q="watch",
            type="profile",
        )


def test_resolve_typed_ref_uses_explicit_ref_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/resolve/metric.watch_time"
        return httpx.Response(
            200, json={"object_kind": "metric", "ref": "metric.watch_time"}, request=request
        )

    result = _invoke_registered_tool(
        "resolve_typed_ref",
        handler,
        ref="metric.watch_time",
    )

    assert result["ok"] is True
    assert result["data"] == {"object_kind": "metric", "ref": "metric.watch_time"}


def test_resolve_typed_ref_preserves_not_found_for_bare_name_lookup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/resolve/watch_time"
        return httpx.Response(404, json={"detail": "'watch_time' not found"}, request=request)

    result = _invoke_registered_tool(
        "resolve_typed_ref",
        handler,
        ref="watch_time",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"


def test_resolve_typed_ref_preserves_not_found_for_non_public_namespace_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/resolve/key.user_id"
        return httpx.Response(404, json={"detail": "'key.user_id' not found"}, request=request)

    result = _invoke_registered_tool(
        "resolve_typed_ref",
        handler,
        ref="key.user_id",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"


def test_create_session_uses_canonical_session_root_request_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions"
        assert request.read() == (
            b'{"goal":"Investigate watch time","budget":{"max_latency_sec":30},'
            b'"policy":{"aggregate_only":true}}'
        )
        return httpx.Response(200, json={"session_id": "sess_123"}, request=request)

    result = _invoke_registered_tool(
        "create_session",
        handler,
        goal="Investigate watch time",
        budget={"max_latency_sec": 30},
        policy={"aggregate_only": True},
    )

    assert result["ok"] is True
    assert result["data"] == {"session_id": "sess_123"}
    assert result["meta"]["factum_path"] == "/sessions"


def test_create_session_omits_null_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions"
        assert request.read() == b'{"goal":"Minimal session"}'
        return httpx.Response(200, json={"session_id": "sess_min"}, request=request)

    result = _invoke_registered_tool(
        "create_session",
        handler,
        goal="Minimal session",
    )

    assert result["ok"] is True
    assert result["data"] == {"session_id": "sess_min"}


def test_get_session_uses_session_root_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/sess_123"
        return httpx.Response(200, json={"session_id": "sess_123"}, request=request)

    result = _invoke_registered_tool(
        "get_session",
        handler,
        session_id="sess_123",
    )

    assert result["ok"] is True
    assert result["data"] == {"session_id": "sess_123"}


def test_get_session_preserves_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions/sess_missing"
        return httpx.Response(404, json={"detail": "'sess_missing' not found"}, request=request)

    result = _invoke_registered_tool(
        "get_session",
        handler,
        session_id="sess_missing",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"


def test_get_session_state_forwards_repeated_query_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/sess_123/state"
        params = request.url.params
        assert params.get("metric") == "watch_time"
        assert params.get("entity") == "video"
        assert params.get_list("proposition_type") == ["metric_status", "metric_delta"]
        assert params.get_list("origin_kind") == ["system_seeded"]
        assert params.get("assessment_presence") == "assessed"
        assert params.get_list("assessment_status") == ["insufficient", "supported"]
        assert params.get("has_blocking_gaps") == "true"
        assert params.get("limit") == "25"
        assert params.get("page_token") == "cursor_1"
        return httpx.Response(200, json={"session_id": "sess_123"}, request=request)

    result = _invoke_registered_tool(
        "get_session_state",
        handler,
        session_id="sess_123",
        metric="watch_time",
        entity="video",
        proposition_type=["metric_status", "metric_delta"],
        origin_kind=["system_seeded"],
        assessment_presence="assessed",
        assessment_status=["insufficient", "supported"],
        has_blocking_gaps=True,
        limit=25,
        page_token="cursor_1",
    )

    assert result["ok"] is True
    assert result["meta"]["factum_path"] == "/sessions/sess_123/state"


def test_get_session_state_omits_empty_query_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions/sess_123/state"
        assert request.url.query == b""
        return httpx.Response(200, json={"session_id": "sess_123"}, request=request)

    result = _invoke_registered_tool(
        "get_session_state",
        handler,
        session_id="sess_123",
        proposition_type=[],
        origin_kind=[],
        assessment_status=[],
    )

    assert result["ok"] is True


def test_get_session_state_preserves_http_400_for_invalid_get_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "detail": "'slice' is not supported on GET /state. Use POST /state/query instead."
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "get_session_state",
        handler,
        session_id="sess_123",
    )

    assert result["ok"] is False
    assert result["status_code"] == 400
    assert result["error"]["message"] == (
        "'slice' is not supported on GET /state. Use POST /state/query instead."
    )


def test_query_session_state_sends_page_token_as_query_and_body_as_canonical_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/state/query"
        assert dict(request.url.params) == {"page_token": "cursor_2"}
        assert request.read() == (
            b'{"metric":"watch_time","entity":"video","slice":{"country":"US"},'
            b'"proposition_types":["metric_status"],"origin_kinds":["system_seeded"],'
            b'"assessment_presence":"assessed","assessment_statuses":["insufficient"],'
            b'"has_blocking_gaps":true,"limit":10}'
        )
        return httpx.Response(200, json={"session_id": "sess_123"}, request=request)

    result = _invoke_registered_tool(
        "query_session_state",
        handler,
        session_id="sess_123",
        metric="watch_time",
        entity="video",
        slice={"country": "US"},
        proposition_types=["metric_status"],
        origin_kinds=["system_seeded"],
        assessment_presence="assessed",
        assessment_statuses=["insufficient"],
        has_blocking_gaps=True,
        limit=10,
        page_token="cursor_2",
    )

    assert result["ok"] is True
    assert result["meta"]["factum_path"] == "/sessions/sess_123/state/query"


def test_query_session_state_preserves_validation_error_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "assessment_presence"],
                        "msg": "Input should be 'assessed' or 'unassessed'",
                        "type": "literal_error",
                    }
                ]
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "query_session_state",
        handler,
        session_id="sess_123",
        assessment_presence="invalid",
    )

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error"]["category"] == "validation"
    assert result["error"]["detail"] == [
        {
            "loc": ["body", "assessment_presence"],
            "msg": "Input should be 'assessed' or 'unassessed'",
            "type": "literal_error",
        }
    ]


def test_get_proposition_context_uses_canonical_context_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/sess_123/propositions/prop_456/context"
        return httpx.Response(
            200, json={"schema_version": "proposition_context_view.v1"}, request=request
        )

    result = _invoke_registered_tool(
        "get_proposition_context",
        handler,
        session_id="sess_123",
        proposition_id="prop_456",
    )

    assert result["ok"] is True
    assert result["data"] == {"schema_version": "proposition_context_view.v1"}


def test_get_proposition_context_preserves_not_found_for_cross_session_or_missing_proposition() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions/sess_123/propositions/prop_456/context"
        return httpx.Response(
            404,
            json={"detail": "Proposition 'prop_456' not found in session 'sess_123'"},
            request=request,
        )

    result = _invoke_registered_tool(
        "get_proposition_context",
        handler,
        session_id="sess_123",
        proposition_id="prop_456",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"


def test_observe_uses_canonical_observe_request_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/observe"
        assert request.read() == (
            b'{"metric":"metric.watch_time","time_scope":{"kind":"range","start":"2025-03-01",'
            b'"end":"2025-03-08"},"result_mode":"standard","scope":{"dimension_filters":'
            b'{"country":"US"}},"granularity":"day","dimensions":["device_type"]}'
        )
        return httpx.Response(200, json={"artifact_id": "obs_123"}, request=request)

    result = _invoke_registered_tool(
        "observe",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={"kind": "range", "start": "2025-03-01", "end": "2025-03-08"},
        scope={"dimension_filters": {"country": "US"}},
        granularity="day",
        dimensions=["device_type"],
    )

    assert result["ok"] is True
    assert result["data"] == {"artifact_id": "obs_123"}
    assert result["meta"]["factum_path"] == "/sessions/sess_123/intents/observe"


def test_compare_uses_path_discriminated_contract_and_default_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/compare"
        assert request.read() == (
            b'{"left_ref":{"artifact_id":"obs_left","step_type":"observe"},'
            b'"right_ref":{"artifact_id":"obs_right","step_type":"observe"},'
            b'"mode":"auto"}'
        )
        return httpx.Response(200, json={"artifact_id": "cmp_123"}, request=request)

    result = _invoke_registered_tool(
        "compare",
        handler,
        session_id="sess_123",
        left_ref={"artifact_id": "obs_left", "step_type": "observe"},
        right_ref={"artifact_id": "obs_right", "step_type": "observe"},
    )

    assert result["ok"] is True
    assert result["data"] == {"artifact_id": "cmp_123"}


def test_decompose_uses_compare_ref_and_default_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/decompose"
        assert request.read() == (
            b'{"compare_ref":{"artifact_id":"cmp_123","step_type":"compare"},'
            b'"dimension":"dimension.country","method":"delta_share"}'
        )
        return httpx.Response(200, json={"artifact_id": "decomp_123"}, request=request)

    result = _invoke_registered_tool(
        "decompose",
        handler,
        session_id="sess_123",
        compare_ref={"artifact_id": "cmp_123", "step_type": "compare"},
        dimension="dimension.country",
    )

    assert result["ok"] is True


def test_correlate_forwards_min_pairs_and_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/correlate"
        assert request.read() == (
            b'{"left_ref":{"artifact_id":"obs_left","step_type":"observe"},'
            b'"right_ref":{"artifact_id":"obs_right","step_type":"observe"},'
            b'"method":"pearson","min_pairs":8}'
        )
        return httpx.Response(200, json={"artifact_id": "corr_123"}, request=request)

    result = _invoke_registered_tool(
        "correlate",
        handler,
        session_id="sess_123",
        left_ref={"artifact_id": "obs_left", "step_type": "observe"},
        right_ref={"artifact_id": "obs_right", "step_type": "observe"},
        method="pearson",
        min_pairs=8,
    )

    assert result["ok"] is True


def test_detect_omits_null_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/detect"
        assert request.read() == (
            b'{"metric":"metric.watch_time","time_scope":{"mode":"single_window","grain":"day",'
            b'"current":{"start":"2025-03-01","end":"2025-03-08"}},'
            b'"profile":"auto","sensitivity":"balanced"}'
        )
        return httpx.Response(200, json={"artifact_id": "detect_123"}, request=request)

    result = _invoke_registered_tool(
        "detect",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2025-03-01", "end": "2025-03-08"},
        },
    )

    assert result["ok"] is True


def test_test_intent_uses_test_endpoint_and_default_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/test"
        assert request.read() == (
            b'{"left_ref":{"artifact_id":"obs_left","step_type":"observe"},'
            b'"right_ref":{"artifact_id":"obs_right","step_type":"observe"},'
            b'"hypothesis":{"family":"difference","alternative":"two_sided","alpha":0.05},'
            b'"method":"auto"}'
        )
        return httpx.Response(200, json={"artifact_id": "test_123"}, request=request)

    result = _invoke_registered_tool(
        "test_intent",
        handler,
        session_id="sess_123",
        left_ref={"artifact_id": "obs_left", "step_type": "observe"},
        right_ref={"artifact_id": "obs_right", "step_type": "observe"},
        hypothesis={"family": "difference", "alternative": "two_sided", "alpha": 0.05},
    )

    assert result["ok"] is True


def test_forecast_uses_canonical_forecast_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/forecast"
        assert request.read() == (
            b'{"source_ref":{"artifact_id":"obs_series","step_type":"observe"},'
            b'"horizon":14,"profile":"trend","interval_level":0.9}'
        )
        return httpx.Response(200, json={"artifact_id": "forecast_123"}, request=request)

    result = _invoke_registered_tool(
        "forecast",
        handler,
        session_id="sess_123",
        source_ref={"artifact_id": "obs_series", "step_type": "observe"},
        horizon=14,
        profile="trend",
        interval_level=0.9,
    )

    assert result["ok"] is True


def test_attribute_uses_canonical_derived_intent_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/attribute"
        assert request.read() == (
            b'{"metric":"metric.watch_time","left":{"time_scope":{"kind":"range","start":"2025-03-01",'
            b'"end":"2025-03-08"}},"right":{"time_scope":{"kind":"range","start":"2025-02-22",'
            b'"end":"2025-03-01"}},"dimensions":["dimension.country","dimension.device_type"],'
            b'"decomposition_method":"delta_share","decomposition_limit":7}'
        )
        return httpx.Response(200, json={"artifact_id": "attribute_123"}, request=request)

    result = _invoke_registered_tool(
        "attribute",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        left={"time_scope": {"kind": "range", "start": "2025-03-01", "end": "2025-03-08"}},
        right={"time_scope": {"kind": "range", "start": "2025-02-22", "end": "2025-03-01"}},
        dimensions=["dimension.country", "dimension.device_type"],
        decomposition_limit=7,
    )

    assert result["ok"] is True


def test_diagnose_uses_default_followup_limit_and_path_discriminated_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/diagnose"
        assert request.read() == (
            b'{"metric":"metric.watch_time","time_scope":{"mode":"single_window","grain":"day",'
            b'"current":{"start":"2025-03-01","end":"2025-03-08"}},'
            b'"candidate_dimensions":["dimension.country"],"profile":"auto",'
            b'"sensitivity":"balanced","followup_limit":3}'
        )
        return httpx.Response(200, json={"artifact_id": "diagnose_123"}, request=request)

    result = _invoke_registered_tool(
        "diagnose",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2025-03-01", "end": "2025-03-08"},
        },
        candidate_dimensions=["dimension.country"],
    )

    assert result["ok"] is True


def test_validate_omits_null_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/validate"
        assert request.read() == (
            b'{"metric":"metric.conversion_rate","left":{"time_scope":{"kind":"range",'
            b'"start":"2025-03-01","end":"2025-03-08"}},"right":{"time_scope":{"kind":"range",'
            b'"start":"2025-02-22","end":"2025-03-01"}}}'
        )
        return httpx.Response(200, json={"artifact_id": "validate_123"}, request=request)

    result = _invoke_registered_tool(
        "validate",
        handler,
        session_id="sess_123",
        metric="metric.conversion_rate",
        left={"time_scope": {"kind": "range", "start": "2025-03-01", "end": "2025-03-08"}},
        right={"time_scope": {"kind": "range", "start": "2025-02-22", "end": "2025-03-01"}},
    )

    assert result["ok"] is True


def test_observe_accepts_pydantic_time_scope_models_and_serializes_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/observe"
        assert request.read() == (
            b'{"metric":"metric.watch_time","time_scope":{"kind":"as_of","at":"2025-03-08T00:00:00"},'
            b'"result_mode":"standard"}'
        )
        return httpx.Response(200, json={"artifact_id": "obs_123"}, request=request)

    result = _invoke_registered_tool(
        "observe",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope=ObserveTimeScopeAsOf(kind="as_of", at="2025-03-08T00:00:00"),
    )

    assert result["ok"] is True


def test_detect_accepts_pydantic_time_scope_models_and_serializes_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/detect"
        assert request.read() == (
            b'{"metric":"metric.watch_time","time_scope":{"mode":"single_window","grain":"day",'
            b'"current":{"start":"2025-03-01","end":"2025-03-08"}},'
            b'"profile":"auto","sensitivity":"balanced"}'
        )
        return httpx.Response(200, json={"artifact_id": "detect_123"}, request=request)

    result = _invoke_registered_tool(
        "detect",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope=DetectTimeScope(
            mode="single_window",
            grain="day",
            current=DetectTimeScopeCurrentWindow(start="2025-03-01", end="2025-03-08"),
        ),
    )

    assert result["ok"] is True


def test_validate_accepts_pydantic_nested_models_and_serializes_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/validate"
        assert request.read() == (
            b'{"metric":"metric.conversion_rate","left":{"time_scope":{"kind":"range",'
            b'"start":"2025-03-01","end":"2025-03-08"}},"right":{"time_scope":{"kind":"range",'
            b'"start":"2025-02-22","end":"2025-03-01"}},"hypothesis":{"family":"difference",'
            b'"alternative":"greater","alpha":0.1,"label":"lift"}}'
        )
        return httpx.Response(200, json={"artifact_id": "validate_123"}, request=request)

    result = _invoke_registered_tool(
        "validate",
        handler,
        session_id="sess_123",
        metric="metric.conversion_rate",
        left=ValidateObservationInput(
            time_scope=ObserveTimeScopeRange(kind="range", start="2025-03-01", end="2025-03-08")
        ),
        right=ValidateObservationInput(
            time_scope=ObserveTimeScopeRange(kind="range", start="2025-02-22", end="2025-03-01")
        ),
        hypothesis=ValidateHypothesis(
            family="difference",
            alternative="greater",
            alpha=0.1,
            label="lift",
        ),
    )

    assert result["ok"] is True


def test_test_intent_accepts_pydantic_nested_models_and_serializes_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/test"
        assert request.read() == (
            b'{"left_ref":{"step_id":"obs_left","step_type":"observe","artifact_id":"artifact_left",'
            b'"observation_type":"numeric_sample_summary"},"right_ref":{"step_id":"obs_right",'
            b'"step_type":"observe","artifact_id":"artifact_right","observation_type":"rate_sample_summary"},'
            b'"hypothesis":{"family":"difference","alternative":"less","alpha":0.2},'
            b'"method":"auto"}'
        )
        return httpx.Response(200, json={"artifact_id": "test_123"}, request=request)

    result = _invoke_registered_tool(
        "test_intent",
        handler,
        session_id="sess_123",
        left_ref=TestObservationRef(
            step_id="obs_left",
            step_type="observe",
            artifact_id="artifact_left",
            observation_type="numeric_sample_summary",
        ),
        right_ref=TestObservationRef(
            step_id="obs_right",
            step_type="observe",
            artifact_id="artifact_right",
            observation_type="rate_sample_summary",
        ),
        hypothesis=HypothesisContract(
            family="difference",
            alternative="less",
            alpha=0.2,
        ),
    )

    assert result["ok"] is True


def test_intent_tools_preserve_422_guidance_from_factum() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions/sess_123/intents/observe"
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "time_scope"],
                        "msg": "Field required",
                        "type": "missing",
                    }
                ],
                "error": {
                    "code": "request_validation_error",
                    "message": "Request validation failed. Use the guided example and contract links.",
                },
                "guidance": {
                    "contract_url": "/openapi/paths/L3Nlc3Npb25zL3tzZXNzaW9uX2lkfS9pbnRlbnRzL29ic2VydmU?operation=post&expand=request,schemas&depth=2",
                    "schema_url": "/openapi/schemas/ObserveRequest?depth=2",
                    "examples": [
                        {"summary": "Minimal payload", "payload": {"metric": "metric.watch_time"}}
                    ],
                },
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "observe",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={},
    )

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error"]["category"] == "validation"
    assert result["error"]["guidance"] == {
        "contract_url": "/openapi/paths/L3Nlc3Npb25zL3tzZXNzaW9uX2lkfS9pbnRlbnRzL29ic2VydmU?operation=post&expand=request,schemas&depth=2",
        "schema_url": "/openapi/schemas/ObserveRequest?depth=2",
        "examples": [{"summary": "Minimal payload", "payload": {"metric": "metric.watch_time"}}],
    }
    assert "guidance.examples" in cast("str", result["error"]["remediation_hint"])


def test_semantic_create_tools_use_inventory_names_and_canonical_paths() -> None:
    cases: list[tuple[str, str, dict[str, object]]] = [
        (
            "create_entity",
            "/semantic/entities",
            {
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {"identity": {"key_refs": ["key.user_id"]}},
            },
        ),
        (
            "create_metric",
            "/semantic/metrics",
            {
                "header": {"metric_ref": "metric.watch_time"},
                "payload": {"metric_family": "count_metric"},
            },
        ),
        (
            "create_process_object",
            "/semantic/process-objects",
            {
                "header": {"process_ref": "process.watch_events"},
                "interface_contract": {"contract_mode": "entity_stream"},
                "payload": {"process_type": "event_stream"},
            },
        ),
        (
            "create_dimension",
            "/semantic/dimensions",
            {
                "header": {"dimension_ref": "dimension.country"},
                "interface_contract": {"grouping": {"supports_grouping": True}},
            },
        ),
        (
            "create_time_semantic",
            "/semantic/time",
            {"header": {"time_ref": "time.created_at"}},
        ),
        (
            "create_enum_set",
            "/semantic/enum-sets",
            {
                "header": {"enum_set_ref": "enum.country_code"},
                "display_name": "Country Code",
                "versions": [{"enum_version": "v1", "values": []}],
            },
        ),
        (
            "create_binding",
            "/semantic/bindings",
            {
                "header": {"binding_ref": "binding.user_events"},
                "interface_contract": {"carrier_bindings": [], "field_bindings": []},
            },
        ),
        (
            "create_compatibility_profile",
            "/compiler/compatibility-profiles",
            {
                "profile_ref": "compiler_profile.metric_requirement",
                "profile_kind": "requirement",
                "schema_version": "v1",
                "subject_kind": "metric",
                "subject_ref": "metric.watch_time",
                "requirement": {"required_process_refs": ["process.watch_events"]},
            },
        ),
    ]

    for tool_name, expected_path, tool_kwargs in cases:
        expected_body = httpx.Request("POST", "http://factum.test", json=tool_kwargs).read()

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
            expected_body: bytes = expected_body,
            tool_name: str = tool_name,
        ) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == expected_path
            assert request.read() == expected_body
            return httpx.Response(200, json={"tool": tool_name}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["data"] == {"tool": tool_name}
        assert result["meta"]["factum_path"] == expected_path


def test_semantic_list_and_get_tools_forward_canonical_status_and_path_parameters() -> None:
    cases: list[tuple[str, str, dict[str, str]]] = [
        ("list_entities", "/semantic/entities", {"status": "published"}),
        ("list_metrics", "/semantic/metrics", {"status": "draft"}),
        ("list_process_objects", "/semantic/process-objects", {"status": "published"}),
        ("list_dimensions", "/semantic/dimensions", {"status": "draft"}),
        ("list_time_semantics", "/semantic/time", {"status": "published"}),
        ("list_enum_sets", "/semantic/enum-sets", {"status": "draft"}),
        ("list_bindings", "/semantic/bindings", {"status": "published"}),
        ("list_compatibility_profiles", "/compiler/compatibility-profiles", {"status": "draft"}),
        ("get_entity", "/semantic/entities/ent_123", {"entity_id": "ent_123"}),
        ("get_metric", "/semantic/metrics/met_123", {"metric_id": "met_123"}),
        (
            "get_process_object",
            "/semantic/process-objects/proc_123",
            {"process_contract_id": "proc_123"},
        ),
        ("get_dimension", "/semantic/dimensions/dim_123", {"dimension_contract_id": "dim_123"}),
        ("get_time_semantic", "/semantic/time/time_123", {"time_contract_id": "time_123"}),
        ("get_enum_set", "/semantic/enum-sets/enum_123", {"enum_set_contract_id": "enum_123"}),
        ("get_binding", "/semantic/bindings/bind_123", {"binding_id": "bind_123"}),
        (
            "get_compatibility_profile",
            "/compiler/compatibility-profiles/cprof_123",
            {"profile_id": "cprof_123"},
        ),
    ]

    for tool_name, expected_path, tool_kwargs in cases:

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
            tool_name: str = tool_name,
            tool_kwargs: dict[str, str] = tool_kwargs,
        ) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == expected_path
            if tool_name.startswith("list_"):
                assert dict(request.url.params) == {"status": next(iter(tool_kwargs.values()))}
            else:
                assert request.url.query == b""
            return httpx.Response(200, json={"tool": tool_name}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["meta"]["factum_path"] == expected_path


def test_semantic_list_tools_forward_detail_query_parameter_when_requested() -> None:
    cases: list[tuple[str, str, dict[str, object]]] = [
        ("list_entities", "/semantic/entities", {"status": "published", "detail": True}),
        ("list_metrics", "/semantic/metrics", {"status": "draft", "detail": True}),
        (
            "list_process_objects",
            "/semantic/process-objects",
            {"status": "published", "detail": True},
        ),
        ("list_dimensions", "/semantic/dimensions", {"status": "draft", "detail": True}),
        ("list_time_semantics", "/semantic/time", {"status": "published", "detail": True}),
        ("list_enum_sets", "/semantic/enum-sets", {"status": "draft", "detail": True}),
        ("list_bindings", "/semantic/bindings", {"status": "published", "detail": True}),
        (
            "list_compatibility_profiles",
            "/compiler/compatibility-profiles",
            {"status": "draft", "detail": True},
        ),
    ]

    for tool_name, expected_path, tool_kwargs in cases:

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
            tool_name: str = tool_name,
            tool_kwargs: dict[str, object] = tool_kwargs,
        ) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == expected_path
            assert dict(request.url.params) == {
                "status": str(tool_kwargs["status"]),
                "detail": "true",
            }
            return httpx.Response(200, json={"tool": tool_name}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["meta"]["factum_path"] == expected_path


def test_semantic_list_tools_omit_detail_query_parameter_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/semantic/bindings"
        assert dict(request.url.params) == {"status": "published"}
        return httpx.Response(200, json={"items": []}, request=request)

    result = _invoke_registered_tool("list_bindings", handler, status="published")

    assert result["ok"] is True
    assert result["data"] == {"items": []}


def test_semantic_update_tools_send_only_canonical_body_fields() -> None:
    cases: list[tuple[str, str, dict[str, object], dict[str, object]]] = [
        (
            "update_entity",
            "/semantic/entities/ent_123",
            {"entity_id": "ent_123", "display_name": "User", "description": "Updated"},
            {"display_name": "User", "description": "Updated"},
        ),
        (
            "update_metric",
            "/semantic/metrics/met_123",
            {"metric_id": "met_123", "payload": {"metric_family": "count_metric"}},
            {"payload": {"metric_family": "count_metric"}},
        ),
        (
            "update_process_object",
            "/semantic/process-objects/proc_123",
            {
                "process_contract_id": "proc_123",
                "interface_contract": {"contract_mode": "entity_stream"},
            },
            {"interface_contract": {"contract_mode": "entity_stream"}},
        ),
        (
            "update_dimension",
            "/semantic/dimensions/dim_123",
            {
                "dimension_contract_id": "dim_123",
                "interface_contract": {"grouping": {"supports_grouping": True}},
            },
            {"interface_contract": {"grouping": {"supports_grouping": True}}},
        ),
        (
            "update_time_semantic",
            "/semantic/time/time_123",
            {"time_contract_id": "time_123", "semantic_roles": ["business_anchor"]},
            {"semantic_roles": ["business_anchor"]},
        ),
        (
            "update_enum_set",
            "/semantic/enum-sets/enum_123",
            {"enum_set_contract_id": "enum_123", "display_name": "Country Code"},
            {"display_name": "Country Code"},
        ),
        (
            "update_binding",
            "/semantic/bindings/bind_123",
            {"binding_id": "bind_123", "description": "Updated"},
            {"description": "Updated"},
        ),
        (
            "update_compatibility_profile",
            "/compiler/compatibility-profiles/cprof_123",
            {
                "profile_id": "cprof_123",
                "capability": {"provided_process_refs": ["process.watch_events"]},
            },
            {"capability": {"provided_process_refs": ["process.watch_events"]}},
        ),
    ]

    for tool_name, expected_path, tool_kwargs, expected_body_payload in cases:
        expected_body = httpx.Request(
            "PUT", "http://factum.test", json=expected_body_payload
        ).read()

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
            expected_body: bytes = expected_body,
            tool_name: str = tool_name,
        ) -> httpx.Response:
            assert request.method == "PUT"
            assert request.url.path == expected_path
            assert request.read() == expected_body
            return httpx.Response(200, json={"tool": tool_name}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["meta"]["factum_path"] == expected_path


def test_semantic_publish_tools_use_canonical_publish_paths() -> None:
    cases = [
        ("publish_entity", "/semantic/entities/ent_123/publish", {"entity_id": "ent_123"}),
        ("publish_metric", "/semantic/metrics/met_123/publish", {"metric_id": "met_123"}),
        (
            "publish_process_object",
            "/semantic/process-objects/proc_123/publish",
            {"process_contract_id": "proc_123"},
        ),
        (
            "publish_dimension",
            "/semantic/dimensions/dim_123/publish",
            {"dimension_contract_id": "dim_123"},
        ),
        (
            "publish_time_semantic",
            "/semantic/time/time_123/publish",
            {"time_contract_id": "time_123"},
        ),
        (
            "publish_enum_set",
            "/semantic/enum-sets/enum_123/publish",
            {"enum_set_contract_id": "enum_123"},
        ),
        ("publish_binding", "/semantic/bindings/bind_123/publish", {"binding_id": "bind_123"}),
        (
            "publish_compatibility_profile",
            "/compiler/compatibility-profiles/cprof_123/publish",
            {"profile_id": "cprof_123"},
        ),
    ]

    for tool_name, expected_path, tool_kwargs in cases:

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
        ) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == expected_path
            assert request.read() == b""
            return httpx.Response(200, json={"status": "published"}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["data"] == {"status": "published"}
        assert result["meta"]["factum_path"] == expected_path


def test_semantic_lifecycle_tools_use_canonical_validate_activate_and_deprecate_paths() -> None:
    cases = [
        ("validate_entity", "/semantic/entities/ent_123/validate", {"entity_id": "ent_123"}),
        ("activate_metric", "/semantic/metrics/met_123/activate", {"metric_id": "met_123"}),
        (
            "deprecate_process_object",
            "/semantic/process-objects/proc_123/deprecate",
            {"process_contract_id": "proc_123"},
        ),
        (
            "validate_dimension",
            "/semantic/dimensions/dim_123/validate",
            {"dimension_contract_id": "dim_123"},
        ),
        (
            "activate_time_semantic",
            "/semantic/time/time_123/activate",
            {"time_contract_id": "time_123"},
        ),
        (
            "deprecate_enum_set",
            "/semantic/enum-sets/enum_123/deprecate",
            {"enum_set_contract_id": "enum_123"},
        ),
        ("validate_binding", "/semantic/bindings/bind_123/validate", {"binding_id": "bind_123"}),
        (
            "activate_compatibility_profile",
            "/compiler/compatibility-profiles/cprof_123/activate",
            {"profile_id": "cprof_123"},
        ),
    ]

    for tool_name, expected_path, tool_kwargs in cases:

        def handler(
            request: httpx.Request,
            expected_path: str = expected_path,
        ) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == expected_path
            assert request.read() == b""
            return httpx.Response(200, json={"path": expected_path}, request=request)

        result = _invoke_registered_tool(tool_name, handler, **tool_kwargs)

        assert result["ok"] is True
        assert result["data"] == {"path": expected_path}
        assert result["meta"]["factum_path"] == expected_path


def test_publish_errors_extract_message_and_code_from_structured_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/entities/ent_123/publish"
        return httpx.Response(
            422,
            json={
                "detail": {
                    "message": "Entity 'ent_123' is not in draft status (status=published).",
                    "code": "publish_state_error",
                    "category": "state",
                }
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "publish_entity",
        handler,
        entity_id="ent_123",
    )

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error"]["category"] == "validation"
    assert result["error"]["message"] == (
        "Entity 'ent_123' is not in draft status (status=published)."
    )
    assert result["error"]["code"] == "publish_state_error"
    assert result["error"]["detail"] == {
        "message": "Entity 'ent_123' is not in draft status (status=published).",
        "code": "publish_state_error",
        "category": "state",
    }


def test_publish_binding_preserves_reference_validation_failure_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/bindings/bind_123/publish"
        return httpx.Response(
            422,
            json={
                "detail": {
                    "message": "All imported semantic refs must be published before binding publish.",
                    "code": "reference_validation_error",
                    "category": "validation",
                }
            },
            request=request,
        )

    result = _invoke_registered_tool(
        "publish_binding",
        handler,
        binding_id="bind_123",
    )

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error"]["message"] == (
        "All imported semantic refs must be published before binding publish."
    )
    assert result["error"]["code"] == "reference_validation_error"


def test_publish_compatibility_profile_preserves_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/compiler/compatibility-profiles/cprof_missing/publish"
        return httpx.Response(404, json={"detail": "'cprof_missing' not found"}, request=request)

    result = _invoke_registered_tool(
        "publish_compatibility_profile",
        handler,
        profile_id="cprof_missing",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"


def test_list_sources_uses_canonical_sources_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sources"
        assert request.url.query == b""
        return httpx.Response(200, json=[{"source_id": "src_123"}], request=request)

    result = _invoke_registered_tool("list_sources", handler)

    assert result["ok"] is True
    assert result["data"] == [{"source_id": "src_123"}]
    assert result["meta"]["factum_path"] == "/sources"


def test_register_source_uses_only_canonical_body_fields() -> None:
    tool_kwargs = {
        "source_type": "duckdb",
        "display_name": "Analytics DuckDB",
        "connection": {"db_path": "/data/analytics.duckdb"},
        "capabilities": {"supports_partitions": False},
    }
    expected_body = httpx.Request("POST", "http://factum.test", json=tool_kwargs).read()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sources"
        assert request.read() == expected_body
        return httpx.Response(200, json={"source_id": "src_123"}, request=request)

    result = _invoke_registered_tool("register_source", handler, **tool_kwargs)

    assert result["ok"] is True
    assert result["data"] == {"source_id": "src_123"}
    assert result["meta"]["factum_path"] == "/sources"


def test_register_source_omits_none_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sources"
        assert request.read() == (
            httpx.Request(
                "POST",
                "http://factum.test",
                json={"source_type": "trino", "display_name": "Warehouse"},
            ).read()
        )
        return httpx.Response(200, json={"source_id": "src_456"}, request=request)

    result = _invoke_registered_tool(
        "register_source",
        handler,
        source_type="trino",
        display_name="Warehouse",
    )

    assert result["ok"] is True
    assert result["data"] == {"source_id": "src_456"}


def test_sync_source_uses_canonical_sync_route_without_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sources/src_123/sync"
        assert request.read() == b""
        return httpx.Response(
            200,
            json={"job_id": "sync_123", "source_id": "src_123", "status": "succeeded"},
            request=request,
        )

    result = _invoke_registered_tool("sync_source", handler, source_id="src_123")

    assert result["ok"] is True
    assert result["data"] == {"job_id": "sync_123", "source_id": "src_123", "status": "succeeded"}
    assert result["meta"]["factum_path"] == "/sources/src_123/sync"


def test_sync_source_preserves_not_found_and_client_failure_shapes() -> None:
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/src_missing/sync"
        return httpx.Response(404, json={"detail": "'src_missing' not found"}, request=request)

    not_found = _invoke_registered_tool("sync_source", not_found_handler, source_id="src_missing")

    assert not_found["ok"] is False
    assert not_found["status_code"] == 404
    assert not_found["error"]["category"] == "not_found"
    assert not_found["error"]["message"] == "'src_missing' not found"

    def client_error_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/src_disabled/sync"
        return httpx.Response(
            400,
            json={"detail": "Sync disabled for this source (mode=none)"},
            request=request,
        )

    client_error = _invoke_registered_tool(
        "sync_source",
        client_error_handler,
        source_id="src_disabled",
    )

    assert client_error["ok"] is False
    assert client_error["status_code"] == 400
    assert client_error["error"]["category"] == "server_error"
    assert client_error["error"]["message"] == "Sync disabled for this source (mode=none)"


def test_get_source_objects_uses_only_canonical_type_and_schema_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sources/src_123/objects"
        assert dict(request.url.params) == {"type": "table", "schema": "events"}
        return httpx.Response(200, json=[{"object_id": "obj_123"}], request=request)

    result = _invoke_registered_tool(
        "get_source_objects",
        handler,
        source_id="src_123",
        type="table",
        schema="events",
    )

    assert result["ok"] is True
    assert result["data"] == [{"object_id": "obj_123"}]
    assert result["meta"]["factum_path"] == "/sources/src_123/objects"


def test_get_source_objects_preserves_missing_source_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/src_missing/objects"
        return httpx.Response(404, json={"detail": "'src_missing' not found"}, request=request)

    result = _invoke_registered_tool(
        "get_source_objects",
        handler,
        source_id="src_missing",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"
    assert result["error"]["message"] == "'src_missing' not found"


def test_get_source_object_reads_one_synced_object_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sources/src_123/objects/obj_456"
        assert dict(request.url.params) == {}
        return httpx.Response(200, json={"object_id": "obj_456"}, request=request)

    result = _invoke_registered_tool(
        "get_source_object",
        handler,
        source_id="src_123",
        object_id="obj_456",
    )

    assert result["ok"] is True
    assert result["data"] == {"object_id": "obj_456"}
    assert result["meta"]["factum_path"] == "/sources/src_123/objects/obj_456"


def test_get_source_object_preserves_missing_object_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/src_123/objects/obj_missing"
        return httpx.Response(404, json={"detail": "'obj_missing' not found"}, request=request)

    result = _invoke_registered_tool(
        "get_source_object",
        handler,
        source_id="src_123",
        object_id="obj_missing",
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error"]["category"] == "not_found"
    assert result["error"]["message"] == "'obj_missing' not found"


def test_resolve_routing_uses_canonical_nested_payload() -> None:
    tool_kwargs = {
        "table_names": ["events.user_video_watch", "dimensions.video_metadata"],
        "routing_intent": {
            "step_type": "aggregate_query",
            "requested_dimensions": ["device_type"],
            "compatible_dimensions": ["device_type", "region"],
            "legal_grains": ["daily"],
            "policy_hints": ["aggregate_only"],
        },
    }
    expected_body = httpx.Request("POST", "http://factum.test", json=tool_kwargs).read()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/routing/resolve"
        assert request.read() == expected_body
        return httpx.Response(
            200, json={"resolved": True, "engine": {"engine_id": "eng_123"}}, request=request
        )

    result = _invoke_registered_tool("resolve_routing", handler, **tool_kwargs)

    assert result["ok"] is True
    assert result["data"] == {"resolved": True, "engine": {"engine_id": "eng_123"}}
    assert result["meta"]["factum_path"] == "/routing/resolve"


def _invoke_registered_tool(
    tool_name: str,
    handler: Any,
    /,
    **tool_kwargs: Any,
) -> dict[str, Any]:
    tools_module_any = cast("Any", tools_module)
    original_client = tools_module_any.FactumHttpClient

    def build_client(config: Any) -> Any:
        return FactumHttpClient(config, transport=httpx.MockTransport(handler))

    openapi_cache = tool_kwargs.pop("_openapi_cache", None)
    tools_module_any.FactumHttpClient = build_client
    try:
        server = cast("Any", _FakeServer())
        if openapi_cache is None:
            register_tools(server, _build_config())
        else:
            register_tools(server, _build_config(), openapi_cache=openapi_cache)
        return cast("dict[str, Any]", server.tools[tool_name](**tool_kwargs))
    finally:
        tools_module_any.FactumHttpClient = original_client


def test_semantic_tool_accepts_object_id_alias() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/entities/entc_123"
        return httpx.Response(200, json={"entity_contract_id": "entc_123"}, request=request)

    result = _invoke_registered_tool("get_entity", handler, object_id="entc_123")

    assert result["ok"] is True
    assert result["data"]["entity_contract_id"] == "entc_123"


def test_list_metrics_forwards_lifecycle_and_readiness_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic/metrics"
        assert request.url.params["lifecycle_status"] == "active"
        assert request.url.params["readiness_status"] == "ready"
        return httpx.Response(200, json={"items": [], "total": 0}, request=request)

    result = _invoke_registered_tool(
        "list_metrics",
        handler,
        lifecycle_status="active",
        readiness_status="ready",
    )

    assert result["ok"] is True
    assert result["data"] == {"items": [], "total": 0}
