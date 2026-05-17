"""Verify stdio and HTTP MCP transports expose the correct tool surfaces.

HTTP mode registers all tool groups including catalog/OpenAPI introspection.
Stdio mode omits catalog tools because the local runtime lacks the wired
FastAPI app and analytics engine required by those tools.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from marivo.contracts.calendar import (
    CalendarDataListResponse,
    CalendarDataQuery,
    CalendarDataRow,
    CalendarDataUpdateRequest,
    CalendarDataUpdateResponse,
)
from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools import register_tools
from marivo.transports.mcp.tools.schemas import McpAoiSliceRef, McpTimeScope


class _FakeSvc:
    """Minimal stub satisfying semantic_v2 / datasource service contracts."""

    def list_semantic_models(self, **kw):
        return {}

    def validate_osi_semantic_models(self, **kw):
        return {}

    def import_osi_semantic_models(self, **kw):
        return None

    def export_osi_semantic_models(self, **kw):
        return {}

    def get_semantic_model(self, **kw):
        return {}

    def delete_semantic_model(self, **kw):
        return {}

    def register_datasource(self, **kw):
        return {}

    def list_datasources(self, **kw):
        return {}

    def get_datasource(self, **kw):
        return {}

    def update_datasource(self, **kw):
        return {}

    def delete_datasource(self, **kw):
        return {}

    def browse_catalog_schemas(self, **kw):
        return {}

    def browse_catalog_tables(self, **kw):
        return {}

    def browse_catalog_columns(self, **kw):
        return {}

    def preview_table(self, **kw):
        return {}

    def list_calendar_data(self, input):
        return CalendarDataListResponse(rows=[], row_count=0, query=input)

    def update_calendar_data(self, input):
        return CalendarDataUpdateResponse(
            status="updated",
            row_count=len(input.rows),
            inserted_count=len(input.rows),
            updated_count=0,
        )


class FakeRuntime:
    """Minimal stub satisfying register_tools' runtime contract."""

    _services: dict[str, _FakeSvc] = {
        "semantic_v2": _FakeSvc(),
        "datasource": _FakeSvc(),
        "calendar_data": _FakeSvc(),
    }

    def get_service(self, name: str) -> _FakeSvc:
        return self._services[name]

    # Methods called by call_runtime in intent tools
    def observe(self, **kw):
        return {}

    def compare(self, **kw):
        return {}

    def decompose(self, **kw):
        return {}

    def detect(self, **kw):
        return {}

    def correlate(self, **kw):
        return {}

    def test(self, **kw):
        return {}

    def forecast(self, **kw):
        return {}

    def attribute(self, **kw):
        return {}

    def diagnose(self, **kw):
        return {}

    def validate(self, **kw):
        return {}

    def create_session(self, **kw):
        return {}

    def list_sessions(self, **kw):
        return {}

    def get_session(self, **kw):
        return {}

    def terminate_session(self, **kw):
        return {}

    def get_session_state(self, **kw):
        return {}

    def query_session_state(self, **kw):
        return {}

    def get_proposition_context(self, **kw):
        return {}


# Catalog / OpenAPI tools are only registered in HTTP mode.
_HTTP_ONLY_TOOLS = frozenset(
    {
        "health_check",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
    }
)


def _tool_names(server: FastMCP) -> set[str]:
    return {t.name for t in server._tool_manager.list_tools()}


def test_http_registers_all_tools():
    """HTTP transport registers the full tool surface including catalog tools."""
    server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(server, FakeRuntime(), transport="http")
    tools = _tool_names(server)
    assert _HTTP_ONLY_TOOLS.issubset(tools), (
        f"HTTP mode missing catalog tools: {_HTTP_ONLY_TOOLS - tools}"
    )


def test_stdio_omits_catalog_tools():
    """Stdio transport omits catalog/OpenAPI tools that require a wired app."""
    server = FastMCP("test-stdio")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)
    assert _HTTP_ONLY_TOOLS.isdisjoint(tools), (
        f"Stdio mode should not expose catalog tools: {_HTTP_ONLY_TOOLS & tools}"
    )


