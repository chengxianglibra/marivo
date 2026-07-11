"""Tests for JSON datasource file sources."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource.backends import apply_json_http_settings
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import JsonSourceIR

_EVENT_SCHEMA = {"event_id": "int64", "amount": "int64", "status": "string"}


class _RawSqlRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def raw_sql(self, sql: str) -> None:
        self.calls.append(sql)


def test_apply_json_http_settings_enables_force_download_for_remote_json() -> None:
    backend = _RawSqlRecorder()

    apply_json_http_settings(
        backend,
        JsonSourceIR(path="https://example.com/events.json", schema=(("event_id", "string"),)),
    )

    assert backend.calls == ["SET force_download=true"]


def test_apply_json_http_settings_ignores_local_http_prefixed_paths_and_non_json() -> None:
    backend = _RawSqlRecorder()

    apply_json_http_settings(
        backend,
        JsonSourceIR(path="http_exports/events.json", schema=(("event_id", "string"),)),
    )
    apply_json_http_settings(
        backend, md.csv("https://example.com/events.csv", schema={"event_id": "string"})
    )

    assert backend.calls == []


def test_apply_json_http_settings_teaches_when_backend_lacks_raw_sql() -> None:
    with pytest.raises(DatasourceMetadataError) as exc_info:
        apply_json_http_settings(
            object(),
            JsonSourceIR(path="https://example.com/events.json", schema=(("event_id", "string"),)),
        )

    message = str(exc_info.value)
    assert "http(s)" in message
    assert "not a generic HTTP API reader" in message
    assert exc_info.value.details == {
        "path": "https://example.com/events.json",
        "reason": "backend_lacks_httpfs",
        "location": "md.json('https://example.com/events.json')",
        "cause": "backend lacks callable raw_sql (DuckDB httpfs not available)",
        "fix_snippet": (
            "# Use a local path or glob pattern instead of an http(s):// URL:\n"
            "import marivo.datasource as md\n"
            'source = md.json("data/events/*.json", format="newline_delimited")'
        ),
    }


def test_apply_json_http_settings_rejects_non_callable_raw_sql() -> None:
    class _NonCallableRawSql:
        raw_sql = "not a method"

    with pytest.raises(DatasourceMetadataError) as exc_info:
        apply_json_http_settings(
            _NonCallableRawSql(),
            JsonSourceIR(path="https://example.com/events.json", schema=(("event_id", "string"),)),
        )

    assert exc_info.value.details["reason"] == "backend_lacks_httpfs"


def _write_ndjson_files(root: Path) -> str:
    data_dir = root / "data" / "events"
    data_dir.mkdir(parents=True)
    (data_dir / "events_a.json").write_text(
        "\n".join(
            [
                '{"event_id": 1, "amount": 10, "status": "paid"}',
                '{"event_id": 2, "amount": 20, "status": "void"}',
            ]
        )
        + "\n"
    )
    (data_dir / "events_b.json").write_text('{"event_id": 3, "amount": 30, "status": "paid"}\n')
    return str(data_dir / "*.json")


def _register_duckdb(project_root: Path) -> None:
    md.register(
        md.DuckDBSpec(name="warehouse", path=str(project_root / "warehouse.duckdb")),
        project_root=project_root,
    )


def _write_project_with_json_entity(
    project_root: Path, json_path: str, *, format: str | None = "newline_delimited"
) -> None:
    (project_root / "marivo.toml").write_text('[project]\nname = "test"\n')
    ds_dir = project_root / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    (ds_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse', path={str(project_root / 'warehouse.duckdb')!r})\n"
    )
    semantic_dir = project_root / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data Team')\n"
    )
    source_args = f"{json_path!r}, schema={_EVENT_SCHEMA!r}"
    if format is not None:
        source_args += f", format={format!r}"
    (semantic_dir / "events.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        f"events = ms.entity(name='events', datasource=warehouse, source=md.json({source_args}))\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum', unit='USD')\n"
    )


def test_verify_object_statically_validates_json_entity(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = project.verify_object(ms.ref("entity.sales.events"))

    assert result.status == "passed"
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scan")


def test_verify_object_statically_validates_json_entity_with_auto_format(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path, format=None)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = project.verify_object(ms.ref("entity.sales.events"))

    assert result.status == "passed"
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scan")


def test_loaded_json_project_materializes_metric(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path)

    from marivo.semantic.materializer import Materializer
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    materializer = Materializer(project, project._session_backend_factory())
    table = materializer.entity("sales.events")

    assert table.count().execute() == 3
