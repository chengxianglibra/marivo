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
init_cli_module = import_module("marivo_mcp.init_cli")

HttpTransportConfig = config_module.HttpTransportConfig
MarivoMcpConfig = config_module.MarivoMcpConfig
TargetResolutionError = config_module.TargetResolutionError
inspect_runtime_manifest = target_resolution_module.inspect_runtime_manifest
resolve_target = target_resolution_module.resolve_target
build_init_config = init_cli_module.build_init_config
init_main = init_cli_module.main
render_client_config = init_cli_module.render_client_config
write_client_config = init_cli_module.write_client_config


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


def test_http_entrypoint_forces_streamable_http_transport(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_resolve_target(config: Any) -> Any:
        seen["transport"] = config.transport
        return type("Resolution", (), {"config": config})()

    monkeypatch.setattr(server_module, "load_config_from_env", lambda: _build_config())
    monkeypatch.setattr(server_module, "resolve_target", fake_resolve_target)
    monkeypatch.setattr(
        server_module, "_run_streamable_http", lambda config: seen.update(run=config)
    )

    server_module.main_http()

    assert seen["transport"] == "streamable-http"
    assert seen["run"].transport == "streamable-http"


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


def test_http_auto_with_base_url_uses_remote_without_workspace() -> None:
    resolution = resolve_target(
        _build_config(
            mode="auto",
            base_url="http://marivo.test",
            transport="streamable-http",
        ),
        cwd="/",
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
    )

    assert resolution.target_kind == "remote"
    assert resolution.workspace_root is None


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


def test_http_local_requires_explicit_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="local", transport="streamable-http"),
            cwd=str(workspace_root),
        )

    error = exc_info.value
    assert error.code == "workspace_root_required"
    assert error.detail == {
        "tried_sources": ["MARIVO_WORKSPACE_ROOT"],
        "transport": "streamable-http",
    }


def test_http_local_guard_rejects_system_workspace_root() -> None:
    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(
                mode="local",
                transport="streamable-http",
                workspace_root="/",
            ),
            cwd="/not/used",
        )

    error = exc_info.value
    assert error.code == "workspace_root_required"
    assert error.detail["reason"] == "system_workspace_root"
    assert error.detail["workspace_root"] == "/"


def test_http_local_guard_rejects_missing_serve_local(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setattr(target_resolution_module.shutil, "which", lambda _name: None)

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(
                mode="local",
                transport="streamable-http",
                workspace_root=str(workspace_root),
            ),
        )

    error = exc_info.value
    assert error.code == "local_runtime_start_failed"
    assert error.detail["reason"] == "serve_local_not_found"
    assert error.detail["workspace_root"] == str(workspace_root)


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


def test_serve_local_workspace_exit_maps_to_workspace_required(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def command_runner(args: list[str], timeout_ms: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="")

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="local", workspace_root=str(workspace_root)),
            health_checker=lambda _base_url, _timeout_ms, _api_token: True,
            pid_checker=lambda _pid: True,
            command_runner=command_runner,
        )

    assert exc_info.value.code == "workspace_root_required"


def test_serve_local_failure_reports_structured_start_failure(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def command_runner(args: list[str], timeout_ms: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 6, stdout="", stderr="")

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="local", workspace_root=str(workspace_root)),
            health_checker=lambda _base_url, _timeout_ms, _api_token: True,
            pid_checker=lambda _pid: True,
            command_runner=command_runner,
        )

    error = exc_info.value
    assert error.code == "local_runtime_start_failed"
    assert error.detail == {
        "workspace_root": str(workspace_root),
        "timeout_ms": 15000,
        "exit_code": 6,
        "health_checked": False,
    }


def test_serve_local_timeout_reports_health_checked_start_failure(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def command_runner(args: list[str], timeout_ms: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout_ms / 1000)

    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(mode="local", workspace_root=str(workspace_root)),
            health_checker=lambda _base_url, _timeout_ms, _api_token: True,
            pid_checker=lambda _pid: True,
            command_runner=command_runner,
        )

    error = exc_info.value
    assert error.code == "local_runtime_start_failed"
    assert error.detail["exit_code"] is None
    assert error.detail["health_checked"] is True


def test_start_timeout_must_exceed_healthcheck_timeout() -> None:
    with pytest.raises(TargetResolutionError) as exc_info:
        resolve_target(
            _build_config(
                mode="remote",
                base_url="http://marivo.test",
                start_timeout_ms=2000,
                healthcheck_timeout_ms=2000,
            ),
            health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        )

    assert exc_info.value.code == "config_invalid"
    assert exc_info.value.detail == {
        "start_timeout_ms": 2000,
        "healthcheck_timeout_ms": 2000,
    }


