from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

FACTUM_MCP_SRC = Path(__file__).resolve().parents[1] / "factum-mcp" / "src"
CONFIG_PATH = FACTUM_MCP_SRC / "factum_mcp" / "config.py"
CONFIG_SPEC = spec_from_file_location("factum_mcp.config", CONFIG_PATH)
assert CONFIG_SPEC is not None
assert CONFIG_SPEC.loader is not None
CONFIG_MODULE = module_from_spec(CONFIG_SPEC)
CONFIG_SPEC.loader.exec_module(CONFIG_MODULE)

FactumMcpConfigError = CONFIG_MODULE.FactumMcpConfigError
load_config_from_env = CONFIG_MODULE.load_config_from_env


class TestFactumMcpConfig:
    def test_load_config_reads_required_and_optional_environment(self) -> None:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv("FACTUM_BASE_URL", "http://127.0.0.1:8000")
            monkeypatch.setenv("FACTUM_TIMEOUT_MS", "15000")
            monkeypatch.setenv("FACTUM_OPENAPI_CACHE_TTL_SEC", "60")
            monkeypatch.setenv("FACTUM_DEFAULT_SOURCE_ID", "src_demo")
            monkeypatch.setenv("FACTUM_API_TOKEN", "secret-token")

            config = load_config_from_env()

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

    def test_load_config_reads_streamable_http_options(self) -> None:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv("FACTUM_BASE_URL", "http://127.0.0.1:8000")
            monkeypatch.setenv("FACTUM_MCP_TRANSPORT", "streamable-http")
            monkeypatch.setenv("FACTUM_MCP_HOST", "0.0.0.0")
            monkeypatch.setenv("FACTUM_MCP_PORT", "9000")
            monkeypatch.setenv("FACTUM_MCP_STREAMABLE_HTTP_PATH", "/")
            monkeypatch.setenv("FACTUM_MCP_STATELESS_HTTP", "false")
            monkeypatch.setenv("FACTUM_MCP_JSON_RESPONSE", "false")

            config = load_config_from_env()

        assert config.transport == "streamable-http"
        assert config.http.host == "0.0.0.0"
        assert config.http.port == 9000
        assert config.http.streamable_http_path == "/"
        assert config.http.stateless_http is False
        assert config.http.json_response is False

    def test_load_config_requires_base_url(self) -> None:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.delenv("FACTUM_BASE_URL", raising=False)

            with pytest.raises(FactumMcpConfigError, match="FACTUM_BASE_URL is required"):
                load_config_from_env()

    def test_load_config_rejects_invalid_integer_values(self) -> None:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv("FACTUM_BASE_URL", "http://127.0.0.1:8000")
            monkeypatch.setenv("FACTUM_TIMEOUT_MS", "not-an-int")

            with pytest.raises(FactumMcpConfigError, match="Invalid factum-mcp configuration"):
                load_config_from_env()

    def test_load_config_rejects_invalid_boolean_values(self) -> None:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv("FACTUM_BASE_URL", "http://127.0.0.1:8000")
            monkeypatch.setenv("FACTUM_MCP_JSON_RESPONSE", "maybe")

            with pytest.raises(FactumMcpConfigError, match="Invalid boolean value"):
                load_config_from_env()


def test_no_global_factum_base_url_leak() -> None:
    assert "FACTUM_BASE_URL" not in os.environ or os.environ["FACTUM_BASE_URL"] != ""
