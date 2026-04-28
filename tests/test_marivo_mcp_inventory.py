from __future__ import annotations

import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast, get_type_hints

import pytest
from pydantic import TypeAdapter

MARIVO_MCP_SRC = Path(__file__).resolve().parents[1] / "marivo-mcp" / "src"
sys.path.insert(0, str(MARIVO_MCP_SRC))

config_module = import_module("marivo_mcp.config")
inventory_module = import_module("marivo_mcp.inventory")
resources_module = import_module("marivo_mcp.resources")
tools_module = import_module("marivo_mcp.tools")

MarivoMcpConfig = config_module.MarivoMcpConfig
HttpTransportConfig = config_module.HttpTransportConfig
get_implemented_specs = inventory_module.get_implemented_specs
get_surface_spec = inventory_module.get_surface_spec
get_tier_specs = inventory_module.get_tier_specs
register_resources = resources_module.register_resources
register_tools = tools_module.register_tools


def test_mcp_tools_import_without_marivo_app_package(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MARIVO_MCP_SRC)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import marivo_mcp.tools; import marivo_mcp.server",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


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
    return MarivoMcpConfig(
        base_url="http://marivo.test",
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
        assert typed_func._marivo_http_method == spec.http_method
        assert (typed_func._marivo_http_path,) == spec.http_paths


def test_registered_resources_expose_inventory_http_metadata() -> None:
    server = cast("Any", _FakeServer())
    register_resources(server, _build_config())

    for name, func in server.resources.items():
        spec = get_surface_spec(name)
        typed_func = cast("Any", func)
        assert typed_func._marivo_http_method == spec.http_method
        assert typed_func._marivo_http_paths == spec.http_paths


def test_p0_inventory_surfaces_remain_implemented() -> None:
    missing = [spec.name for spec in get_tier_specs("p0") if not spec.implemented]
    assert missing == []


def test_inventory_tracks_known_http_contracts_not_yet_wrapped() -> None:
    assert get_surface_spec("list_sessions").implemented is False
    assert get_surface_spec("get_source").implemented is False


def test_observe_tool_time_scope_annotation_exposes_discriminator_schema() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    observe = server.tools["observe"]
    hints = get_type_hints(observe, include_extras=True)
    time_scope_schema = TypeAdapter(hints["time_scope"]).json_schema()

    # Verify key content fragments (not exact match due to multi-line formatting)
    desc = time_scope_schema["description"]
    assert "Canonical object only" in desc
    assert "shorthand strings are NOT accepted" in desc
    assert "half-open [start, end)" in desc
    assert "end is EXCLUSIVE" in desc
    assert '{"kind":"range","start":"2024-03-01","end":"2024-04-01"}' in desc
    assert "covers March 1-31 inclusive" in desc
    assert time_scope_schema["$ref"] == "#/$defs/JsonObject"
    assert time_scope_schema["$defs"]["JsonObject"]["type"] == "object"
    scope_schema = TypeAdapter(hints["scope"]).json_schema()
    assert scope_schema["anyOf"][0]["$ref"] == "#/$defs/ObserveScope"


def test_observe_tool_time_scope_string_error_points_to_canonical_shape() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    observe = server.tools["observe"]
    hints = get_type_hints(observe, include_extras=True)
    adapter = TypeAdapter(hints["time_scope"])

    with pytest.raises(Exception) as exc_info:
        adapter.validate_python("2026-04-01..2026-04-15")

    message = str(exc_info.value)
    assert "observe.time_scope requires canonical object shape" in message
    assert '{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}' in message
    assert "end is EXCLUSIVE" in message
    assert "pass the next day as end" in message


def test_semantic_create_tools_expose_authoring_models() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    enum_hints = get_type_hints(server.tools["create_enum_set"])
    metric_hints = get_type_hints(server.tools["create_metric"])
    binding_hints = get_type_hints(server.tools["create_binding"])

    enum_header_schema = TypeAdapter(enum_hints["header"]).json_schema()
    assert enum_header_schema["additionalProperties"] is False
    assert set(enum_header_schema["required"]) == {"enum_set_ref", "value_type"}

    metric_header_schema = TypeAdapter(metric_hints["header"]).json_schema()
    assert "observation_grain_ref" in metric_header_schema["required"]
    assert "metric_contract_version" in metric_header_schema["required"]

    binding_contract_schema = TypeAdapter(binding_hints["interface_contract"]).json_schema()
    defs = binding_contract_schema["$defs"]
    assert defs["McpTimeSurface"]["additionalProperties"] is False
    assert "surface_ref" in defs["McpTimeSurface"]["required"]
    assert "date_surface_ref" in defs["McpTimeBinding"]["properties"]


def test_create_enum_set_rejects_common_header_typos() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())
    hints = get_type_hints(server.tools["create_enum_set"])
    adapter = TypeAdapter(hints["header"])

    with pytest.raises(Exception) as exc_info:
        adapter.validate_python(
            {
                "enum_ref": "enum.query_state",
                "enum_contract_version": "enum.v1",
                "value_type": "string",
            }
        )

    message = str(exc_info.value)
    assert "enum_set_ref" in message
    assert "enum_ref" in message
    assert "enum_contract_version" in message