def test_semantic_tools_expose_compact_document_inventory() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)

    assert {
        "list_semantic_models",
        "get_semantic_model",
        "validate_osi_semantic_models",
        "import_osi_semantic_models",
        "export_osi_semantic_models",
        "delete_semantic_model",
    }.issubset(tools)
    assert {
        "create_semantic_model",
        "import_osi_document",
        "export_osi_document",
        "update_semantic_model",
        "get_semantic_model_readiness",
        "create_dataset",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "delete_dataset",
        "create_field",
        "list_fields",
        "get_field",
        "update_field",
        "delete_field",
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
    }.isdisjoint(tools)


def test_semantic_document_tools_accept_inline_document_or_file_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    validate_props = tools["validate_osi_semantic_models"].parameters["$defs"][
        "McpOsiDocumentInput"
    ]["properties"]
    import_props = tools["import_osi_semantic_models"].parameters["$defs"]["McpOsiDocumentInput"][
        "properties"
    ]
    export_props = tools["export_osi_semantic_models"].parameters["properties"]

    assert set(validate_props) == {"document", "input_path"}
    assert set(import_props) == {"document", "input_path"}
    assert set(export_props) == {"semantic_model_name", "output_path"}


def test_mcp_semantic_tools_do_not_expose_requesting_user() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    checked_names = [
        "list_semantic_models",
        "get_semantic_model",
        "delete_semantic_model",
    ]
    for name in checked_names:
        assert "requesting_user" not in tools[name].parameters.get("properties", {})
        assert "owner_user" not in tools[name].parameters.get("properties", {})


def test_delete_semantic_model_tool_schema_is_model_only() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools["delete_semantic_model"].parameters["properties"]) == {"model"}


