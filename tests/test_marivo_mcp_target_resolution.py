from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

config_module = import_module("marivo_mcp.config")
target_resolution_module = import_module("marivo_mcp.target_resolution")
server_module = import_module("marivo_mcp.server")

HttpTransportConfig = config_module.HttpTransportConfig
MarivoMcpConfig = config_module.MarivoMcpConfig
TargetResolutionError = config_module.TargetResolutionError
inspect_runtime_manifest = target_resolution_module.inspect_runtime_manifest
resolve_target = target_resolution_module.resolve_target


class _FakeServerSettings:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 8000
        self.streamable_http_path = "/mcp"


class _FakeServer:
    def __init__(
        self,
        _name: str,
        *,
        stateless_http: bool,
        json_response: bool,
        streamable_http_path: str,
    ) -> None:
        self.settings = _FakeServerSettings()
        self.tools: dict[str, object] = {}
        self.resources: dict[str, object] = {}

    def tool(self) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, uri: str) -> Any:
        def decorator(func: Any) -> Any:
            self.resources[uri] = func
            return func

        return decorator

    def run(self, transport: str | None = None) -> None:
        raise AssertionError(f"Unexpected run({transport!r}) during unit tests")


def _build_config(**overrides: Any) -> MarivoMcpConfig:
    values: dict[str, Any] = {
        "mode": "auto",
        "base_url": None,
        "api_token": None,
        "workspace_root": None,
        "local_host": "127.0.0.1",
        "local_port": 0,
        "start_timeout_ms": 15000,
        "healthcheck_timeout_ms": 2000,
        "timeout_ms": 1500,
        "openapi_cache_ttl_sec": 300,
        "default_source_id": None,
        "transport": "stdio",
        "http": HttpTransportConfig(),
    }
    values.update(overrides)
    return MarivoMcpConfig(**values)


def _write_manifest(root_path: Path, **overrides: Any) -> Path:
    dot_marivo = root_path / ".marivo"
    dot_marivo.mkdir(exist_ok=True)
    data: dict[str, Any] = {
        "version": "0.1.0",
        "workspace_root": str(root_path),
        "mode": "local",
        "base_url": "http://127.0.0.1:49152",
        "host": "127.0.0.1",
        "port": 49152,
        "pid": 12345,
        "started_at": "2026-04-25T00:00:00Z",
        "config_path": str(dot_marivo / "marivo.yaml"),
        "metadata_path": str(dot_marivo / "metadata.sqlite"),
    }
    data.update(overrides)
    manifest_path = dot_marivo / "runtime.json"
    manifest_path.write_text(json.dumps(data) + "\n")
    return manifest_path


def test_remote_mode_requires_base_url() -> None:
    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(_build_config(mode="remote"))

    assert exc_info.value.code == "remote_target_required"
    assert exc_info.value.detail == {}


def test_remote_mode_resolves_health_checked_base_url() -> None:
    calls: list[tuple[str, int, str | None]] = []

    def health_checker(base_url: str, timeout_ms: int, api_token: str | None) -> bool:
        calls.append((base_url, timeout_ms, api_token))
        return True

    resolution = resolve_target(
        _build_config(
            mode="remote",
            base_url="http://marivo.test",
            api_token="secret-token",
            workspace_root="/does/not/matter",
        ),
        health_checker=health_checker,
    )

    assert resolution.target_kind == "remote"
    assert resolution.base_url == "http://marivo.test"
    assert resolution.config.api_token == "secret-token"
    assert calls == [("http://marivo.test", 2000, "secret-token")]


def test_remote_mode_unreachable_does_not_fall_back_to_workspace() -> None:
    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="auto", base_url="http://marivo.test"),
            cwd="/not/a/workspace",
            health_checker=lambda _base_url, _timeout_ms, _api_token: False,
        )

    error = exc_info.value
    assert error.code == "remote_target_unreachable"
    assert error.detail["base_url"] == "http://marivo.test"


