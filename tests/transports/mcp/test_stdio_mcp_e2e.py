"""Stdio MCP E2E test: verify marivo mcp server construction and tool registration."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from marivo.contracts.ids import SessionId


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


class FakeRuntime:
    """Minimal stub satisfying register_tools' runtime contract."""

    _services: dict[str, _FakeSvc] = {"semantic_v2": _FakeSvc(), "datasource": _FakeSvc()}

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

    def get_session_trace(self, **kw):
        return {
            "session_id": kw["session_id"],
            "goal": None,
            "lifecycle_status": "active",
            "created_at": "2026-05-18T00:00:00+00:00",
            "updated_at": "2026-05-18T00:00:00+00:00",
            "steps": [],
            "artifact_ids": [],
            "schema_version": "session_trace.v1",
        }

    def terminate_session(self, **kw):
        return {}

    def get_session_state(self, **kw):
        return {}

    def get_proposition_context(self, **kw):
        return {}


class RecordingSemanticSvc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_semantic_models(self, **kw):
        return {}

    def validate_osi_semantic_models(self, **kw):
        self.calls.append(("validate", kw))
        return {"valid": True}

    def import_osi_semantic_models(self, **kw):
        self.calls.append(("import", kw))
        return None

    def export_osi_semantic_models(self, **kw):
        self.calls.append(("export", kw))
        return {"version": "0.1.1", "semantic_model": []}

    def get_semantic_model(self, **kw):
        return {}

    def delete_semantic_model(self, **kw):
        self.calls.append(("delete", kw))
        return None


class RecordingDatasourceSvc(_FakeSvc):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def preview_table(self, **kw):
        self.calls.append(("preview", kw))
        return {"previewed": True}


class RecordingRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.semantic = RecordingSemanticSvc()
        self.datasource = RecordingDatasourceSvc()
        self._services = {"semantic_v2": self.semantic, "datasource": self.datasource}
        self.intent_calls: list[tuple[str, dict[str, Any]]] = []

    def validate(self, **kw):
        self.intent_calls.append(("validate", kw))
        return {"validated": True}


def _make_server(name: str) -> Any:
    from mcp.server.fastmcp import FastMCP

    return FastMCP(name)


def test_marivo_mcp_entry_point_callable():
    """The marivo mcp subcommand handler is callable."""
    from marivo.transports.cli.cmd_mcp import handle

    assert callable(handle)


def test_stdio_mcp_falls_back_to_system_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.delenv("MARIVO_USER", raising=False)

    assert _resolve_stdio_user() == getpass.getuser()


def test_stdio_mcp_falls_back_on_blank_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.setenv("MARIVO_USER", "   ")

    assert _resolve_stdio_user() == getpass.getuser()


def test_stdio_mcp_uses_explicit_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.setenv("MARIVO_USER", "  alice  ")

    assert _resolve_stdio_user() == "alice"


def test_stdio_server_registers_tools():
    """A stdio-configured FastMCP server registers tools without catalog/OpenAPI group."""
    from marivo.transports.mcp.tools import register_tools

    server = _make_server("marivo")  # Same name as cmd_mcp.py uses
    register_tools(server, FakeRuntime(), transport="stdio")

    tools = server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]

    # Catalog / OpenAPI tools must NOT be present in stdio mode
    for excluded in (
        "health_check",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
    ):
        assert excluded not in tool_names, f"Stdio mode should not expose {excluded}"

    # Verify all expected stdio tools are present
    expected_tools = [
        # Intent tools
        "observe",
        "compare",
        "decompose",
        "detect",
        "correlate",
        "forecast",
        "attribute",
        "diagnose",
        # Session tools
        "create_session",
        "list_sessions",
        "get_session",
        "get_session_trace",
        "terminate_session",
        "get_session_state",
        "get_proposition_context",
        # Semantic tools
        "list_semantic_models",
        "get_semantic_model",
        "validate_osi_semantic_models",
        "import_osi_semantic_models",
        "export_osi_semantic_models",
        "delete_semantic_model",
        # Datasource tools
        "list_datasources",
        "create_datasource",
        "get_datasource",
        "update_datasource",
        "delete_datasource",
        "browse_schemas",
        "browse_tables",
        "browse_columns",
        "preview_table",
    ]

    for tool_name in expected_tools:
        assert tool_name in tool_names, f"Missing tool: {tool_name}"


