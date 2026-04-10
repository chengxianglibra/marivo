from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

FACTUM_MCP_SRC = Path(__file__).resolve().parents[1] / "factum-mcp" / "src"
sys.path.insert(0, str(FACTUM_MCP_SRC))

config_module = import_module("factum_mcp.config")
inventory_module = import_module("factum_mcp.inventory")
resources_module = import_module("factum_mcp.resources")
tools_module = import_module("factum_mcp.tools")

FactumMcpConfig = config_module.FactumMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
get_implemented_specs = inventory_module.get_implemented_specs
get_surface_spec = inventory_module.get_surface_spec
get_tier_specs = inventory_module.get_tier_specs
register_resources = resources_module.register_resources
register_tools = tools_module.register_tools


class _FakeServerSettings:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 8000
        self.streamable_http_path = "/mcp"


class _FakeServer:
    def __init__(self) -> None:
        self.settings = _FakeServerSettings()
        self.tools: dict[str, Any] = {}
        self.resources: dict[str, Any] = {}

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


def _build_config() -> Any:
    return FactumMcpConfig(
        base_url="http://factum.test",
        api_token=None,
        timeout_ms=1500,
        openapi_cache_ttl_sec=300,
        default_source_id=None,
        transport="stdio",
        http=HttpTransportConfig(),
    )


def test_registered_tools_match_implemented_inventory() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    expected = {spec.name for spec in get_implemented_specs("tool")}
    assert set(server.tools) == expected


def test_registered_resources_match_implemented_inventory() -> None:
    server = cast("Any", _FakeServer())
    register_resources(server, _build_config())

    expected = {spec.name for spec in get_implemented_specs("resource")}
    assert set(server.resources) == expected


def test_registered_tools_expose_inventory_method_and_path_metadata() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    for name, func in server.tools.items():
        spec = get_surface_spec(name)
        typed_func = cast("Any", func)
        assert typed_func._factum_http_method == spec.http_method
        assert (typed_func._factum_http_path,) == spec.http_paths


def test_registered_resources_expose_inventory_http_metadata() -> None:
    server = cast("Any", _FakeServer())
    register_resources(server, _build_config())

    for name, func in server.resources.items():
        spec = get_surface_spec(name)
        typed_func = cast("Any", func)
        assert typed_func._factum_http_method == spec.http_method
        assert typed_func._factum_http_paths == spec.http_paths


def test_p0_inventory_surfaces_remain_implemented() -> None:
    missing = [spec.name for spec in get_tier_specs("p0") if not spec.implemented]
    assert missing == []


def test_inventory_tracks_known_http_contracts_not_yet_wrapped() -> None:
    assert get_surface_spec("list_sessions").implemented is False
    assert get_surface_spec("get_source").implemented is False
