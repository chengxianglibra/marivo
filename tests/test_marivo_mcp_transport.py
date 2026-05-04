from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

tools_module = import_module("marivo_mcp.tools")
config_module = import_module("marivo_mcp.config")
http_client_module = import_module("marivo_mcp.http_client")
openapi_cache_module = import_module("marivo_mcp.openapi_cache")

MarivoMcpConfig = config_module.MarivoMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
MarivoHttpClient = http_client_module.MarivoHttpClient
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
    return MarivoMcpConfig(
        base_url="http://marivo.test",
        api_token=api_token,
        timeout_ms=1500,
        openapi_cache_ttl_sec=300,
        default_datasource_id=None,
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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/health")

    assert envelope.ok is True
    assert envelope.status_code == 200
    assert envelope.data == {"status": "ok"}
    assert envelope.error is None
    assert envelope.meta.marivo_path == "/health"
    assert envelope.meta.method == "GET"
    assert envelope.meta.request_url == "http://marivo.test/health"
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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/health")

    assert envelope.ok is True
    assert envelope.data == {"raw_text": "ok"}
    assert envelope.meta.content_type == "text/plain"
    client.close()


def test_injects_bearer_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client = MarivoHttpClient(
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
                        "loc": ["body", "name"],
                        "msg": "field required",
                        "type": "value_error.missing",
                    }
                ],
                "error": {
                    "code": "request_validation_error",
                    "message": "Request validation failed. Use the guided example and contract links.",
                },
                "guidance": {
                    "schema_url": "/openapi/schemas/SemanticModel?depth=6",
                    "contract_url": "/openapi/paths/L3NlbWFudGljLW1vZGVscw",
                    "examples": [{"summary": "Minimal payload", "payload": {"name": "my_model"}}],
                    "next_action": "Start with guidance.examples.",
                },
            },
            request=request,
        )

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("POST", "/semantic-models", json_body={})

    assert envelope.ok is False
    assert envelope.status_code == 422
    assert envelope.error is not None
    assert envelope.error.category == "validation"
    assert envelope.error.code == "request_validation_error"
    assert envelope.error.detail == [
        {
            "loc": ["body", "name"],
            "msg": "field required",
            "type": "value_error.missing",
        }
    ]
    assert envelope.error.guidance == {
        "schema_url": "/openapi/schemas/SemanticModel?depth=6",
        "contract_url": "/openapi/paths/L3NlbWFudGljLW1vZGVscw",
        "examples": [{"summary": "Minimal payload", "payload": {"name": "my_model"}}],
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
        "/semantic-models/model_123/relationships": httpx.Response(
            409,
            json={"detail": "Relationship already exists"},
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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    not_found = client.request_envelope("GET", "/sessions/missing")
    conflict = client.request_envelope(
        "POST", "/semantic-models/model_123/relationships", json_body={}
    )

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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("GET", "/datasources")

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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
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

    client = MarivoHttpClient(_build_config(), transport=httpx.MockTransport(handler))
    envelope = client.request_envelope("POST", "/semantic-models", json_body={})

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
    original_client = tools_module_any.MarivoHttpClient

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    tools_module_any.MarivoHttpClient = build_client
    try:
        server = cast("Any", _FakeServer())
        register_tools(server, _build_config())
        result = server.tools["health_check"]()
    finally:
        tools_module_any.MarivoHttpClient = original_client

    assert result == {
        "ok": True,
        "status_code": 200,
        "data": {"status": "ok"},
        "error": None,
        "meta": {
            "marivo_path": "/health",
            "method": "GET",
            "request_url": "http://marivo.test/health",
            "attempt_count": 1,
            "content_type": "application/json",
        },
    }


def test_registers_core_tool_groups() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    # Session & intent tools
    assert set(server.tools) >= {
        "create_session",
        "list_sessions",
        "get_session",
        "terminate_session",
        "get_session_state",
        "query_session_state",
        "get_proposition_context",
        "health_check",
        "get_catalog",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
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
    # V2 Semantic Model tools
    assert set(server.tools) >= {
        "create_semantic_model",
        "list_semantic_models",
        "import_osi_document",
        "get_semantic_model",
        "update_semantic_model",
        "delete_semantic_model",
        "get_semantic_model_readiness",
        "create_dataset",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "delete_dataset",
        "create_relationship",
        "list_relationships",
        "get_relationship",
        "update_relationship",
        "delete_relationship",
        "create_metric",
        "list_metrics",
        "get_metric",
        "update_metric",
        "delete_metric",
    }
    # Governance tools
    assert set(server.tools) >= {
        "create_policy",
        "list_policies",
        "get_policy",
        "update_policy",
        "delete_policy",
        "create_quality_rule",
        "list_quality_rules",
        "delete_quality_rule",
        "governance_check",
    }
    # Calendar tools (removed from MCP surface)
    assert "load_calendar_data" not in server.tools
    assert "list_calendar_versions" not in server.tools
    # Routing tool (removed from MCP surface)
    assert "resolve_routing" not in server.tools
    # Datasource tools
    assert set(server.tools) >= {
        "list_datasources",
        "create_datasource",
        "browse_columns",
    }


def test_no_legacy_semantic_tools_registered() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    legacy_tools = {
        "create_entity",
        "list_entities",
        "get_entity",
        "update_entity",
        "validate_entity",
        "activate_entity",
        "deprecate_entity",
        "publish_entity",
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
        "validate_relationship",
        "activate_relationship",
        "deprecate_relationship",
        "publish_relationship",
        "create_compatibility_profile",
        "list_compatibility_profiles",
        "get_compatibility_profile",
        "update_compatibility_profile",
        "validate_compatibility_profile",
        "activate_compatibility_profile",
        "deprecate_compatibility_profile",
        "publish_compatibility_profile",
        "semantic_batch",
        "list_grains",
        "browse_catalogs",
    }

    for name in legacy_tools:
        assert name not in server.tools, f"Legacy tool {name!r} should not be registered"


# ------------------------------------------------------------------
# OpenAPI discovery tests
# ------------------------------------------------------------------


def test_list_openapi_paths_uses_openapi_index_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/index"
        assert request.url.query == b""
        return httpx.Response(200, json={"paths": [], "schemas": []}, request=request)

    result = _invoke_registered_tool("list_openapi_paths", handler)

    assert result["ok"] is True
    assert result["data"] == {"paths": [], "schemas": []}
    assert result["meta"]["marivo_path"] == "/openapi/index"


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
    assert result["meta"]["marivo_path"] == "/openapi/fragment"


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


def test_get_openapi_path_fragment_encodes_raw_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/paths/L3Nlc3Npb25z"
        assert request.url.params.get_list("expand") == ["schemas"]
        assert request.url.params.get("depth") == "1"
        return httpx.Response(200, json={"path": "/sessions"}, request=request)

    result = _invoke_registered_tool(
        "get_openapi_path_fragment",
        handler,
        path="/sessions",
        expand=["schemas"],
    )

    assert result["ok"] is True
    assert result["data"] == {"path": "/sessions"}


def test_get_openapi_path_fragment_preserves_http_400_for_invalid_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"detail": "Invalid encoded path. Decoded OpenAPI paths must start with '/'."},
            request=request,
        )

    result = _invoke_registered_tool(
        "get_openapi_path_fragment",
        handler,
        path="sessions",
    )

    assert result["ok"] is False
    assert result["status_code"] == 400
    assert result["error"]["message"] == (
        "Invalid encoded path. Decoded OpenAPI paths must start with '/'."
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


# ------------------------------------------------------------------
# Session & Intent tests
# ------------------------------------------------------------------


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
    assert result["meta"]["marivo_path"] == "/sessions"


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


def test_terminate_session_uses_canonical_lifecycle_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/terminate"
        assert request.read() == b'{"terminal_reason":"answered"}'
        return httpx.Response(
            200,
            json={"session_id": "sess_123", "lifecycle": {"status": "closed"}},
            request=request,
        )

    result = _invoke_registered_tool(
        "terminate_session",
        handler,
        session_id="sess_123",
        terminal_reason="answered",
    )

    assert result["ok"] is True
    assert result["data"] == {"session_id": "sess_123", "lifecycle": {"status": "closed"}}
    assert result["meta"]["marivo_path"] == "/sessions/sess_123/terminate"


def test_terminate_session_uses_default_terminal_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_default/terminate"
        assert request.read() == b'{"terminal_reason":"user_closed"}'
        return httpx.Response(200, json={"session_id": "sess_default"}, request=request)

    result = _invoke_registered_tool(
        "terminate_session",
        handler,
        session_id="sess_default",
    )

    assert result["ok"] is True
    assert result["data"] == {"session_id": "sess_default"}


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
    assert result["meta"]["marivo_path"] == "/sessions/sess_123/state"


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
    assert result["meta"]["marivo_path"] == "/sessions/sess_123/state/query"


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
            b'{"metric":"metric.watch_time","result_mode":"standard","time_scope":{"kind":"range",'
            b'"start":"2025-03-01","end":"2025-03-08"},"scope":{"constraints":'
            b'{"dimension.country":"US"}},"dimensions":["dimension.device_type"]}'
        )
        return httpx.Response(200, json={"artifact_id": "obs_123"}, request=request)

    result = _invoke_registered_tool(
        "observe",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={"kind": "range", "start": "2025-03-01", "end": "2025-03-08"},
        scope={"constraints": {"dimension.country": "US"}},
        dimensions=["dimension.device_type"],
    )

    assert result["ok"] is True
    assert result["data"] == {"artifact_id": "obs_123"}
    assert result["meta"]["marivo_path"] == "/sessions/sess_123/intents/observe"


def test_observe_forwards_calendar_policy_ref_in_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/observe"
        assert request.read() == (
            b'{"metric":"metric.watch_time","result_mode":"standard","time_scope":{"kind":"range",'
            b'"start":"2025-10-01","end":"2025-10-08"},"calendar_policy_ref":'
            b'"calendar_policy.calendar_yoy"}'
        )
        return httpx.Response(200, json={"artifact_id": "obs_456"}, request=request)

    result = _invoke_registered_tool(
        "observe",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={"kind": "range", "start": "2025-10-01", "end": "2025-10-08"},
        calendar_policy_ref="calendar_policy.calendar_yoy",
    )

    assert result["ok"] is True
    assert result["data"] == {"artifact_id": "obs_456"}


def test_compare_uses_path_discriminated_contract_and_default_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/compare"
        assert request.read() == (
            b'{"left_ref":{"step_id":"obs_left","step_type":"observe"},'
            b'"right_ref":{"step_id":"obs_right","step_type":"observe"},'
            b'"mode":"auto"}'
        )
        return httpx.Response(200, json={"artifact_id": "cmp_123"}, request=request)

    result = _invoke_registered_tool(
        "compare",
        handler,
        session_id="sess_123",
        left_ref={"step_id": "obs_left", "step_type": "observe"},
        right_ref={"step_id": "obs_right", "step_type": "observe"},
    )

    assert result["ok"] is True
    assert result["data"] == {"artifact_id": "cmp_123"}


def test_decompose_uses_compare_ref_and_default_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/sess_123/intents/decompose"
        assert request.read() == (
            b'{"compare_ref":{"step_id":"cmp_123","step_type":"compare"},'
            b'"dimension":"dimension.country","method":"delta_share"}'
        )
        return httpx.Response(200, json={"artifact_id": "decomp_123"}, request=request)

    result = _invoke_registered_tool(
        "decompose",
        handler,
        session_id="sess_123",
        compare_ref={"step_id": "cmp_123", "step_type": "compare"},
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
            b'{"metric":"metric.watch_time","time_scope":{"kind":"range",'
            b'"start":"2025-03-01","end":"2025-03-08"},"granularity":"day",'
            b'"profile":"auto","sensitivity":"balanced"}'
        )
        return httpx.Response(200, json={"artifact_id": "detect_123"}, request=request)

    result = _invoke_registered_tool(
        "detect",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        time_scope={"kind": "range", "start": "2025-03-01", "end": "2025-03-08"},
        granularity="day",
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
            b'{"mode":"auto_detect","metric":"metric.watch_time","time_scope":{"kind":"range",'
            b'"start":"2025-03-01","end":"2025-03-08"},"granularity":"day",'
            b'"candidate_dimensions":["dimension.country"],"profile":"auto",'
            b'"sensitivity":"balanced","followup_limit":3,"decomposition_limit":5,'
            b'"baseline_policy":"previous_adjacent_equal_length"}'
        )
        return httpx.Response(200, json={"artifact_id": "diagnose_123"}, request=request)

    result = _invoke_registered_tool(
        "diagnose",
        handler,
        session_id="sess_123",
        metric="metric.watch_time",
        candidate_dimensions=["dimension.country"],
        time_scope={"kind": "range", "start": "2025-03-01", "end": "2025-03-08"},
        granularity="day",
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


def test_observe_rejects_json_string_time_scope_before_http_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP handler should not be called for invalid MCP input")

    with pytest.raises(ValueError, match=r"observe\.time_scope requires canonical object shape"):
        _invoke_registered_tool(
            "observe",
            handler,
            session_id="sess_123",
            metric="metric.watch_time",
            time_scope='{"kind":"range","start":"2025-03-01","end":"2025-03-08"}',
        )


def test_compare_rejects_json_string_ref_before_http_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP handler should not be called for invalid MCP input")

    with pytest.raises(ValueError, match="Pass a structured object, not a JSON-encoded string"):
        _invoke_registered_tool(
            "compare",
            handler,
            session_id="sess_123",
            left_ref='{"artifact_id":"obs_left","step_type":"observe"}',
            right_ref={"artifact_id": "obs_right", "step_type": "observe"},
        )


def test_compare_rejects_missing_step_id_before_http_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP handler should not be called for invalid MCP input")

    with pytest.raises(ValueError) as exc_info:
        _invoke_registered_tool(
            "compare",
            handler,
            session_id="sess_123",
            left_ref={"step_type": "observe"},
            right_ref={"step_id": "obs_right", "step_type": "observe"},
        )

    message = str(exc_info.value)
    assert "left_ref:" in message
    assert "step_id" in message


def test_detect_rejects_missing_range_end_before_http_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP handler should not be called for invalid MCP input")

    with pytest.raises(ValueError) as exc_info:
        _invoke_registered_tool(
            "detect",
            handler,
            session_id="sess_123",
            metric="metric.watch_time",
            time_scope={"kind": "range", "start": "2025-03-01"},
            granularity="day",
        )

    message = str(exc_info.value)
    assert "time_scope:" in message
    assert "end" in message


def test_intent_tools_preserve_422_guidance_from_marivo() -> None:
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
        time_scope={"kind": "range", "start": "2025-03-01"},
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


# ------------------------------------------------------------------
# V2 Semantic Model tests
# ------------------------------------------------------------------


def test_create_semantic_model_uses_v2_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/semantic-models"
        body = request.read()
        assert b'"name":"analytics_model"' in body
        return httpx.Response(200, json={"name": "analytics_model"}, request=request)

    result = _invoke_registered_tool(
        "create_semantic_model",
        handler,
        payload={"name": "analytics_model"},
    )

    assert result["ok"] is True
    assert result["meta"]["marivo_path"] == "/semantic-models"


def test_list_semantic_models_uses_v2_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/semantic-models"
        return httpx.Response(200, json=[{"name": "model_123"}], request=request)

    result = _invoke_registered_tool("list_semantic_models", handler)

    assert result["ok"] is True
    assert result["data"] == [{"name": "model_123"}]


def test_get_semantic_model_uses_v2_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/semantic-models/analytics"
        return httpx.Response(200, json={"name": "analytics"}, request=request)

    result = _invoke_registered_tool("get_semantic_model", handler, model="analytics")

    assert result["ok"] is True
    assert result["data"] == {"name": "analytics"}


def test_delete_semantic_model_uses_v2_delete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/semantic-models/old_model"
        return httpx.Response(204, request=request)

    result = _invoke_registered_tool("delete_semantic_model", handler, model="old_model")

    assert result["ok"] is True


def test_v2_dataset_crud_uses_model_scoped_paths() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/semantic-models/analytics/datasets")
        return httpx.Response(200, json={"name": "events"}, request=request)

    result = _invoke_registered_tool(
        "create_dataset",
        handler,
        model="analytics",
        payload={"name": "events"},
    )

    assert result["ok"] is True


def test_v2_metric_crud_uses_model_scoped_paths() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/semantic-models/analytics/metrics/watch_time"
        return httpx.Response(200, json={"name": "watch_time"}, request=request)

    result = _invoke_registered_tool(
        "get_metric",
        handler,
        model="analytics",
        name="watch_time",
    )

    assert result["ok"] is True
    assert result["meta"]["marivo_path"] == "/semantic-models/analytics/metrics/watch_time"


# ------------------------------------------------------------------
# Datasource tests (fixed)
# ------------------------------------------------------------------


def test_list_datasources_uses_canonical_datasources_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/datasources"
        assert request.url.query == b""
        return httpx.Response(200, json=[{"datasource_id": "ds_123"}], request=request)

    result = _invoke_registered_tool("list_datasources", handler)

    assert result["ok"] is True
    assert result["data"] == [{"datasource_id": "ds_123"}]
    assert result["meta"]["marivo_path"] == "/datasources"


def test_create_datasource_uses_policy_field() -> None:
    tool_kwargs = {
        "datasource_type": "duckdb",
        "display_name": "Analytics DuckDB",
        "connection": {"db_path": "/data/analytics.duckdb"},
        "policy": {"allow_live_browse": True},
    }
    expected_body = httpx.Request("POST", "http://marivo.test", json=tool_kwargs).read()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/datasources"
        assert request.read() == expected_body
        return httpx.Response(200, json={"datasource_id": "ds_123"}, request=request)

    result = _invoke_registered_tool("create_datasource", handler, **tool_kwargs)

    assert result["ok"] is True
    assert result["data"] == {"datasource_id": "ds_123"}
    assert result["meta"]["marivo_path"] == "/datasources"


def test_create_datasource_omits_none_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/datasources"
        assert request.read() == (
            httpx.Request(
                "POST",
                "http://marivo.test",
                json={"datasource_type": "trino", "display_name": "Warehouse"},
            ).read()
        )
        return httpx.Response(200, json={"datasource_id": "ds_456"}, request=request)

    result = _invoke_registered_tool(
        "create_datasource",
        handler,
        datasource_type="trino",
        display_name="Warehouse",
    )

    assert result["ok"] is True
    assert result["data"] == {"datasource_id": "ds_456"}


def test_browse_columns_uses_live_columns_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/datasources/ds_123/browse/columns"
        assert dict(request.url.params) == {
            "schema_name": "events",
            "table_name": "watch_events",
        }
        return httpx.Response(
            200,
            json=[{"name": "event_id", "schema_name": "events", "table_name": "watch_events"}],
            request=request,
        )

    result = _invoke_registered_tool(
        "browse_columns",
        handler,
        datasource_id="ds_123",
        schema_name="events",
        table_name="watch_events",
    )

    assert result["ok"] is True
    assert result["data"] == [
        {"name": "event_id", "schema_name": "events", "table_name": "watch_events"}
    ]
    assert result["meta"]["marivo_path"] == "/datasources/ds_123/browse/columns"


def test_browse_tables_uses_schema_name_query_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/datasources/ds_123/browse/tables"
        assert request.url.params.get("schema_name") == "events"
        return httpx.Response(200, json=[{"table_name": "watch_events"}], request=request)

    result = _invoke_registered_tool(
        "browse_tables",
        handler,
        datasource_id="ds_123",
        schema_name="events",
    )

    assert result["ok"] is True


def test_preview_table_uses_get_and_catalog_preview_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/datasources/ds_123/catalog/preview"
        assert request.url.params.get("schema") == "events"
        assert request.url.params.get("table") == "watch_events"
        assert request.url.params.get("limit") == "50"
        return httpx.Response(200, json={"rows": [{"id": 1}]}, request=request)

    result = _invoke_registered_tool(
        "preview_table",
        handler,
        datasource_id="ds_123",
        schema="events",
        table="watch_events",
        limit=50,
    )

    assert result["ok"] is True
    assert result["meta"]["marivo_path"] == "/datasources/ds_123/catalog/preview"


# ------------------------------------------------------------------
# Governance tests
# ------------------------------------------------------------------


def test_create_policy_sends_canonical_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/policies"
        body = request.read()
        assert b'"name":"agg_only"' in body
        assert b'"policy_type":"aggregate_only"' in body
        return httpx.Response(200, json={"policy_id": "pol_123"}, request=request)

    result = _invoke_registered_tool(
        "create_policy",
        handler,
        name="agg_only",
        policy_type="aggregate_only",
        definition={"min_group_size": 10},
    )

    assert result["ok"] is True
    assert result["meta"]["marivo_path"] == "/policies"


def _invoke_registered_tool(
    tool_name: str,
    handler: Any,
    /,
    **tool_kwargs: Any,
) -> dict[str, Any]:
    tools_module_any = cast("Any", tools_module)
    original_client = tools_module_any.MarivoHttpClient

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    openapi_cache = tool_kwargs.pop("_openapi_cache", None)
    tools_module_any.MarivoHttpClient = build_client
    try:
        server = cast("Any", _FakeServer())
        if openapi_cache is None:
            register_tools(server, _build_config())
        else:
            register_tools(server, _build_config(), openapi_cache=openapi_cache)
        return cast("dict[str, Any]", server.tools[tool_name](**tool_kwargs))
    finally:
        tools_module_any.MarivoHttpClient = original_client