@pytest.mark.asyncio
async def test_stdio_get_session_trace_calls_runtime() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = FakeRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["get_session_trace"].run({"session_id": "sess_trace"})

    assert result == {
        "data": {
            "session_id": "sess_trace",
            "goal": None,
            "lifecycle_status": "active",
            "created_at": "2026-05-18T00:00:00+00:00",
            "updated_at": "2026-05-18T00:00:00+00:00",
            "steps": [],
            "artifact_ids": [],
            "schema_version": "session_trace.v1",
        },
        "error": None,
    }


@pytest.mark.asyncio
async def test_stdio_semantic_document_tools_support_local_json_files(tmp_path: Path):
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    doc = {"version": "0.1.1", "semantic_model": []}
    input_path = tmp_path / "semantic.json"
    output_path = tmp_path / "exported.json"
    input_path.write_text('{"version":"0.1.1","semantic_model":[]}', encoding="utf-8")

    await tools["validate_osi_semantic_models"].run({"input": {"input_path": str(input_path)}})
    import_result = await tools["import_osi_semantic_models"].run({"input": {"document": doc}})
    export_result = await tools["export_osi_semantic_models"].run(
        {"semantic_model_name": "commerce", "output_path": str(output_path)}
    )
    delete_result = await tools["delete_semantic_model"].run({"model": "commerce"})

    assert runtime.semantic.calls == [
        ("validate", {"doc_data": doc}),
        ("import", {"doc_data": doc}),
        ("export", {"semantic_model_name": "commerce"}),
        ("delete", {"name": "commerce", "owner_user": "test_user"}),
    ]
    assert output_path.read_text(encoding="utf-8") == (
        '{\n  "version": "0.1.1",\n  "semantic_model": []\n}\n'
    )
    assert export_result["data"]["output_path"] == str(output_path)
    assert import_result == {
        "data": {
            "status": "success",
            "message": "OSI semantic models imported successfully.",
        },
        "error": None,
    }
    assert delete_result == {"data": None, "error": None}