def test_preview_table_filters_schema_is_structured_object() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    filters_schema = tools["preview_table"].parameters["properties"]["filters"]

    assert filters_schema == {
        "anyOf": [
            {"additionalProperties": True, "type": "object"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Filters",
    }


def test_test_intent_tool_schema_matches_current_aoi_surface() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    properties = tools["test_intent"].parameters["properties"]
    slice_schema = tools["test_intent"].parameters["$defs"]["McpAoiSliceRef"]
    hypothesis_schema = tools["test_intent"].parameters["$defs"]["McpTestHypothesis"]

    assert "method" not in properties
    assert "kind" not in properties
    assert properties["left"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert properties["right"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert slice_schema["additionalProperties"] is False
    assert set(slice_schema["properties"]) == {"time_scope", "filter"}
    assert "scope" not in slice_schema["properties"]
    assert "hypothesis" in tools["test_intent"].parameters["required"]
    assert properties["hypothesis"] == {"$ref": "#/$defs/McpTestHypothesis"}
    assert hypothesis_schema["additionalProperties"] is False
    assert hypothesis_schema["required"] == ["alternative", "significance"]
    assert "family" not in hypothesis_schema["properties"]
    assert hypothesis_schema["properties"]["alternative"]["enum"] == [
        "two_sided",
        "greater",
        "less",
    ]
    significance_schema = hypothesis_schema["properties"]["significance"]
    assert significance_schema["enum"] == ["conservative", "balanced", "aggressive"]
    assert "conservative=0.01" in significance_schema["description"]
    assert "balanced=0.05" in significance_schema["description"]
    assert "aggressive=0.10" in significance_schema["description"]


def test_observe_and_detect_filter_schemas_expose_aoi_expression() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    for tool_name in ("observe", "detect"):
        parameters = tools[tool_name].parameters
        filter_schema = parameters["properties"]["filter_expression"]
        expression_schema = parameters["$defs"]["McpExpression"]
        dialect_schema = parameters["$defs"]["McpDialect"]

        assert filter_schema["anyOf"][0] == {"$ref": "#/$defs/McpExpression"}
        assert "AOI Expression" in filter_schema["description"]
        assert expression_schema["additionalProperties"] is False
        assert expression_schema["required"] == ["dialects"]
        assert expression_schema["properties"]["dialects"]["items"] == {
            "$ref": "#/$defs/McpDialect"
        }
        assert dialect_schema["required"] == ["expression"]
        assert dialect_schema["properties"]["dialect"]["default"] == "ANSI_SQL"

    observe_props = tools["observe"].parameters["properties"]
    assert "without dimensions" in observe_props["granularity"]["description"]
    assert "without granularity" in observe_props["dimensions"]["description"]
    assert "scalar observe" in observe_props["granularity"]["description"]


def test_mcp_time_scope_schema_documents_naive_datetime_default() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    time_scope_schema = tools["observe"].parameters["$defs"]["McpTimeScope"]
    start_description = time_scope_schema["properties"]["start"]["description"]
    end_description = time_scope_schema["properties"]["end"]["description"]

    for description in (start_description, end_description):
        assert "date-only strings" in description
        assert "timezone-naive datetimes" in description
        assert "timezone-aware" in description
        assert "service system timezone" in description
    assert "half-open [start, end) interval" in start_description
    assert "date-only end means midnight" in end_description
    assert time_scope_schema["examples"] == [
        {"field": "log_date", "start": "2026-05-15", "end": "2026-05-16"},
        {
            "field": "event_time",
            "start": "2026-05-15T00:00:00",
            "end": "2026-05-16T00:00:00",
        },
        {
            "field": "event_time",
            "start": "2026-05-15T00:00:00+08:00",
            "end": "2026-05-16T00:00:00+08:00",
        },
    ]


def test_validate_hypothesis_schema_omits_fixed_family() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    properties = tools["validate"].parameters["properties"]
    slice_schema = tools["validate"].parameters["$defs"]["McpAoiSliceRef"]
    hypothesis_schema = tools["validate"].parameters["$defs"]["McpValidateHypothesis"]

    assert "method" not in properties
    assert properties["left"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert properties["right"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert slice_schema["additionalProperties"] is False
    assert set(slice_schema["properties"]) == {"time_scope", "filter"}
    assert "scope" not in slice_schema["properties"]
    assert properties["hypothesis"]["anyOf"][0] == {"$ref": "#/$defs/McpValidateHypothesis"}
    assert hypothesis_schema["additionalProperties"] is False
    assert "required" not in hypothesis_schema
    assert set(hypothesis_schema["properties"]) == {"alternative", "significance"}
    assert "family" not in hypothesis_schema["properties"]
    assert hypothesis_schema["properties"]["alternative"]["anyOf"][0]["enum"] == [
        "two_sided",
        "greater",
        "less",
    ]
    assert hypothesis_schema["properties"]["significance"]["anyOf"][0]["enum"] == [
        "conservative",
        "balanced",
        "aggressive",
    ]


def test_attribute_schema_uses_aoi_slice_refs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    properties = tools["attribute"].parameters["properties"]
    slice_schema = tools["attribute"].parameters["$defs"]["McpAoiSliceRef"]

    assert properties["left"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert properties["right"] == {"$ref": "#/$defs/McpAoiSliceRef"}
    assert slice_schema["additionalProperties"] is False
    assert set(slice_schema["properties"]) == {"time_scope", "filter"}
    assert "scope" not in slice_schema["properties"]
    assert properties["decomposition_method"]["default"] == "delta_share"
    assert properties["decomposition_method"]["const"] == "delta_share"


def test_attribute_tool_passes_generated_request(monkeypatch) -> None:
    calls = {}

    async def fake_call_runtime(method, /, **kwargs):
        calls["method"] = method
        calls["kwargs"] = kwargs
        return {"data": {}, "error": None}

    monkeypatch.setattr("marivo.transports.mcp.tools.intents.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["attribute"]
    slice_ref = McpAoiSliceRef(
        time_scope=McpTimeScope(
            field="event_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        )
    )

    result = asyncio.run(
        tool.fn(
            session_id="sess_1",
            metric="view_time",
            left=slice_ref,
            right=slice_ref,
            dimensions=["region"],
        )
    )

    assert result == {"data": {}, "error": None}
    assert calls["method"] == runtime.attribute
    assert "request" in calls["kwargs"]
    assert "params" not in calls["kwargs"]
    assert isinstance(calls["kwargs"]["request"], aoi.Attribute)
    assert calls["kwargs"]["request"].decomposition_limit == 5


def test_diagnose_schema_documents_mode_specific_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    diagnose = tools["diagnose"]
    properties = diagnose.parameters["properties"]

    assert "auto_detect mode requires time_scope and granularity" in diagnose.description
    assert "explicit_compare mode requires current and baseline" in diagnose.description
    assert "must omit the top-level time_scope and granularity fields together" in (
        diagnose.description
    )
    assert "auto_detect requires time_scope and granularity" in properties["mode"]["description"]
    assert "omits top-level time_scope and granularity" in properties["mode"]["description"]
    assert "mode='auto_detect'" in properties["time_scope"]["description"]
    assert "put time_scope inside current and baseline" in (properties["time_scope"]["description"])
    assert "mode='auto_detect'" in properties["granularity"]["description"]
    assert (
        "omit this top-level field together with top-level time_scope"
        in (properties["granularity"]["description"])
    )
    assert "mode='explicit_compare'" in properties["current"]["description"]
    assert "mode='explicit_compare'" in properties["baseline"]["description"]


def test_correlate_and_forecast_tool_schemas_document_time_series_artifact_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    correlate_props = tools["correlate"].parameters["properties"]
    forecast_props = tools["forecast"].parameters["properties"]

    for name in ("left_artifact_id", "right_artifact_id"):
        description = correlate_props[name]["description"]
        assert "observe(time_series)" in description
        assert "granularity" in description

    assert correlate_props["min_pairs"]["default"] is None
    assert correlate_props["min_pairs"]["anyOf"][0]["minimum"] == 1
    assert "service default of 5" in correlate_props["min_pairs"]["description"]

    forecast_description = forecast_props["source_artifact_id"]["description"]
    assert "observe(time_series)" in forecast_description
    assert "granularity" in forecast_description
    assert "datasource" in forecast_description


def test_correlate_tool_passes_generated_request_with_min_pairs(monkeypatch) -> None:
    calls = {}

    async def fake_call_runtime(method, /, **kwargs):
        calls["method"] = method
        calls["kwargs"] = kwargs
        return {"data": {}, "error": None}

    monkeypatch.setattr("marivo.transports.mcp.tools.intents.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["correlate"]

    result = asyncio.run(
        tool.fn(
            session_id="sess_1",
            left_artifact_id="art_left",
            right_artifact_id="art_right",
            method="pearson",
            min_pairs=7,
        )
    )

    assert result == {"data": {}, "error": None}
    assert calls["method"] == runtime.correlate
    request = calls["kwargs"]["request"]
    assert isinstance(request, aoi.Correlate)
    assert request.left_artifact_id == "art_left"
    assert request.right_artifact_id == "art_right"
    assert request.method == "pearson"
    assert request.min_pairs == 7


def test_forecast_tool_passes_generated_request(monkeypatch) -> None:
    calls = {}

    async def fake_call_runtime(method, /, **kwargs):
        calls["method"] = method
        calls["kwargs"] = kwargs
        return {"data": {}, "error": None}

    monkeypatch.setattr("marivo.transports.mcp.tools.intents.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["forecast"]

    result = asyncio.run(
        tool.fn(
            session_id="sess_1",
            source_artifact_id="art_source",
            horizon=14,
        )
    )

    assert result == {"data": {}, "error": None}
    assert calls["method"] == runtime.forecast
    request = calls["kwargs"]["request"]
    assert isinstance(request, aoi.Forecast)
    assert request.source_artifact_id == "art_source"
    assert request.horizon == 14
    assert set(request.model_dump()) == {"source_artifact_id", "horizon"}


def test_compare_tool_schema_exposes_compare_type_enum_and_default() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    compare_type = tools["compare"].parameters["properties"]["compare_type"]

    assert compare_type["default"] == "normal"
    assert compare_type["enum"] == [
        "normal",
        "holiday_aligned",
        "weekday_aligned",
        "holiday_and_weekday_aligned",
    ]


def test_calendar_tools_registered_in_both_modes() -> None:
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    for server in (stdio_server, http_server):
        tools = _tool_names(server)
        assert {"list_calendar_data", "update_calendar_data"}.issubset(tools)


def test_calendar_tool_schemas_are_structured_and_typed() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    list_tool = tools["list_calendar_data"]
    update_tool = tools["update_calendar_data"]

    assert list_tool.parameters["properties"]["input"] == {"$ref": "#/$defs/CalendarDataQuery"}
    assert update_tool.parameters["properties"]["input"] == {
        "$ref": "#/$defs/CalendarDataUpdateRequest"
    }
    assert list_tool.output_schema is not None
    assert update_tool.output_schema is not None
    assert "CalendarDataListResponse" in list_tool.output_schema["$defs"]
    assert "CalendarDataUpdateResponse" in update_tool.output_schema["$defs"]
    assert "CalendarDataRow" in update_tool.parameters["$defs"]
    row_schema = update_tool.parameters["$defs"]["CalendarDataRow"]
    assert row_schema["additionalProperties"] is False
    assert set(row_schema["properties"]) == {
        "calendar_date",
        "day_kind",
        "holiday_name",
        "holiday_group_id",
        "year_relative_holiday_key",
    }


def test_calendar_tools_call_runtime_with_typed_inputs(monkeypatch) -> None:
    calls = []

    async def fake_call_runtime(method, /, **kwargs):
        calls.append((method, kwargs))
        return {"data": {"rows": [], "row_count": 0, "query": {}}, "error": None}

    monkeypatch.setattr("marivo.transports.mcp.tools.calendar.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["list_calendar_data"]
    query = CalendarDataQuery(start_date="2026-02-01", end_date="2026-03-01")

    result = asyncio.run(tool.fn(input=query))

    assert result.error is None
    assert calls[0][0] == runtime.get_service("calendar_data").list_calendar_data
    assert calls[0][1]["input"] == query


def test_update_calendar_data_tool_accepts_calendar_rows(monkeypatch) -> None:
    calls = []

    async def fake_call_runtime(method, /, **kwargs):
        calls.append((method, kwargs))
        return {
            "data": {
                "status": "updated",
                "row_count": 1,
                "inserted_count": 1,
                "updated_count": 0,
            },
            "error": None,
        }

    monkeypatch.setattr("marivo.transports.mcp.tools.calendar.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["update_calendar_data"]
    request = CalendarDataUpdateRequest(
        rows=[
            CalendarDataRow(
                calendar_date="2026-02-16",
                day_kind="holiday",
                holiday_name="Spring Festival",
                holiday_group_id="spring_festival",
            )
        ]
    )

    result = asyncio.run(tool.fn(input=request))

    assert result.data is not None
    assert result.data.status == "updated"
    assert calls[0][0] == runtime.get_service("calendar_data").update_calendar_data
    assert calls[0][1]["input"] == request


def test_shared_tools_identical_schema():
    """Tools present in both modes have identical parameter schemas."""
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    stdio_tools = {t.name: t.parameters for t in stdio_server._tool_manager.list_tools()}
    http_tools = {t.name: t.parameters for t in http_server._tool_manager.list_tools()}

    shared = set(stdio_tools) & set(http_tools)
    for name in sorted(shared):
        assert stdio_tools[name] == http_tools[name], f"Schema diverged for {name}"


def test_stdio_is_subset_of_http():
    """Stdio tool set is a strict subset of HTTP tool set."""
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    stdio_tools = _tool_names(stdio_server)
    http_tools = _tool_names(http_server)
    assert stdio_tools < http_tools, (
        f"Stdio tools should be a strict subset of HTTP tools; "
        f"stdio-only: {stdio_tools - http_tools}"
    )