def test_local_mode_ignored_remote_fields_emit_warnings(tmp_path: Path, capsys: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_manifest(workspace_root)

    resolve_target(
        _build_config(
            mode="local",
            base_url="http://ignored.test",
            api_token="ignored-token",
            workspace_root=str(workspace_root),
        ),
        health_checker=lambda _base_url, _timeout_ms, _api_token: True,
        pid_checker=lambda _pid: True,
    )

    captured = capsys.readouterr()
    assert "MARIVO_BASE_URL is set but mode=local" in captured.err
    assert "MARIVO_API_TOKEN is set but mode=local" in captured.err


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


def test_mcp_init_remote_print_config() -> None:
    output = build_init_config(
        mode="remote",
        base_url="http://marivo.test",
        workspace_root=None,
        client="generic",
    )

    assert output["target_kind"] == "remote"
    assert output["mcpServers"] == {
        "marivo": {
            "command": "marivo-mcp",
            "env": {
                "MARIVO_MODE": "remote",
                "MARIVO_BASE_URL": "http://marivo.test",
            },
        }
    }
    assert output["mcp_server"] == {
        "command": "marivo-mcp",
        "env": {
            "MARIVO_MODE": "remote",
            "MARIVO_BASE_URL": "http://marivo.test",
        },
    }
    assert json.loads(render_client_config(output, client="generic")) == {
        "mcpServers": output["mcpServers"]
    }


def test_mcp_init_http_remote_prints_client_url_and_server_env() -> None:
    output = build_init_config(
        mode="remote",
        base_url="http://marivo.test",
        api_token="secret-token",
        workspace_root=None,
        client="generic",
        transport="streamable-http",
        http_host="0.0.0.0",
        http_port=9000,
        http_path="mcp",
    )

    assert output["target_kind"] == "remote"
    assert output["transport"] == "streamable-http"
    assert output["mcpServers"] == {
        "marivo": {
            "url": "http://127.0.0.1:9000/mcp",
        }
    }
    assert output["mcp_server"] == {
        "command": "marivo-mcp-http",
        "env": {
            "MARIVO_MODE": "remote",
            "MARIVO_BASE_URL": "http://marivo.test",
            "MARIVO_API_TOKEN": "secret-token",
            "MARIVO_MCP_TRANSPORT": "streamable-http",
            "MARIVO_MCP_HOST": "0.0.0.0",
            "MARIVO_MCP_PORT": "9000",
            "MARIVO_MCP_STREAMABLE_HTTP_PATH": "/mcp",
        },
    }
    assert json.loads(render_client_config(output, client="generic")) == {
        "mcpServers": output["mcpServers"]
    }


def test_mcp_init_http_remote_brackets_ipv6_client_host() -> None:
    output = build_init_config(
        mode="remote",
        base_url="http://marivo.test",
        workspace_root=None,
        client="generic",
        transport="streamable-http",
        http_host="::1",
        http_port=9000,
    )

    assert output["mcpServers"] == {
        "marivo": {
            "url": "http://[::1]:9000/mcp",
        }
    }


def test_mcp_init_http_local_requires_explicit_workspace_root(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.delenv("MARIVO_WORKSPACE_ROOT", raising=False)

    with pytest.raises(TargetResolutionError) as exc_info:
        build_init_config(
            mode="local",
            base_url=None,
            workspace_root=None,
            client="generic",
            transport="streamable-http",
            cwd=str(workspace_root),
        )

    error = exc_info.value
    assert error.code == "workspace_root_required"
    assert error.detail == {
        "tried_sources": ["--workspace-root", "MARIVO_WORKSPACE_ROOT"],
        "transport": "streamable-http",
    }


def test_mcp_init_remote_preserves_api_token() -> None:
    output = build_init_config(
        mode="remote",
        base_url="http://marivo.test",
        api_token="secret-token",
        workspace_root=None,
        client="generic",
    )

    assert output["mcp_server"] == {
        "command": "marivo-mcp",
        "env": {
            "MARIVO_MODE": "remote",
            "MARIVO_BASE_URL": "http://marivo.test",
            "MARIVO_API_TOKEN": "secret-token",
        },
    }


def test_mcp_init_local_print_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="generic",
    )

    assert output["target_kind"] == "local"
    assert output["mcpServers"] == {
        "marivo": {
            "command": "marivo-mcp",
            "env": {
                "MARIVO_MODE": "local",
                "MARIVO_WORKSPACE_ROOT": str(workspace_root),
            },
        }
    }
    assert output["mcp_server"] == {
        "command": "marivo-mcp",
        "env": {
            "MARIVO_MODE": "local",
            "MARIVO_WORKSPACE_ROOT": str(workspace_root),
        },
    }


def test_mcp_init_local_defaults_to_cwd_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=None,
        client="generic",
        cwd=str(workspace_root),
    )

    assert output["mcp_server"] == {
        "command": "marivo-mcp",
        "env": {
            "MARIVO_MODE": "local",
            "MARIVO_WORKSPACE_ROOT": str(workspace_root),
        },
    }


