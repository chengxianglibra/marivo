"""Tests for JSON datasource file sources."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import ibis
import pytest
from duckdb import InvalidInputException

import marivo.datasource as md
from marivo.datasource import backends as datasource_backends
from marivo.datasource.backends import apply_json_http_settings
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import JsonSourceIR
from marivo.datasource.json_source import json_source_url, read_json_source

_EVENT_SCHEMA = {"event_id": "int64", "amount": "int64", "status": "string"}


@contextmanager
def _post_json_server(
    response_body: object,
) -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            payload = json.dumps(response_body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/change-focus/api/v2/change/list", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
    assert "remote GET and JSON-body POST" in message
    assert exc_info.value.location == "md.json('https://example.com/events.json')"
    assert exc_info.value.repair is not None


def test_apply_json_http_settings_rejects_non_callable_raw_sql() -> None:
    class _NonCallableRawSql:
        raw_sql = "not a method"

    with pytest.raises(DatasourceMetadataError) as exc_info:
        apply_json_http_settings(
            _NonCallableRawSql(),
            JsonSourceIR(path="https://example.com/events.json", schema=(("event_id", "string"),)),
        )

    assert exc_info.value.received == "backend without raw_sql"


def test_json_source_url_encodes_fixed_and_bound_query_values() -> None:
    source = md.json(
        "http://hawkeye.example/query_range/datasource/81?tenant=main",
        schema={"value": "float64"},
        query_params={
            "query": 'sum(metric{q1=~"a|b"}) by (cluster, q1)',
            "start": md.source_param("start"),
            "end": md.source_param("end"),
            "step": "60s",
        },
    )

    assert json_source_url(source, {"start": "now-3600", "end": "now"}) == (
        "http://hawkeye.example/query_range/datasource/81?"
        "tenant=main&query=sum%28metric%7Bq1%3D~%22a%7Cb%22%7D%29+by+%28cluster%2C+q1%29"
        "&start=now-3600&end=now&step=60s"
    )


def test_json_source_url_requires_exact_declared_runtime_bindings() -> None:
    source = md.json(
        "https://api.example/query",
        schema={"value": "float64"},
        query_params={"start": md.source_param("start")},
    )

    with pytest.raises(ValueError, match=r"missing=\('start',\)"):
        json_source_url(source)
    with pytest.raises(ValueError, match=r"extra=\('end',\)"):
        json_source_url(source, {"start": 1, "end": 2})
    with pytest.raises(TypeError, match="must be str, int, float, or bool"):
        json_source_url(source, {"start": [1]})  # type: ignore[dict-item]


def test_json_source_url_validates_parameters_declared_only_in_post_body() -> None:
    source = md.json(
        "https://api.example/query",
        schema={"value": "float64"},
        method="POST",
        body={"page": md.source_param("page_num")},
    )

    with pytest.raises(ValueError, match=r"missing=\('page_num',\)"):
        json_source_url(source)
    assert json_source_url(source, {"page_num": 2}) == "https://api.example/query"


def test_json_source_url_rejects_query_name_declared_twice() -> None:
    source = md.json(
        "https://api.example/query?step=30s",
        schema={"value": "float64"},
        query_params={"step": "60s"},
    )

    with pytest.raises(ValueError, match="declared both in path and query_params"):
        json_source_url(source)


def test_post_json_source_binds_body_values_and_sends_multiple_scoped_headers() -> None:
    response = {
        "data": {
            "change_infos": [
                {"change_id": 101, "title": "first"},
                {"change_id": 102, "title": "second"},
            ]
        }
    }
    with _post_json_server(response) as (url, requests):
        backend = ibis.duckdb.connect(":memory:")
        auth = datasource_backends._configure_duckdb_http_auth(
            backend,
            scope=url,
            bearer_token=None,
            headers={
                "x-secretid": "secret-id",
                "x-signature": "secret-signature",
            },
        )
        backend.__dict__["_marivo_duckdb_http_auth"] = auth
        source = md.json(
            url,
            schema={"change_id": "int64", "title": "string"},
            method="POST",
            body={
                "platform_id": 1,
                "specific_source": [md.source_param("app_id")],
                "page_num": md.source_param("page_num"),
                "page_size": 100,
            },
            records_path="$.data.change_infos",
        )

        try:
            table = read_json_source(
                backend,
                source,
                source_params={"app_id": "app-42", "page_num": 3},
            )
            assert requests == []
            assert table.execute().to_dict(orient="records") == [
                {"change_id": 101, "title": "first"},
                {"change_id": 102, "title": "second"},
            ]
        finally:
            backend.disconnect()

    assert len(requests) == 1
    assert requests[0]["path"] == "/change-focus/api/v2/change/list"
    assert requests[0]["body"] == {
        "platform_id": 1,
        "specific_source": ["app-42"],
        "page_num": 3,
        "page_size": 100,
    }
    headers = requests[0]["headers"]
    assert isinstance(headers, dict)
    normalized_headers = {str(name).lower(): value for name, value in headers.items()}
    assert normalized_headers["x-secretid"] == "secret-id"
    assert normalized_headers["x-signature"] == "secret-signature"
    assert normalized_headers["content-type"] == "application/json"


def test_post_json_source_preserves_literal_objects_that_resemble_parameter_markers() -> None:
    literal = {"kind": "source_param", "name": "literal_value"}
    with _post_json_server([{"id": "ok"}]) as (url, requests):
        backend = ibis.duckdb.connect(":memory:")
        source = md.json(
            url,
            schema={"id": "string"},
            method="POST",
            body={"filter": literal},
        )

        try:
            assert read_json_source(backend, source).execute().to_dict(orient="records") == [
                {"id": "ok"}
            ]
        finally:
            backend.disconnect()

    assert requests[0]["body"] == {"filter": literal}
    assert source.body_params == ()


def test_parameterized_json_inspection_uses_declared_schema_without_runtime_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    monkeypatch.chdir(tmp_path)
    _register_duckdb(tmp_path)
    source = md.json(
        "https://api.invalid/query",
        schema={"value": "float64"},
        query_params={"start": md.source_param("start")},
    )

    inspection = md.inspect(md.DuckDBSpec(name="warehouse").ref, source)

    assert tuple((column.name, column.type) for column in inspection.schema) == (
        ("value", "float64"),
    )


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
    project_root: Path,
    json_path: str,
    *,
    format: str | None = "newline_delimited",
    records_path: str | None = None,
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
    if records_path is not None:
        source_args += f", records_path={records_path!r}"
    (semantic_dir / "events.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
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

    result = ms.SemanticCatalog(project).verify(ms.ref.entity("sales.events"))

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

    result = ms.SemanticCatalog(project).verify(ms.ref.entity("sales.events"))

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


def _write_wrapped_json(root: Path) -> str:
    path = root / "events.json"
    path.write_text(
        '{"code": 0, "result": {"items": ['
        '{"event_id": 1, "amount": 10, "status": "paid"},'
        '{"event_id": 2, "amount": 20, "status": "void"}'
        "]}}"
    )
    return str(path)


def test_loaded_wrapped_json_project_materializes_records(tmp_path: Path) -> None:
    source_path = _write_wrapped_json(tmp_path)
    _write_project_with_json_entity(
        tmp_path,
        source_path,
        format=None,
        records_path="$.result.items",
    )

    from marivo.semantic.materializer import Materializer
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    materializer = Materializer(project, project._session_backend_factory())

    assert materializer.entity("sales.events").execute().to_dict(orient="records") == [
        {"event_id": 1, "amount": 10, "status": "paid"},
        {"event_id": 2, "amount": 20, "status": "void"},
    ]


def test_wrapped_json_empty_records_array_materializes_zero_rows(tmp_path: Path) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text('{"code": 0, "result": {"items": []}}')
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        schema=_EVENT_SCHEMA,
        records_path="$.result.items",
    )

    try:
        assert read_json_source(backend, source).count().execute() == 0
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "payload",
    [
        '{"code": -2, "message": "token is empty"}',
        '{"code": 0, "result": {"items": {}}}',
    ],
)
def test_wrapped_json_invalid_records_path_fails_closed(tmp_path: Path, payload: str) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text(payload)
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        schema=_EVENT_SCHEMA,
        records_path="$.result.items",
    )

    try:
        table = read_json_source(backend, source)
        with pytest.raises(
            InvalidInputException,
            match=(
                r"records_path '\$\.result\.items' did not resolve to an array; "
                r"verify the response envelope and API authentication"
            ),
        ):
            table.execute()
    finally:
        backend.disconnect()


def test_wrapped_json_inspection_and_sample_use_record_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _write_wrapped_json(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    monkeypatch.chdir(tmp_path)
    backend = ibis.duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    backend.disconnect()
    _register_duckdb(tmp_path)
    source = md.json(
        source_path,
        schema=_EVENT_SCHEMA,
        records_path="$.result.items",
    )

    inspection = md.inspect(md.DuckDBSpec(name="warehouse").ref, source)
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=tuple(_EVENT_SCHEMA),
        refresh=True,
    )

    assert tuple(column.name for column in inspection.schema) == tuple(_EVENT_SCHEMA)
    assert snapshot.coverage.retained_row_count == 2
    assert snapshot.profiles[0].sample_distinct_count == 2