def test_create_binding_rejects_time_surface_ref_typo() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())
    hints = get_type_hints(server.tools["create_binding"])
    adapter = TypeAdapter(hints["interface_contract"])

    with pytest.raises(Exception) as exc_info:
        adapter.validate_python(
            {
                "carrier_bindings": [
                    {
                        "binding_key": "primary",
                        "carrier_kind": "table",
                        "carrier_locator": "analytics.trino_queries",
                        "binding_role": "primary",
                        "time_surfaces": [
                            {
                                "time_surface_ref": "time_surface.created_at",
                                "physical_name": "created_at",
                            }
                        ],
                    }
                ],
                "field_bindings": [],
            }
        )

    message = str(exc_info.value)
    assert "surface_ref" in message
    assert "time_surface_ref" in message


def test_create_binding_rejects_metric_input_metric_ref() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())
    hints = get_type_hints(server.tools["create_binding"])
    adapter = TypeAdapter(hints["interface_contract"])

    with pytest.raises(Exception) as exc_info:
        adapter.validate_python(
            {
                "carrier_bindings": [
                    {
                        "binding_key": "primary",
                        "carrier_kind": "table",
                        "carrier_locator": "analytics.trino_queries",
                        "binding_role": "primary",
                        "field_surfaces": [
                            {"surface_ref": "field.count", "physical_name": "query_id"}
                        ],
                    }
                ],
                "field_bindings": [
                    {
                        "carrier_binding_key": "primary",
                        "target": {"target_kind": "metric_input", "target_key": "count_target"},
                        "semantic_ref": "metric.trino_query_count",
                        "surface_ref": "field.count",
                    }
                ],
            }
        )

    assert "metric_input." in str(exc_info.value)


def test_create_binding_rejects_batch_only_default_refs() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())
    hints = get_type_hints(server.tools["create_binding"])
    adapter = TypeAdapter(hints["interface_contract"])

    with pytest.raises(Exception) as exc_info:
        adapter.validate_python(
            {
                "carrier_binding_refs": ["shared_primary"],
                "time_binding_refs": ["shared_time"],
                "field_bindings": [],
            }
        )

    message = str(exc_info.value)
    assert "carrier_binding_refs" in message
    assert "time_binding_refs" in message


def test_typed_intent_tools_expose_top_level_session_id() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    typed_intent_names = [
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
    ]

    for tool_name in typed_intent_names:
        hints = get_type_hints(server.tools[tool_name])
        assert hints["session_id"] is str
        assert "request" not in hints


def test_detect_and_diagnose_time_scope_annotations_expose_range_and_granularity() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    detect = server.tools["detect"]
    diagnose = server.tools["diagnose"]

    detect_time_scope = TypeAdapter(get_type_hints(detect)["time_scope"]).json_schema()
    detect_granularity = TypeAdapter(get_type_hints(detect)["granularity"]).json_schema()
    diagnose_time_scope = TypeAdapter(get_type_hints(diagnose)["time_scope"]).json_schema()
    diagnose_granularity = TypeAdapter(get_type_hints(diagnose)["granularity"]).json_schema()

    assert set(detect_time_scope["required"]) == {"kind", "start", "end"}
    assert detect_time_scope["properties"]["kind"]["const"] == "range"
    assert detect_granularity["enum"] == ["hour", "day", "week", "month"]
    assert diagnose_time_scope["$defs"]["JsonObject"]["type"] == "object"
    assert diagnose_granularity["anyOf"][0]["enum"] == ["hour", "day", "week", "month"]