def test_mcp_init_local_requires_workspace_root(monkeypatch: Any) -> None:
    monkeypatch.setattr(init_cli_module.os, "getcwd", lambda: "/does/not/exist")

    with pytest.raises(TargetResolutionError) as exc_info:
        build_init_config(mode="local", base_url=None, workspace_root=None, client="generic")

    assert exc_info.value.code == "workspace_root_required"
    assert exc_info.value.detail == {
        "tried_sources": ["--workspace-root", "MARIVO_WORKSPACE_ROOT", "cwd"]
    }


def test_mcp_init_rejects_unsupported_client() -> None:
    with pytest.raises(TargetResolutionError) as exc_info:
        build_init_config(
            mode="remote",
            base_url="http://marivo.test",
            workspace_root=None,
            client="unknown",
        )

    assert exc_info.value.code == "mcp_init_client_unsupported"


def test_mcp_init_codex_print_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="codex",
        server_name="marivo",
    )

    assert render_client_config(output, client="codex") == (
        "[mcp_servers.marivo]\n"
        'command = "marivo-mcp"\n'
        f'env = {{ MARIVO_MODE = "local", MARIVO_WORKSPACE_ROOT = "{workspace_root}" }}\n'
    )


def test_mcp_init_codex_http_print_config() -> None:
    output = build_init_config(
        mode="remote",
        base_url="http://marivo.test",
        workspace_root=None,
        client="codex",
        server_name="marivo",
        transport="streamable-http",
        http_port=9000,
    )

    assert render_client_config(output, client="codex") == (
        '[mcp_servers.marivo]\nurl = "http://127.0.0.1:9000/mcp"\n'
    )


def test_mcp_init_codex_quotes_non_ascii_server_name(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="codex",
        server_name="服务",
    )

    assert render_client_config(output, client="codex") == (
        '[mcp_servers."服务"]\n'
        'command = "marivo-mcp"\n'
        f'env = {{ MARIVO_MODE = "local", MARIVO_WORKSPACE_ROOT = "{workspace_root}" }}\n'
    )


def test_mcp_init_codex_write_creates_project_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config_path = tmp_path / ".codex" / "config.toml"
    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="codex",
    )

    written_path = write_client_config(
        output,
        client="codex",
        config_path=str(config_path),
    )

    assert written_path == str(config_path)
    assert config_path.read_text() == (
        "[mcp_servers.marivo]\n"
        'command = "marivo-mcp"\n'
        f'env = {{ MARIVO_MODE = "local", MARIVO_WORKSPACE_ROOT = "{workspace_root}" }}\n'
    )


def test_mcp_init_codex_write_replaces_existing_server_and_preserves_others(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'model = "gpt-5.5"\n\n'
        "[mcp_servers.marivo]\n"
        'command = "/old/marivo-mcp"\n'
        'env = { MARIVO_BASE_URL = "http://old.test" }\n\n'
        "[mcp_servers.other]\n"
        'command = "other-mcp"\n'
    )
    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="codex",
    )

    write_client_config(output, client="codex", config_path=str(config_path))

    assert config_path.read_text() == (
        'model = "gpt-5.5"\n\n'
        "[mcp_servers.marivo]\n"
        'command = "marivo-mcp"\n'
        f'env = {{ MARIVO_MODE = "local", MARIVO_WORKSPACE_ROOT = "{workspace_root}" }}\n'
        "[mcp_servers.other]\n"
        'command = "other-mcp"\n'
    )


def test_mcp_init_generic_write_is_unsupported(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    output = build_init_config(
        mode="local",
        base_url=None,
        workspace_root=str(workspace_root),
        client="generic",
    )

    with pytest.raises(TargetResolutionError) as exc_info:
        write_client_config(output, client="generic", config_path=str(tmp_path / "config.json"))

    assert exc_info.value.code == "mcp_init_client_unsupported"


def test_mcp_init_generic_write_cli_falls_back_to_print_config(
    tmp_path: Path,
    capsys: Any,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    init_main(
        [
            "--mode",
            "local",
            "--workspace-root",
            str(workspace_root),
            "--client",
            "generic",
            "--write",
        ]
    )

    captured = capsys.readouterr()
    assert "printing config instead" in captured.err
    assert json.loads(captured.out) == {
        "mcpServers": {
            "marivo": {
                "command": "marivo-mcp",
                "env": {
                    "MARIVO_MODE": "local",
                    "MARIVO_WORKSPACE_ROOT": str(workspace_root),
                },
            }
        }
    }
