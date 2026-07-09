"""Tests for JSON datasource file sources."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource.backends import apply_json_http_settings
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import JsonSourceIR


class _RawSqlRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def raw_sql(self, sql: str) -> None:
        self.calls.append(sql)


def test_apply_json_http_settings_enables_force_download_for_remote_json() -> None:
    backend = _RawSqlRecorder()

    apply_json_http_settings(backend, JsonSourceIR(path="https://example.com/events.json"))

    assert backend.calls == ["SET force_download=true"]


def test_apply_json_http_settings_ignores_local_http_prefixed_paths_and_non_json() -> None:
    backend = _RawSqlRecorder()

    apply_json_http_settings(backend, JsonSourceIR(path="http_exports/events.json"))
    apply_json_http_settings(backend, md.csv("https://example.com/events.csv"))

    assert backend.calls == []


def test_apply_json_http_settings_teaches_when_backend_lacks_raw_sql() -> None:
    with pytest.raises(DatasourceMetadataError) as exc_info:
        apply_json_http_settings(object(), JsonSourceIR(path="https://example.com/events.json"))

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
            _NonCallableRawSql(), JsonSourceIR(path="https://example.com/events.json")
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


def test_inspect_table_reads_json_file_source_schema(tmp_path: Path) -> None:
    source = md.json(_write_ndjson_files(tmp_path), format="newline_delimited")
    _register_duckdb(tmp_path)

    metadata = md.inspect_table(md.ref("datasource.warehouse"), source, project_root=tmp_path)

    assert metadata.table == "events"
    assert [column.name for column in metadata.columns] == ["event_id", "amount", "status"]
    assert metadata.backend_type == "duckdb"


def test_inspect_table_reads_json_with_auto_format(tmp_path: Path) -> None:
    source = md.json(_write_ndjson_files(tmp_path))
    _register_duckdb(tmp_path)

    metadata = md.inspect_table(md.ref("datasource.warehouse"), source, project_root=tmp_path)

    assert [column.name for column in metadata.columns] == ["event_id", "amount", "status"]
    assert metadata.backend_type == "duckdb"


def test_discover_measures_reads_json_and_preserves_column_inspection_source(
    tmp_path: Path,
) -> None:
    source = md.json(_write_ndjson_files(tmp_path), format="newline_delimited")
    _register_duckdb(tmp_path)

    result = md.discover_measures(
        md.ref("datasource.warehouse"),
        source,
        columns=("amount",),
        project_root=tmp_path,
    )

    assert result.source == source
    assert result.source.kind == "json"
    assert [candidate.column for candidate in result.columns] == ["amount"]


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
    source_args = repr(json_path)
    if format is not None:
        source_args += f", format={format!r}"
    (semantic_dir / "events.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        f"events = ms.entity(name='events', datasource=warehouse, source=ms.json({source_args}))\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum', unit='USD')\n"
    )


def test_verify_object_reads_json_entity(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = project.verify_object(ms.ref("entity.sales.events"), scope=md.ScanScope(max_rows=10))

    assert result.status == "passed"
    assert result.scan is not None
    assert result.scan.rows_scanned == 3


def test_verify_object_reads_json_entity_with_auto_format(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path, format=None)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = project.verify_object(ms.ref("entity.sales.events"), scope=md.ScanScope(max_rows=10))

    assert result.status == "passed"
    assert result.scan is not None
    assert result.scan.rows_scanned == 3


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


def test_json_missing_keys_are_visible_as_nullable_columns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.json").write_text('{"event_id": 1, "amount": 10}\n')
    (data_dir / "b.json").write_text('{"event_id": 2, "status": "paid"}\n')
    _register_duckdb(tmp_path)

    metadata = md.inspect_table(
        md.ref("datasource.warehouse"),
        md.json(str(data_dir / "*.json"), format="newline_delimited"),
        project_root=tmp_path,
    )

    assert [column.name for column in metadata.columns] == ["event_id", "amount", "status"]


def test_json_type_conflict_surfaces_json_typed_column(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.json").write_text('{"event_id": 1, "payload": {"kind": "object"}}\n')
    (data_dir / "b.json").write_text('{"event_id": 2, "payload": "text"}\n')
    _register_duckdb(tmp_path)

    metadata = md.inspect_table(
        md.ref("datasource.warehouse"),
        md.json(str(data_dir / "*.json"), format="newline_delimited"),
        project_root=tmp_path,
    )

    payload = next(column for column in metadata.columns if column.name == "payload")
    assert "json" in payload.type.lower()


@pytest.mark.skipif(
    os.environ.get("MARIVO_TEST_HTTP_JSON") != "1",
    reason="set MARIVO_TEST_HTTP_JSON=1 to run the live DuckDB httpfs JSON smoke test",
)
def test_http_json_source_smoke_with_force_download(tmp_path: Path) -> None:
    _register_duckdb(tmp_path)

    metadata = md.inspect_table(
        md.ref("datasource.warehouse"),
        md.json("https://jsonplaceholder.typicode.com/todos/1"),
        project_root=tmp_path,
    )

    assert {"id", "title", "completed"} <= {column.name for column in metadata.columns}