def test_t6_tools_use_strongly_typed_nested_models_instead_of_raw_dicts() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    observe_hints = get_type_hints(server.tools["observe"])
    detect_hints = get_type_hints(server.tools["detect"])
    diagnose_hints = get_type_hints(server.tools["diagnose"])
    compare_hints = get_type_hints(server.tools["compare"])
    test_hints = get_type_hints(server.tools["test_intent"])
    forecast_hints = get_type_hints(server.tools["forecast"])
    attribute_hints = get_type_hints(server.tools["attribute"])
    validate_hints = get_type_hints(server.tools["validate"])

    observe_time_scope_schema = TypeAdapter(observe_hints["time_scope"]).json_schema()
    assert observe_time_scope_schema["$defs"]["JsonObject"]["type"] == "object"
    detect_time_scope_schema = TypeAdapter(detect_hints["time_scope"]).json_schema()
    assert set(detect_time_scope_schema["required"]) == {"kind", "start", "end"}
    assert (
        TypeAdapter(diagnose_hints["time_scope"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert set(TypeAdapter(compare_hints["left_ref"]).json_schema()["required"]) == {
        "step_id",
        "step_type",
    }
    assert set(TypeAdapter(compare_hints["right_ref"]).json_schema()["required"]) == {
        "step_id",
        "step_type",
    }
    assert (
        TypeAdapter(test_hints["left_ref"]).json_schema()["$defs"]["JsonObject"]["type"] == "object"
    )
    assert (
        TypeAdapter(test_hints["right_ref"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert (
        TypeAdapter(test_hints["hypothesis"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert (
        TypeAdapter(forecast_hints["source_ref"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert (
        TypeAdapter(attribute_hints["left"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert (
        TypeAdapter(attribute_hints["right"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )
    assert (
        TypeAdapter(validate_hints["left"]).json_schema()["$defs"]["JsonObject"]["type"] == "object"
    )
    assert (
        TypeAdapter(validate_hints["right"]).json_schema()["$defs"]["JsonObject"]["type"]
        == "object"
    )


def test_nested_object_params_still_validate_against_canonical_models() -> None:
    server = cast("Any", _FakeServer())
    register_tools(server, _build_config())

    tools_module_any = cast("Any", tools_module)
    assert tools_module_any._require_observe_time_scope_object(
        {"kind": "range", "start": "2026-04-01", "end": "2026-04-15"}
    ) == {"kind": "range", "start": "2026-04-01", "end": "2026-04-15"}
    assert tools_module_any._require_structured_object(
        {"step_id": "step_123", "step_type": "observe"},
        field_name="left_ref",
    ) == {"step_id": "step_123", "step_type": "observe"}

    with pytest.raises(ValueError, match=r"observe\.time_scope requires canonical object shape"):
        tools_module_any._require_observe_time_scope_object('{"kind":"range"}')

    with pytest.raises(ValueError, match="Pass a structured object, not a JSON-encoded string"):
        tools_module_any._require_structured_object('{"step_id":"step_123"}', field_name="left_ref")


def test_typed_intent_tool_runtime_schema_uses_objects_not_strings() -> None:
    mcp_module = pytest.importorskip("mcp.server.fastmcp")
    server = mcp_module.FastMCP("test")
    register_tools(server, _build_config())

    cases = {
        "observe": "time_scope",
        "compare": "left_ref",
        "correlate": "left_ref",
        "detect": "time_scope",
        "test_intent": "hypothesis",
        "forecast": "source_ref",
        "attribute": "left",
        "diagnose": "time_scope",
        "validate": "left",
    }

    for tool_name, field_name in cases.items():
        tool = server._tool_manager.get_tool(tool_name)
        assert tool is not None
        assert tool.parameters["properties"][field_name]["type"] == "object"
