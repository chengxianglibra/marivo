"""Verify stdio and HTTP MCP transports expose the correct tool surfaces.

HTTP mode registers all tool groups including catalog/OpenAPI introspection.
Stdio mode omits catalog tools because the local runtime lacks the wired
FastAPI app and analytics engine required by those tools.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from marivo.contracts.calendar import (
    CalendarDataListResponse,
    CalendarDataQuery,
    CalendarDataRow,
    CalendarDataUpdateRequest,
    CalendarDataUpdateResponse,
)
from marivo.contracts.generated import aoi
from marivo.contracts.ids import UserId
from marivo.identity import current_user, require_user
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

    def get_proposition_context(self, **kw):
        return {}


class RecordingTerminateRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.terminate_call: dict[str, object] | None = None

    def terminate_session(
        self,
        session_id: str,
        actor: UserId | None = None,
        terminal_reason: str = "user_closed",
    ) -> None:
        if actor is None:
            actor = UserId(require_user())
        self.terminate_call = {
            "session_id": session_id,
            "actor": actor,
            "terminal_reason": terminal_reason,
        }


class RecordingDiagnoseRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.diagnose_call: dict[str, object] | None = None

    def diagnose(self, **kw):
        self.diagnose_call = kw
        return _diagnose_envelope()


def _diagnose_envelope() -> dict[str, object]:
    return {
        "intent_type": "diagnose",
        "step_type": "diagnose",
        "step_ref": {"session_id": "sess_1", "step_id": "step_diag", "step_type": "diagnose"},
        "artifact_id": "art_diag",
        "result": {
            "bundle_type": "diagnosis_bundle",
            "aoi_artifacts": [{"artifact_id": "art_decomp", "result": {"rows": [{"key": "A"}]}}],
            "diagnoses": [
                {
                    "drivers": [
                        {
                            "dimension": "region",
                            "decompose_ref": {
                                "session_id": "sess_1",
                                "step_id": "step_decomp",
                                "step_type": "decompose",
                                "artifact_id": "art_decomp",
                            },
                            "top_segment": {"key": "A", "absolute_contribution": 10.0},
                            "total_contribution": 10.0,
                            "total_contribution_share": 1.0,
                            "rows": [{"key": "A", "absolute_contribution": 10.0}],
                            "returned_row_count": 1,
                            "total_row_count": 1,
                            "is_truncated": False,
                            "issues": [],
                        }
                    ]
                }
            ],
        },
        "product_metadata": {"aoi_artifacts": [{"artifact_id": "art_decomp"}]},
    }


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


def _assert_aoi_slice_ref_schema_uses_filter(slice_schema: dict[str, Any]) -> None:
    assert slice_schema["additionalProperties"] is False
    assert set(slice_schema["properties"]) == {"time_scope", "filter"}
    properties = slice_schema["properties"]
    assert "filter_expression" not in properties
    assert "half-open [start, end)" in properties["time_scope"]["description"]
    assert "`filter` field" in properties["filter"]["description"]
    assert "`filter_expression`" in properties["filter"]["description"]
    assert "scope" not in properties


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


def test_terminate_session_tool_resolves_actor_from_current_user() -> None:
    server = FastMCP("test")
    runtime = RecordingTerminateRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["terminate_session"]

    token = current_user.set("alice")
    try:
        result = asyncio.run(tool.fn(session_id="sess_1", terminal_reason="analysis_complete"))
    finally:
        current_user.reset(token)

    assert result == {"data": None, "error": None}
    assert runtime.terminate_call == {
        "session_id": "sess_1",
        "actor": UserId("alice"),
        "terminal_reason": "analysis_complete",
    }
    assert "actor" not in tool.parameters["properties"]


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
    assert properties["current"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert properties["baseline"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert properties["grain"]["enum"] == ["hour", "day", "week", "month", "quarter", "year"]
    assert "AOI time granularity" in properties["grain"]["description"]
    assert "Current AOI slice" in properties["current"]["description"]
    assert "Baseline AOI slice" in properties["baseline"]["description"]
    _assert_aoi_slice_ref_schema_uses_filter(slice_schema)
    assert "hypothesis" in tools["test_intent"].parameters["required"]
    assert "grain" in tools["test_intent"].parameters["required"]
    assert properties["hypothesis"]["$ref"] == "#/$defs/McpTestHypothesis"
    assert "family is fixed internally" in properties["hypothesis"]["description"]
    assert hypothesis_schema["additionalProperties"] is False
    assert hypothesis_schema["required"] == ["alternative", "significance"]
    assert "family" not in hypothesis_schema["properties"]
    assert hypothesis_schema["properties"]["alternative"]["enum"] == [
        "two_sided",
        "greater",
        "less",
    ]
    assert (
        "current is greater than baseline"
        in hypothesis_schema["properties"]["alternative"]["description"]
    )
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
    assert "owns this intent call" in observe_props["session_id"]["description"]
    assert "Semantic metric identifier" in observe_props["metric"]["description"]
    assert "observed metric data slice" in observe_props["time_scope"]["description"]
    assert "without dimensions" in observe_props["granularity"]["description"]
    assert "without granularity" in observe_props["dimensions"]["description"]
    assert "non-empty dimension list" in observe_props["dimensions"]["description"]
    assert observe_props["dimensions"]["anyOf"][0]["minItems"] == 1
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


def test_detect_and_decompose_tool_schemas_document_aoi_parameters() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    detect = tools["detect"]
    detect_props = detect.parameters["properties"]
    assert "Detect anomaly candidates" in detect.description
    assert "owns this intent call" in detect_props["session_id"]["description"]
    assert "Semantic metric identifier" in detect_props["metric"]["description"]
    assert "AOI TimeScope" in detect_props["time_scope"]["description"]
    assert "time bucket granularity" in detect_props["granularity"]["description"]
    assert "Detection strategy" in detect_props["strategy"]["description"]
    assert "single dimension" in detect_props["dimension"]["description"]
    assert "Detection sensitivity" in detect_props["sensitivity"]["description"]
    assert detect_props["sensitivity"]["default"] == "aggressive"
    assert "Maximum anomaly candidates" in detect_props["limit"]["description"]
    assert detect_props["limit"]["anyOf"][0]["minimum"] == 1

    decompose = tools["decompose"]
    decompose_props = decompose.parameters["properties"]
    assert "Decompose the delta" in decompose.description
    assert "string artifact IDs" in decompose.description
    assert "owns this intent call" in decompose_props["session_id"]["description"]
    assert "compare artifact ID" in decompose_props["compare_artifact_id"]["description"]
    assert decompose_props["compare_artifact_id"]["minLength"] == 1
    assert "Dimension name" in decompose_props["dimension"]["description"]
    assert decompose_props["dimension"]["minLength"] == 1
    assert "Maximum top dimension values" in decompose_props["limit"]["description"]
    assert decompose_props["limit"]["anyOf"][0]["minimum"] == 1


def test_validate_hypothesis_schema_omits_fixed_family() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    properties = tools["validate"].parameters["properties"]
    slice_schema = tools["validate"].parameters["$defs"]["McpAoiSliceRef"]
    hypothesis_schema = tools["validate"].parameters["$defs"]["McpValidateHypothesis"]

    assert "method" not in properties
    assert properties["current"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert properties["baseline"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert properties["grain"]["enum"] == ["hour", "day", "week", "month", "quarter", "year"]
    assert "AOI time granularity" in properties["grain"]["description"]
    assert "Current AOI slice" in properties["current"]["description"]
    assert "Baseline AOI slice" in properties["baseline"]["description"]
    _assert_aoi_slice_ref_schema_uses_filter(slice_schema)
    assert "grain" in tools["validate"].parameters["required"]
    assert properties["hypothesis"]["anyOf"][0] == {"$ref": "#/$defs/McpValidateHypothesis"}
    assert "family defaults internally" in properties["hypothesis"]["description"]
    assert hypothesis_schema["additionalProperties"] is False
    assert "required" not in hypothesis_schema
    assert set(hypothesis_schema["properties"]) == {"alternative", "significance"}
    assert "family" not in hypothesis_schema["properties"]
    assert hypothesis_schema["properties"]["alternative"]["anyOf"][0]["enum"] == [
        "two_sided",
        "greater",
        "less",
    ]
    assert "Defaults to two_sided" in hypothesis_schema["properties"]["alternative"]["description"]
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

    assert properties["current"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert properties["baseline"]["$ref"] == "#/$defs/McpAoiSliceRef"
    assert "Current AOI slice" in properties["current"]["description"]
    assert "Baseline AOI slice" in properties["baseline"]["description"]
    _assert_aoi_slice_ref_schema_uses_filter(slice_schema)
    assert "known current-vs-baseline change" in properties["dimensions"]["description"]
    assert properties["dimensions"]["minItems"] == 1
    assert properties["decomposition_method"]["default"] == "delta_share"
    assert properties["decomposition_method"]["const"] == "delta_share"
    assert "Only delta_share is supported" in properties["decomposition_method"]["description"]
    assert properties["decomposition_limit"]["minimum"] == 1


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
            current=slice_ref,
            baseline=slice_ref,
            dimensions=["region"],
        )
    )

    assert result == {"data": {}, "error": None}
    assert calls["method"] == runtime.attribute
    assert "request" in calls["kwargs"]
    assert "params" not in calls["kwargs"]
    assert isinstance(calls["kwargs"]["request"], aoi.Attribute)
    assert calls["kwargs"]["request"].decomposition_limit == 5


def test_diagnose_schema_documents_auto_detect_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    diagnose = tools["diagnose"]
    properties = diagnose.parameters["properties"]

    assert "auto-detect anomaly diagnosis" in diagnose.description
    assert "detects anomalous candidates" in diagnose.description
    assert "mode" not in properties
    assert "current" not in properties
    assert "baseline" not in properties
    assert "candidate_dimensions" not in properties
    assert "dimensions" in properties
    assert "scan_dimension" in properties
    assert "exactly one metric" in properties["metric"]["description"]
    assert (
        "time range scanned by the internal detect step" in properties["time_scope"]["description"]
    )
    assert "time bucket granularity" in properties["granularity"]["description"]
    assert "AOI Expression" in properties["filter_expression"]["description"]
    assert "single dimension used only to split" in properties["scan_dimension"]["description"]
    assert "independent from attribution dimensions" in properties["scan_dimension"]["description"]
    assert "Required attribution dimensions" in properties["dimensions"]["description"]
    assert "independent from scan_dimension" in properties["dimensions"]["description"]
    assert "Detection strategy" in properties["strategy"]["description"]
    assert "Detection sensitivity" in properties["sensitivity"]["description"]
    assert "Maximum anomaly candidates" in properties["candidate_limit"]["description"]
    assert properties["candidate_limit"]["anyOf"][0]["minimum"] == 1
    assert "Maximum driver rows" in properties["decomposition_limit"]["description"]
    assert properties["decomposition_limit"]["anyOf"][0]["minimum"] == 1
    assert properties["include_details"]["default"] is False
    assert "full embedded AOI artifacts" in properties["include_details"]["description"]


def test_diagnose_tool_defaults_to_compact_response_and_can_include_details() -> None:
    server = FastMCP("test")
    runtime = RecordingDiagnoseRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["diagnose"]
    time_scope = McpTimeScope(
        field="event_time",
        start="2026-05-01T00:00:00Z",
        end="2026-05-08T00:00:00Z",
    )

    compact = asyncio.run(
        tool.fn(
            session_id="sess_1",
            metric="view_time",
            dimensions=["region"],
            strategy="point_anomaly",
            time_scope=time_scope,
            granularity="day",
        )
    )
    full = asyncio.run(
        tool.fn(
            session_id="sess_1",
            metric="view_time",
            dimensions=["region"],
            strategy="point_anomaly",
            time_scope=time_scope,
            granularity="day",
            include_details=True,
        )
    )

    compact_data = compact["data"]
    compact_driver = compact_data["result"]["diagnoses"][0]["drivers"][0]
    full_data = full["data"]
    full_driver = full_data["result"]["diagnoses"][0]["drivers"][0]

    assert runtime.diagnose_call is not None
    assert isinstance(runtime.diagnose_call["request"], aoi.Diagnose)
    assert compact_data["result"]["aoi_artifacts"] == []
    assert compact_data["product_metadata"]["aoi_artifacts"] == []
    assert "rows" not in compact_driver
    assert compact_driver["decompose_ref"]["artifact_id"] == "art_decomp"
    assert full_data["result"]["aoi_artifacts"]
    assert full_data["product_metadata"]["aoi_artifacts"]
    assert full_driver["rows"] == [{"key": "A", "absolute_contribution": 10.0}]


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
    assert "Correlation method" in correlate_props["method"]["description"]

    forecast_description = forecast_props["source_artifact_id"]["description"]
    assert "observe(time_series)" in forecast_description
    assert "granularity" in forecast_description
    assert "datasource" in forecast_description
    assert forecast_props["horizon"]["minimum"] == 1
    assert "future buckets" in forecast_props["horizon"]["description"]


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

    compare = tools["compare"]
    compare_type = compare.parameters["properties"]["compare_type"]
    current_description = compare.parameters["properties"]["current_artifact_id"]["description"]
    baseline_description = compare.parameters["properties"]["baseline_artifact_id"]["description"]

    assert compare_type["default"] == "normal"
    assert compare_type["enum"] == [
        "normal",
        "holiday_aligned",
        "weekday_aligned",
        "holiday_and_weekday_aligned",
    ]
    assert "scalar, segmented, or time_series" in current_description
    assert "dimensions=['log_hour']" in current_description
    assert "calendar-aligned compare types require time_series" in baseline_description


def test_compare_tool_passes_generated_request_for_segmented_artifact_ids(monkeypatch) -> None:
    calls = {}

    async def fake_call_runtime(method, /, **kwargs):
        calls["method"] = method
        calls["kwargs"] = kwargs
        return {"data": {}, "error": None}

    monkeypatch.setattr("marivo.transports.mcp.tools.intents.call_runtime", fake_call_runtime)

    server = FastMCP("test")
    runtime = FakeRuntime()
    register_tools(server, runtime, transport="stdio")
    tool = {tool.name: tool for tool in server._tool_manager.list_tools()}["compare"]

    result = asyncio.run(
        tool.fn(
            session_id="sess_1",
            current_artifact_id="art_segmented_left",
            baseline_artifact_id="art_segmented_right",
            compare_type="normal",
        )
    )

    assert result == {"data": {}, "error": None}
    assert calls["method"] == runtime.compare
    request = calls["kwargs"]["request"]
    assert isinstance(request, aoi.Compare)
    assert request.current_artifact_id == "art_segmented_left"
    assert request.baseline_artifact_id == "art_segmented_right"
    assert request.compare_type == "normal"


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
