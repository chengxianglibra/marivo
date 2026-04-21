from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import httpx

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

config_module = import_module("marivo_mcp.config")
http_client_module = import_module("marivo_mcp.http_client")
smoke_module = import_module("marivo_mcp.smoke")

MarivoMcpConfig = config_module.MarivoMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
MarivoHttpClient = http_client_module.MarivoHttpClient
run_live_smoke = smoke_module.run_live_smoke
summarize_results = smoke_module.summarize_results


def _build_config() -> Any:
    return MarivoMcpConfig(
        base_url="http://marivo.test",
        api_token=None,
        timeout_ms=1500,
        openapi_cache_ttl_sec=300,
        default_source_id=None,
        transport="stdio",
        http=HttpTransportConfig(),
    )


def test_live_smoke_covers_health_openapi_session_lifecycle_state_and_validation() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if request.url.path == "/openapi/index":
            return httpx.Response(200, json={"revision": "rev_123", "paths": []}, request=request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"session_id": "sess_123"}, request=request)
        if request.url.path == "/sessions/sess_123/terminate":
            return httpx.Response(
                200,
                json={"session_id": "sess_123", "lifecycle": {"status": "closed"}},
                request=request,
            )
        if request.url.path == "/sessions/sess_123/state":
            return httpx.Response(
                200,
                json={"schema_version": "session_state_view.v1", "items": []},
                request=request,
            )
        if request.url.path == "/semantic/entities":
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
                    "guidance": {"schema_url": "/openapi/schemas/TypedEntityCreateRequest?depth=2"},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

    smoke_module_any = cast("Any", smoke_module)
    original_client = smoke_module_any.MarivoHttpClient

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    smoke_module_any.MarivoHttpClient = build_client
    try:
        results = run_live_smoke(_build_config())
    finally:
        smoke_module_any.MarivoHttpClient = original_client

    assert [(result.name, result.ok) for result in results] == [
        ("health_check", True),
        ("list_openapi_paths", True),
        ("create_session", True),
        ("terminate_session", True),
        ("get_session_state", True),
        ("validation_envelope", True),
    ]
    assert requests == [
        ("GET", "/health"),
        ("GET", "/openapi/index"),
        ("POST", "/sessions"),
        ("POST", "/sessions/sess_123/terminate"),
        ("GET", "/sessions/sess_123/state"),
        ("POST", "/semantic/entities"),
    ]
    assert summarize_results(results)["ok"] is True


def test_live_smoke_marks_missing_session_id_as_failed_followup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if request.url.path == "/openapi/index":
            return httpx.Response(200, json={"revision": "rev_123", "paths": []}, request=request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"status": "created"}, request=request)
        if request.url.path == "/semantic/entities":
            return httpx.Response(
                422,
                json={"detail": [{"loc": ["body"], "msg": "invalid", "type": "value_error"}]},
                request=request,
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

    smoke_module_any = cast("Any", smoke_module)
    original_client = smoke_module_any.MarivoHttpClient

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    smoke_module_any.MarivoHttpClient = build_client
    try:
        results = run_live_smoke(_build_config())
    finally:
        smoke_module_any.MarivoHttpClient = original_client

    summary = summarize_results(results)
    assert summary["ok"] is False
    assert "get_session_state" in summary["failed_checks"]
