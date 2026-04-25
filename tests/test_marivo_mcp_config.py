from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
CONFIG_PATH = MARIVO_MCP_SRC / "marivo_mcp" / "config.py"
CONFIG_SPEC = spec_from_file_location("marivo_mcp.config", CONFIG_PATH)
assert CONFIG_SPEC is not None
assert CONFIG_SPEC.loader is not None
CONFIG_MODULE = module_from_spec(CONFIG_SPEC)
CONFIG_SPEC.loader.exec_module(CONFIG_MODULE)

MarivoMcpConfigError = CONFIG_MODULE.MarivoMcpConfigError
TargetResolutionError = CONFIG_MODULE.TargetResolutionError
load_config_from_env = CONFIG_MODULE.load_config_from_env


def test_load_config_reads_required_and_optional_environment() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("MARIVO_TIMEOUT_MS", "15000")
        monkeypatch.setenv("MARIVO_OPENAPI_CACHE_TTL_SEC", "60")
        monkeypatch.setenv("MARIVO_DEFAULT_SOURCE_ID", "src_demo")
        monkeypatch.setenv("MARIVO_API_TOKEN", "secret-token")

        config = load_config_from_env()

    assert config.mode == "auto"
    assert config.base_url == "http://127.0.0.1:8000"
    assert config.transport == "stdio"
    assert config.timeout_ms == 15000
    assert config.openapi_cache_ttl_sec == 60
    assert config.default_source_id == "src_demo"
    assert config.api_token == "secret-token"
    assert config.http.host == "127.0.0.1"
    assert config.http.port == 8000
    assert config.http.streamable_http_path == "/mcp"
    assert config.http.stateless_http is True
    assert config.http.json_response is True


def test_load_config_allows_missing_base_url_for_target_resolution() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("MARIVO_BASE_URL", raising=False)

        config = load_config_from_env()

    assert config.mode == "auto"
    assert config.base_url is None


def test_load_config_reads_target_resolution_options() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_MODE", "local")
        monkeypatch.setenv("MARIVO_WORKSPACE_ROOT", "/tmp/marivo-workspace")
        monkeypatch.setenv("MARIVO_LOCAL_HOST", "localhost")
        monkeypatch.setenv("MARIVO_LOCAL_PORT", "0")
        monkeypatch.setenv("MARIVO_START_TIMEOUT_MS", "30000")
        monkeypatch.setenv("MARIVO_HEALTHCHECK_TIMEOUT_MS", "3000")

        config = load_config_from_env()

    assert config.mode == "local"
    assert config.workspace_root == "/tmp/marivo-workspace"
    assert config.local_host == "localhost"
    assert config.local_port == 0
    assert config.start_timeout_ms == 30000
    assert config.healthcheck_timeout_ms == 3000


def test_load_config_reads_streamable_http_options() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("MARIVO_MCP_TRANSPORT", "streamable-http")
        monkeypatch.setenv("MARIVO_MCP_HOST", "0.0.0.0")
        monkeypatch.setenv("MARIVO_MCP_PORT", "9000")
        monkeypatch.setenv("MARIVO_MCP_STREAMABLE_HTTP_PATH", "/")
        monkeypatch.setenv("MARIVO_MCP_STATELESS_HTTP", "false")
        monkeypatch.setenv("MARIVO_MCP_JSON_RESPONSE", "false")

        config = load_config_from_env()

    assert config.transport == "streamable-http"
    assert config.http.host == "0.0.0.0"
    assert config.http.port == 9000
    assert config.http.streamable_http_path == "/"
    assert config.http.stateless_http is False
    assert config.http.json_response is False


def test_load_config_rejects_invalid_mode_with_structured_error() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_MODE", "debug")

        with pytest.raises(TargetResolutionError) as exc_info:
            load_config_from_env()

    error = exc_info.value
    assert error.code == "config_invalid"
    assert error.detail == {
        "mode_value": "debug",
        "allowed": ["auto", "remote", "local"],
    }
    assert error.guidance == "允许值：auto, remote, local"


def test_load_config_rejects_invalid_integer_values() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("MARIVO_TIMEOUT_MS", "not-an-int")

        with pytest.raises(MarivoMcpConfigError, match="Invalid marivo-mcp configuration"):
            load_config_from_env()


def test_load_config_rejects_invalid_local_port_values() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_LOCAL_PORT", "-1")

        with pytest.raises(MarivoMcpConfigError, match="Invalid marivo-mcp configuration"):
            load_config_from_env()


def test_load_config_rejects_invalid_boolean_values() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MARIVO_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("MARIVO_MCP_JSON_RESPONSE", "maybe")

        with pytest.raises(MarivoMcpConfigError, match="Invalid boolean value"):
            load_config_from_env()


def test_no_global_marivo_base_url_leak() -> None:
    assert "MARIVO_BASE_URL" not in os.environ or os.environ["MARIVO_BASE_URL"] != ""