def test_stdio_local_server_defers_target_resolution_until_request(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARIVO_MODE", "local")
    monkeypatch.delenv("MARIVO_BASE_URL", raising=False)
    monkeypatch.setenv("MARIVO_MCP_TRANSPORT", "stdio")
    monkeypatch.setattr(server_module, "load_fastmcp", lambda: _FakeServer)

    def fail_resolve_target(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("resolve_target should not run during stdio server startup")

    monkeypatch.setattr(server_module, "resolve_target", fail_resolve_target)

    server = server_module.build_server()

    assert isinstance(server, _FakeServer)
    assert "health_check" in server.tools


def test_local_mode_resolves_workspace_from_roots_and_reuses_healthy_manifest(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = _write_manifest(workspace_root)

    resolution = resolve_target(
        _build_config(mode="local", base_url="http://ignored.test", api_token="ignored"),
        workspace_roots=[f"file://{workspace_root}"],
        cwd="/not/a/workspace",
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: True,
    )

    assert resolution.target_kind == "local"
    assert resolution.base_url == "http://127.0.0.1:49152"
    assert resolution.workspace_root == str(workspace_root)
    assert resolution.manifest_path == manifest_path
    assert resolution.runtime_state == "manifest_valid_healthy"
    assert resolution.config.base_url == "http://127.0.0.1:49152"
    assert resolution.config.api_token is None


def test_auto_without_base_url_uses_cwd_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_manifest(workspace_root)

    resolution = resolve_target(
        _build_config(mode="auto"),
        cwd=str(workspace_root),
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: True,
    )

    assert resolution.target_kind == "local"
    assert resolution.workspace_root == str(workspace_root)


def test_local_mode_requires_valid_workspace_root(tmp_path: Path) -> None:
    invalid_file = tmp_path / "not-a-dir"
    invalid_file.write_text("")

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="local", workspace_root=str(invalid_file)),
            workspace_roots=[],
            cwd="relative/path",
        )

    error = exc_info.value
    assert error.code == "workspace_root_required"
    assert error.detail == {"tried_sources": ["MARIVO_WORKSPACE_ROOT", "mcp_roots", "cwd"]}


def test_manifest_missing_starts_local_runtime(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    commands: list[list[str]] = []

    status = inspect_runtime_manifest(
        str(workspace_root),
        healthcheck_timeout_ms=2000,
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: True,
    )

    assert status.state == "no_manifest"

    def command_runner(args: list[str], timeout_ms: int) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        assert timeout_ms == 15000
        _write_manifest(workspace_root)
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    resolution = resolve_target(
        _build_config(mode="local", workspace_root=str(workspace_root)),
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: True,
        command_runner=command_runner,
    )

    assert resolution.target_kind == "local"
    assert resolution.runtime_state == "manifest_valid_healthy"
    assert commands == [
        [
            "serve-local",
            "--workspace-root",
            str(workspace_root),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--start-timeout-ms",
            "15000",
            "--format",
            "json",
        ]
    ]


def test_manifest_unhealthy_runtime_is_restarted(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = _write_manifest(workspace_root)
    commands: list[list[str]] = []
    health_results = iter([False, True])

    def health_checker(_base_url: str, _timeout_ms: int, _api_token: str | None) -> bool:
        return next(health_results)

    def command_runner(args: list[str], timeout_ms: int) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:2] == ["runtime", "stop"]:
            manifest_path.unlink()
        elif args[0] == "serve-local":
            _write_manifest(workspace_root)
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    resolution = resolve_target(
        _build_config(mode="local", workspace_root=str(workspace_root)),
        health_checker=health_checker,
        pid_checker=lambda _pid: True,
        command_runner=command_runner,
    )

    assert resolution.target_kind == "local"
    assert resolution.runtime_state == "manifest_valid_healthy"
    assert commands == [
        [
            "runtime",
            "stop",
            "--workspace-root",
            str(workspace_root),
            "--format",
            "json",
        ],
        [
            "serve-local",
            "--workspace-root",
            str(workspace_root),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--start-timeout-ms",
            "15000",
            "--format",
            "json",
        ],
    ]


def test_invalid_manifest_raises_runtime_manifest_invalid(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = workspace_root / ".marivo" / "runtime.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text("{not json")

    with pytest.raises(TargetResolutionError) as exc_info:
        inspect_runtime_manifest(str(workspace_root), healthcheck_timeout_ms=2000)

    error = exc_info.value
    assert error.code == "runtime_manifest_invalid"
    assert error.detail["manifest_path"] == str(manifest_path)
    assert error.detail["missing_fields"] is None


def test_manifest_missing_required_fields_reports_missing_fields(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = _write_manifest(workspace_root)
    manifest_path.write_text(json.dumps({"version": "0.1.0"}) + "\n")

    with pytest.raises(TargetResolutionError) as exc_info:
        inspect_runtime_manifest(str(workspace_root), healthcheck_timeout_ms=2000)

    error = exc_info.value
    assert error.code == "runtime_manifest_invalid"
    assert "workspace_root" in error.detail["missing_fields"]


def test_manifest_base_url_invariant_is_enforced(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_manifest(workspace_root, base_url="http://127.0.0.1:9999")

    with pytest.raises(TargetResolutionError) as exc_info:
        inspect_runtime_manifest(str(workspace_root), healthcheck_timeout_ms=2000)

    assert exc_info.value.code == "runtime_manifest_invalid"
    assert "base_url invariant violation" in exc_info.value.detail["parse_error"]


def test_manifest_workspace_root_must_match_manifest_location(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    _write_manifest(
        workspace_a,
        workspace_root=str(workspace_b),
        config_path=str(workspace_b / ".marivo" / "marivo.yaml"),
        metadata_path=str(workspace_b / ".marivo" / "metadata.sqlite"),
    )

    with pytest.raises(TargetResolutionError) as exc_info:
        inspect_runtime_manifest(str(workspace_a), healthcheck_timeout_ms=2000)

    assert exc_info.value.code == "runtime_manifest_invalid"
    assert "workspace_root does not match manifest location" in exc_info.value.detail["parse_error"]


def test_manifest_pid_dead_is_stale(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_manifest(workspace_root)

    status = inspect_runtime_manifest(
        str(workspace_root),
        healthcheck_timeout_ms=2000,
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: False,
    )

    assert status.state == "manifest_stale_pid_dead"


def test_manifest_health_failure_is_stale_unhealthy(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_manifest(workspace_root)

    status = inspect_runtime_manifest(
        str(workspace_root),
        healthcheck_timeout_ms=2000,
        health_checker=lambda _base_url, _timeout_ms, _api_token: False,
        pid_checker=lambda _pid: True,
    )

    assert status.state == "manifest_stale_unhealthy"
