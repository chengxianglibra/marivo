from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

resources_module = import_module("marivo_mcp.resources")
config_module = import_module("marivo_mcp.config")
http_client_module = import_module("marivo_mcp.http_client")

MarivoMcpConfig = config_module.MarivoMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
MarivoHttpClient = http_client_module.MarivoHttpClient
MarivoHttpClientError = http_client_module.MarivoHttpClientError
register_resources = resources_module.register_resources


class _FakeServerSettings:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 8000
        self.streamable_http_path = "/mcp"


class _FakeServer:
    def __init__(self) -> None:
        self.settings = _FakeServerSettings()
        self.resources: dict[str, Any] = {}

    def tool(self) -> Any:
        raise AssertionError("Unexpected tool registration")

    def resource(self, uri: str) -> Any:
        def decorator(func: Any) -> Any:
            self.resources[uri] = func
            return func

        return decorator

    def run(self, transport: str | None = None) -> None:
        raise AssertionError(f"Unexpected run({transport!r}) during unit tests")


def _build_config() -> Any:
    return MarivoMcpConfig(
        base_url="http://marivo.test",
        api_token=None,
        timeout_ms=1500,
        openapi_cache_ttl_sec=300,
        default_datasource_id=None,
        transport="stdio",
        http=HttpTransportConfig(),
    )


def test_registers_resources_and_scaffold_config_resource() -> None:
    server = cast("Any", _FakeServer())
    register_resources(server, _build_config())

    assert set(server.resources) == {
        "marivo://server/config",
        "marivo://sessions/{session_id}/state",
        "marivo://sessions/{session_id}/propositions/{proposition_id}/context",
        "marivo://semantic/{family}",
    }


def test_server_config_resource_exposes_non_secret_runtime_settings() -> None:
    server = cast("Any", _FakeServer())
    register_resources(server, _build_config())

    payload = server.resources["marivo://server/config"]()

    assert "base_url=http://marivo.test" in payload
    assert "openapi_cache_ttl_sec=300" in payload
    assert "default_datasource_id=" in payload


def test_session_state_resource_mirrors_canonical_http_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/sess_123/state"
        return httpx.Response(
            200,
            json={"schema_version": "session_state_view.v1", "items": []},
            request=request,
        )

    result = _invoke_registered_resource(
        "marivo://sessions/{session_id}/state",
        handler,
        session_id="sess_123",
    )

    assert result == {"schema_version": "session_state_view.v1", "items": []}


def test_proposition_context_resource_mirrors_canonical_http_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/sess_123/propositions/prop_456/context"
        return httpx.Response(200, json={"proposition_id": "prop_456"}, request=request)

    result = _invoke_registered_resource(
        "marivo://sessions/{session_id}/propositions/{proposition_id}/context",
        handler,
        session_id="sess_123",
        proposition_id="prop_456",
    )

    assert result == {"proposition_id": "prop_456"}


def test_semantic_family_resource_reads_one_canonical_family_surface() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/semantic-models"
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=[{"name": "model_123"}], request=request)

    result = _invoke_registered_resource(
        "marivo://semantic/{family}",
        handler,
        family="models",
    )

    assert result == [{"name": "model_123"}]


def test_semantic_family_resource_rejects_unknown_families() -> None:
    with pytest.raises(ValueError, match="Unsupported semantic family"):
        _invoke_registered_resource(
            "marivo://semantic/{family}",
            lambda request: httpx.Response(200, json={}, request=request),
            family="keys",
        )


def test_resource_failures_raise_structured_http_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions/sess_missing/state"
        return httpx.Response(
            404,
            json={"detail": "Session 'sess_missing' not found"},
            request=request,
        )

    with pytest.raises(MarivoHttpClientError, match="Session 'sess_missing' not found") as error:
        _invoke_registered_resource(
            "marivo://sessions/{session_id}/state",
            handler,
            session_id="sess_missing",
        )

    assert error.value.status_code == 404
    assert error.value.category == "not_found"
    assert error.value.path == "/sessions/sess_missing/state"


def _invoke_registered_resource(
    uri: str,
    handler: Any,
    /,
    **resource_kwargs: Any,
) -> object:
    resources_module_any = cast("Any", resources_module)
    original_client = resources_module_any.MarivoHttpClient

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    resources_module_any.MarivoHttpClient = build_client
    try:
        server = cast("Any", _FakeServer())
        register_resources(server, _build_config())
        return server.resources[uri](**resource_kwargs)
    finally:
        resources_module_any.MarivoHttpClient = original_client
