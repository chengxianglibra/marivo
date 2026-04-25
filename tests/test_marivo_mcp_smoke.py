from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

config_module = import_module("marivo_mcp.config")
http_client_module = import_module("marivo_mcp.http_client")
smoke_module = import_module("marivo_mcp.smoke")

MarivoMcpConfig = config_module.MarivoMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
MarivoHttpClient = http_client_module.MarivoHttpClient
TargetResolutionError = config_module.TargetResolutionError
run_live_smoke = smoke_module.run_live_smoke
summarize_results = smoke_module.summarize_results


def _build_config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "base_url": "http://marivo.test",
        "api_token": None,
        "timeout_ms": 1500,
        "openapi_cache_ttl_sec": 300,
        "default_source_id": None,
        "transport": "stdio",
        "http": HttpTransportConfig(),
    }
    values.update(overrides)
    return MarivoMcpConfig(
        **values,
    )


def _build_resolution(
    config: Any,
    *,
    target_kind: str = "remote",
    base_url: str = "http://marivo.test",
    workspace_root: str | None = None,
    manifest_path: str | None = None,
    runtime_state: str = "remote",
) -> Any:
    return SimpleNamespace(
        target_kind=target_kind,
        base_url=base_url,
        config=config.model_copy(update={"base_url": base_url}),
        workspace_root=workspace_root,
        manifest_path=None if manifest_path is None else Path(manifest_path),
        runtime_state=runtime_state,
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
    original_resolve_target = smoke_module_any.resolve_target

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    smoke_module_any.MarivoHttpClient = build_client
    smoke_module_any.resolve_target = lambda config: _build_resolution(config)
    try:
        result = run_live_smoke(_build_config(mode="remote"))
    finally:
        smoke_module_any.MarivoHttpClient = original_client
        smoke_module_any.resolve_target = original_resolve_target

    assert result.target_kind == "remote"
    assert result.base_url == "http://marivo.test"
    assert [(check.name, check.ok) for check in result.checks] == [
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
    summary = summarize_results(result)
    assert summary["ok"] is True
    assert summary["target_kind"] == "remote"
    assert summary["base_url"] == "http://marivo.test"


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
    original_resolve_target = smoke_module_any.resolve_target

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    smoke_module_any.MarivoHttpClient = build_client
    smoke_module_any.resolve_target = lambda config: _build_resolution(config)
    try:
        result = run_live_smoke(_build_config(mode="remote"))
    finally:
        smoke_module_any.MarivoHttpClient = original_client
        smoke_module_any.resolve_target = original_resolve_target

    summary = summarize_results(result)
    assert summary["ok"] is False
    assert "get_session_state" in summary["failed_checks"]


def test_live_smoke_reports_local_auto_managed_target_metadata() -> None:
    workspace_root = "/workspace/project"
    manifest_path = "/workspace/project/.marivo/runtime.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if request.url.path == "/openapi/index":
            return httpx.Response(200, json={"revision": "rev_123", "paths": []}, request=request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"session_id": "sess_123"}, request=request)
        if request.url.path == "/sessions/sess_123/terminate":
            return httpx.Response(200, json={"session_id": "sess_123"}, request=request)
        if request.url.path == "/sessions/sess_123/state":
            return httpx.Response(200, json={"items": []}, request=request)
        if request.url.path == "/semantic/entities":
            return httpx.Response(
                422,
                json={"detail": [{"loc": ["body"], "msg": "invalid", "type": "value_error"}]},
                request=request,
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

    smoke_module_any = cast("Any", smoke_module)
    original_client = smoke_module_any.MarivoHttpClient
    original_resolve_target = smoke_module_any.resolve_target

    def build_client(config: Any) -> Any:
        return MarivoHttpClient(config, transport=httpx.MockTransport(handler))

    def resolve_local(config: Any) -> Any:
        return _build_resolution(
            config,
            target_kind="local",
            base_url="http://127.0.0.1:48231",
            workspace_root=workspace_root,
            manifest_path=manifest_path,
            runtime_state="manifest_valid_healthy",
        )

    smoke_module_any.MarivoHttpClient = build_client
    smoke_module_any.resolve_target = resolve_local
    try:
        result = run_live_smoke(_build_config(mode="local", base_url=None))
    finally:
        smoke_module_any.MarivoHttpClient = original_client
        smoke_module_any.resolve_target = original_resolve_target

    summary = summarize_results(result)
    assert summary["ok"] is True
    assert summary["target_kind"] == "local"
    assert summary["base_url"] == "http://127.0.0.1:48231"
    assert summary["workspace_root"] == workspace_root
    assert summary["manifest_path"] == manifest_path
    assert summary["runtime_state"] == "manifest_valid_healthy"


def test_smoke_cli_reports_target_resolution_error_as_json(
    capsys: Any,
) -> None:
    smoke_module_any = cast("Any", smoke_module)
    original_load_config_from_env = smoke_module_any.load_config_from_env
    original_resolve_target = smoke_module_any.resolve_target

    def fail_resolve_target(_config: Any) -> Any:
        raise TargetResolutionError(
            code="remote_target_unreachable",
            message="无法连接到远程 Marivo 服务：http://marivo.test",
            detail={"base_url": "http://marivo.test"},
            guidance="请检查地址是否正确、服务是否运行",
        )

    smoke_module_any.load_config_from_env = lambda: _build_config(mode="remote")
    smoke_module_any.resolve_target = fail_resolve_target
    try:
        with pytest.raises(SystemExit) as exc_info:
            smoke_module.main()
    finally:
        smoke_module_any.load_config_from_env = original_load_config_from_env
        smoke_module_any.resolve_target = original_resolve_target

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert json.loads(captured.out) == {
        "ok": False,
        "error": {
            "code": "remote_target_unreachable",
            "message": "无法连接到远程 Marivo 服务：http://marivo.test",
            "detail": {"base_url": "http://marivo.test"},
            "guidance": "请检查地址是否正确、服务是否运行",
        },
    }