@pytest.mark.asyncio
async def test_stdio_preview_table_accepts_structured_filter_dict() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["preview_table"].run(
        {
            "datasource_id": "ds_test",
            "schema": "analytics",
            "table": "jobs",
            "filters": {"state": "FAILED"},
        }
    )

    assert result == {"data": {"previewed": True}, "error": None}
    assert runtime.datasource.calls == [
        (
            "preview",
            {
                "datasource_id": "ds_test",
                "schema_name": "analytics",
                "table_name": "jobs",
                "limit": 100,
                "filters": {"state": "FAILED"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_stdio_preview_table_omits_missing_filters() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    await tools["preview_table"].run(
        {
            "datasource_id": "ds_test",
            "schema": "analytics",
            "table": "jobs",
        }
    )

    assert runtime.datasource.calls == [
        (
            "preview",
            {
                "datasource_id": "ds_test",
                "schema_name": "analytics",
                "table_name": "jobs",
                "limit": 100,
            },
        )
    ]


@pytest.mark.asyncio
async def test_stdio_preview_table_rejects_filter_array() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    with pytest.raises(Exception, match="Input should be a valid dictionary"):
        await tools["preview_table"].run(
            {
                "datasource_id": "ds_test",
                "schema": "analytics",
                "table": "jobs",
                "filters": [{"column": "state", "value": "FAILED"}],
            }
        )


@pytest.mark.asyncio
async def test_stdio_validate_injects_fixed_hypothesis_family() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["validate"].run(
        {
            "session_id": "sess_test",
            "metric": "view_time",
            "current": {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-04-24T00:00:00Z",
                    "end": "2026-05-01T00:00:00Z",
                }
            },
            "grain": "day",
            "hypothesis": {"alternative": "greater", "significance": "balanced"},
        }
    )

    assert result == {"data": {"validated": True}, "error": None}
    assert runtime.intent_calls[0][0] == "validate"
    assert runtime.intent_calls[0][1]["session_id"] == "sess_test"
    request = runtime.intent_calls[0][1]["request"]
    assert request.model_dump(mode="json", exclude_none=True) == {
        "metric": "view_time",
        "current": {
            "time_scope": {
                "field": "log_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
            }
        },
        "baseline": {
            "time_scope": {
                "field": "log_time",
                "start": "2026-04-24T00:00:00Z",
                "end": "2026-05-01T00:00:00Z",
            }
        },
        "grain": "day",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "greater",
            "significance": "balanced",
        },
    }


@pytest.mark.asyncio
async def test_stdio_validate_preserves_partial_hypothesis_defaults() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["validate"].run(
        {
            "session_id": "sess_test",
            "metric": "view_time",
            "current": {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-04-24T00:00:00Z",
                    "end": "2026-05-01T00:00:00Z",
                }
            },
            "grain": "day",
            "hypothesis": {"alternative": "greater"},
        }
    )

    assert result == {"data": {"validated": True}, "error": None}
    assert runtime.intent_calls[0][1]["request"].model_dump(mode="json")["hypothesis"] == {
        "family": "two_sample_mean",
        "alternative": "greater",
        "significance": "balanced",
    }


@pytest.mark.asyncio
async def test_stdio_validate_rejects_hypothesis_family() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await tools["validate"].run(
            {
                "session_id": "sess_test",
                "metric": "view_time",
                "current": {
                    "time_scope": {
                        "field": "log_time",
                        "start": "2026-05-01T00:00:00Z",
                        "end": "2026-05-08T00:00:00Z",
                    }
                },
                "baseline": {
                    "time_scope": {
                        "field": "log_time",
                        "start": "2026-04-24T00:00:00Z",
                        "end": "2026-05-01T00:00:00Z",
                    }
                },
                "grain": "day",
                "hypothesis": {
                    "family": "two_sample_mean",
                    "alternative": "greater",
                    "significance": "balanced",
                },
            }
        )


@pytest.mark.asyncio
async def test_stdio_context_tools_use_local_canonical_evidence_repos(tmp_path: Path) -> None:
    from marivo.profiles.local import LocalConfig, create_local_runtime
    from marivo.transports.mcp.tools import register_tools

    _init_marivo_dir(tmp_path)
    runtime = create_local_runtime(LocalConfig(workspace_root=tmp_path))
    session = runtime.create_session(goal="stdio context tools")
    session_id = str(session.session_id)
    runtime.commit_artifact_with_extraction(
        SessionId(session_id),
        "step_stdio_context_compare",
        "compare_artifact",
        "stdio_context_compare",
        _scalar_compare_artifact(),
        step_type="compare",
        artifact_schema_version="v1",
    )
    proposition_id = runtime.metadata.query_one(
        "SELECT proposition_id FROM propositions WHERE session_id = ?",
        [session_id],
    )["proposition_id"]

    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    context_result = await tools["get_proposition_context"].run(
        {"session_id": session_id, "proposition_id": proposition_id}
    )
    state_result = await tools["get_session_state"].run(
        {
            "session_id": session_id,
            "slice": {},
            "proposition_types": ["change"],
            "origin_kinds": ["system_seeded"],
            "limit": 10,
        }
    )

    assert context_result["error"] is None
    assert context_result["data"]["proposition"]["proposition_id"] == proposition_id
    assert state_result["error"] is None
    assert state_result["data"]["active_propositions"][0]["proposition"]["proposition_id"] == (
        proposition_id
    )


def test_marivo_mcp_help_flag():
    """marivo mcp subcommand is registered and responds to --help."""
    marivo_bin = Path(sys.executable).parent / "marivo"
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(repo_root)
        if not env.get("PYTHONPATH")
        else f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
    )
    started = time.perf_counter()
    result = subprocess.run(
        [str(marivo_bin), "mcp", "--help"],
        capture_output=True,
        env=env,
        text=True,
        timeout=5,
    )
    elapsed = time.perf_counter() - started
    assert result.returncode == 0
    assert elapsed < 2.0


def _init_marivo_dir(root: Path) -> None:
    marivo = root / ".marivo"
    marivo.mkdir(exist_ok=True)
    (marivo / "models").mkdir(exist_ok=True)
    (marivo / "evidence").mkdir(exist_ok=True)
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        'profile = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )


def _scalar_compare_artifact() -> dict[str, Any]:
    left_window = {"field": "time", "start": "2026-05-01", "end": "2026-05-08"}
    right_window = {"field": "time", "start": "2026-04-24", "end": "2026-05-01"}
    return {
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "schema_version": "2.0",
        "metric": "revenue",
        "unit": "usd",
        "axes": [{"kind": "comparison_side"}],
        "subject": {"comparison_kind": "scalar"},
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "current_value": 120.0,
                            "baseline_value": 100.0,
                            "delta_abs": 20.0,
                            "delta_pct": 0.2,
                            "direction": "increase",
                        },
                    ],
                }
            ],
            "scope": {
                "current_value": 120.0,
                "baseline_value": 100.0,
                "delta_abs": 20.0,
                "delta_pct": 0.2,
                "direction": "increase",
            },
        },
        "current_ref": {"artifact_id": "art_left_observe"},
        "baseline_ref": {"artifact_id": "art_right_observe"},
        "resolved_input_summary": {
            "current_scope": {},
            "current_time_scope": left_window,
            "baseline_time_scope": right_window,
        },
    }
