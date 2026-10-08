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

import marivo.datasource as md
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.json_source import json_source_url, read_json_source

_EVENT_SCHEMA = {"event_id": "int64", "amount": "int64", "status": "string"}
_EVENT_COLUMNS = {name: name for name in _EVENT_SCHEMA}


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


def test_json_source_url_encodes_fixed_and_bound_query_values() -> None:
    source = md.json(
        "http://hawkeye.example/query_range/datasource/81?tenant=main",
        columns={"value": "value"},
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
        columns={"value": "value"},
        query_params={"start": md.source_param("start")},
    )

    with pytest.raises(ValueError, match=r"missing=\('start',\)"):
        json_source_url(source)
    with pytest.raises(ValueError, match=r"extra=\('end',\)"):
        json_source_url(source, {"start": 1, "end": 2})
    with pytest.raises(TypeError, match="must be str, int, float, or bool"):
        json_source_url(source, {"start": [1, {"nested": True}]})  # type: ignore[dict-item]


def test_json_source_url_encodes_list_query_value() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"specificsource": ["app-1", "app-2"], "page": 1},
    )

    assert json_source_url(source) == (
        "https://api.example/query?specificsource=app-1&specificsource=app-2&page=1"
    )


def test_json_source_url_binds_list_source_param() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"specificsource": md.source_param("apps")},
    )

    assert json_source_url(source, {"apps": ["app-1", "app-2"]}) == (
        "https://api.example/query?specificsource=app-1&specificsource=app-2"
    )


def test_json_source_url_resolves_source_params_inside_list() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"specificsource": ["app-1", md.source_param("second")]},
    )

    assert json_source_url(source, {"second": "app-2"}) == (
        "https://api.example/query?specificsource=app-1&specificsource=app-2"
    )


def test_json_source_url_rejects_non_scalar_list_items() -> None:
    with pytest.raises(TypeError, match="must contain str, int, float, bool"):
        md.json(
            "https://api.example/query",
            columns={"value": "value"},
            query_params={"specificsource": ["app-1", ["nested"]]},  # type: ignore[dict-item]
        )


def test_json_source_url_rejects_nested_list_binding() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"specificsource": md.source_param("apps")},
    )
    with pytest.raises(TypeError, match="must be flat"):
        json_source_url(source, {"apps": [["app-1", "app-2"]]})


def test_json_source_url_rejects_empty_list_binding() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"specificsource": md.source_param("apps")},
    )
    with pytest.raises(ValueError, match="empty list"):
        json_source_url(source, {"apps": []})


def test_post_json_source_binds_array_body_value() -> None:
    response = {"data": {"change_infos": [{"change_id": 101}]}}
    with _post_json_server(response) as (url, requests):
        backend = ibis.duckdb.connect(":memory:")
        source = md.json(
            url,
            columns={"change_id": "change_id"},
            method="POST",
            body={
                "specific_source": md.source_param("apps"),
                "page_num": 1,
            },
            records_path="$.data.change_infos",
        )

        try:
            table = read_json_source(
                backend,
                source,
                source_params={"apps": ["app-1", "app-2"]},
            )
            assert table.execute().to_dict(orient="records") == [{"change_id": 101}]
        finally:
            backend.disconnect()

    assert requests[0]["body"] == {
        "specific_source": ["app-1", "app-2"],
        "page_num": 1,
    }


def test_post_json_source_uses_bound_session_batches() -> None:
    response = {"data": {"change_infos": [{"change_id": 101}]}}
    with _post_json_server(response) as (url, requests):
        datasource = DatasourceIR(
            semantic_id="source",
            name="source",
            backend_type="duckdb",
            fields={},
            env_refs={},
            ai_context=AiContextIR(),
            python_symbol="source",
            location=DatasourceSourceLocation("source.py", 1),
        )
        source = md.json(
            url,
            columns={"change_id": "change_id"},
            method="POST",
            body={"specific_source": md.source_param("apps")},
            records_path="$.data.change_infos",
        )
        backend = ibis.duckdb.connect(":memory:")
        with SourceSession(provider_for("duckdb"), datasource, backend) as session:
            binding = session.bind(
                source,
                source_identity="http-json",
                source_params={"apps": ["app-1", "app-2"]},
            )
            assert session.collect_bounded(
                binding.relation,
                source_identities=("http-json",),
                purpose="authoring.sample",
                max_rows=2,
            ).to_pylist() == [{"change_id": 101}]
            assert session.submissions[-1].state == "succeeded"
    assert len(requests) == 1


def test_json_source_url_validates_parameters_declared_only_in_post_body() -> None:
    source = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        method="POST",
        body={"page": md.source_param("page_num")},
    )

    with pytest.raises(ValueError, match=r"missing=\('page_num',\)"):
        json_source_url(source)
    assert json_source_url(source, {"page_num": 2}) == "https://api.example/query"


def test_json_source_url_rejects_query_name_declared_twice() -> None:
    source = md.json(
        "https://api.example/query?step=30s",
        columns={"value": "value"},
        query_params={"step": "60s"},
    )

    with pytest.raises(ValueError, match="declared both in path and query_params"):
        json_source_url(source)


def test_post_json_source_binds_body_values_without_credentials() -> None:
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
        source = md.json(
            url,
            columns={"change_id": "change_id", "title": "title"},
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
            assert len(requests) == 1
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
    assert "x-secretid" not in normalized_headers
    assert "x-signature" not in normalized_headers
    assert normalized_headers["content-type"] == "application/json"


def test_post_json_source_preserves_literal_objects_that_resemble_parameter_markers() -> None:
    literal = {"kind": "source_param", "name": "literal_value"}
    with _post_json_server([{"id": "ok"}]) as (url, requests):
        backend = ibis.duckdb.connect(":memory:")
        source = md.json(
            url,
            columns={"id": "id"},
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


def test_parameterized_json_inspection_does_not_probe_remote_source_without_runtime_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    monkeypatch.chdir(tmp_path)
    _register_duckdb(tmp_path)
    source = md.json(
        "https://api.invalid/query",
        columns={"value": "value"},
        query_params={"start": md.source_param("start")},
    )

    inspection = md.inspect(md.DuckDBSpec(name="warehouse").ref, source)

    assert tuple((column.name, column.type) for column in inspection.schema) == (
        ("value", "unknown"),
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
    source_args = f"{json_path!r}, columns={_EVENT_COLUMNS!r}"
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
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all(), unit='USD')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum', unit='USD')\n"
    )


def test_load_and_require_json_entity(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = ms.SemanticCatalog(project).require(ms.ref.entity("sales.events"))

    assert result.kind.value == "entity"
    assert result.ref == ms.ref.entity("sales.events")


def test_load_and_require_json_entity_with_auto_format(tmp_path: Path) -> None:
    source_path = _write_ndjson_files(tmp_path)
    _write_project_with_json_entity(tmp_path, source_path, format=None)

    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    result = ms.SemanticCatalog(project).require(ms.ref.entity("sales.events"))

    assert result.kind.value == "entity"
    assert result.ref == ms.ref.entity("sales.events")


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


def _write_nested_json(root: Path) -> str:
    path = root / "events.json"
    path.write_text(
        '{"result": {"items": ['
        '{"id": 1, "user": {"name": "alice"}, "tags": ["a", "b"]},'
        '{"id": 2, "user": {"name": "bob"}, "tags": ["c"]}'
        "]}}"
    )
    return str(path)


def _write_traversal_json(root: Path) -> str:
    path = root / "events.json"
    path.write_text(
        '{"result": {"items": ['
        '{"id": 1, "specificsource": [{"appid": 10, "name": "app-1"}, '
        '{"appid": 20, "name": "app-2"}]},'
        '{"id": 2, "specificsource": [{"appid": 30, "name": "app-3"}]}'
        "]}}"
    )
    return str(path)


def test_unpack_nested_object_member_path(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_nested_json(tmp_path),
        columns={"id": "id", "user_name": "user.name"},
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute().sort_values("id")
        assert result.to_dict(orient="records") == [
            {"id": 1, "user_name": "alice"},
            {"id": 2, "user_name": "bob"},
        ]
    finally:
        backend.disconnect()


def test_unpack_array_index_path(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_nested_json(tmp_path),
        columns={"id": "id", "first_tag": "tags[0]"},
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute().sort_values("id")
        assert result.to_dict(orient="records") == [
            {"id": 1, "first_tag": "a"},
            {"id": 2, "first_tag": "c"},
        ]
    finally:
        backend.disconnect()


def test_unpack_array_traversal_path(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_nested_json(tmp_path),
        columns={"id": "id", "tag": "tags[]"},
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute().sort_values(["id", "tag"])
        assert result.to_dict(orient="records") == [
            {"id": 1, "tag": "a"},
            {"id": 1, "tag": "b"},
            {"id": 2, "tag": "c"},
        ]
    finally:
        backend.disconnect()


def test_unpack_array_traversal_nested_member(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_traversal_json(tmp_path),
        columns={"id": "id", "app_name": "specificsource[].name"},
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute().sort_values(["id", "app_name"])
        assert result.to_dict(orient="records") == [
            {"id": 1, "app_name": "app-1"},
            {"id": 1, "app_name": "app-2"},
            {"id": 2, "app_name": "app-3"},
        ]
    finally:
        backend.disconnect()


def _write_traversal_gaps_json(root: Path) -> str:
    path = root / "events.json"
    path.write_text(
        '{"result": {"items": ['
        '{"id": 1, "specificsource": [{"name": "app-1"}]},'
        '{"id": 2, "specificsource": []},'
        '{"id": 3, "specificsource": null},'
        '{"id": 4}'
        "]}}"
    )
    return str(path)


def test_unpack_array_traversal_drops_missing_empty_and_null(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_traversal_gaps_json(tmp_path),
        columns={"id": "id", "app_name": "specificsource[].name"},
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute()
        assert result.to_dict(orient="records") == [
            {"id": 1, "app_name": "app-1"},
        ]
    finally:
        backend.disconnect()


def test_unpack_sibling_fields_share_one_array_traversal(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        _write_traversal_json(tmp_path),
        columns={
            "id": "id",
            "app_id": "specificsource[].appid",
            "app_name": "specificsource[].name",
        },
        records_path="$.result.items",
    )
    try:
        table = read_json_source(backend, source)
        result = table.execute().sort_values(["id", "app_id"])
        assert result.to_dict(orient="records") == [
            {"id": 1, "app_id": 10, "app_name": "app-1"},
            {"id": 1, "app_id": 20, "app_name": "app-2"},
            {"id": 2, "app_id": 30, "app_name": "app-3"},
        ]
    finally:
        backend.disconnect()


def test_json_field_paths_reject_invalid_nested_path() -> None:
    with pytest.raises(ValueError, match="must start with a field name"):
        md.json(
            "https://api.example/query",
            columns={"name": "[0].name"},
            records_path="$.items",
        )


def test_json_field_path_can_select_nested_root_record(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    path.write_text('{"user": {"name": "Ada"}}')
    backend = ibis.duckdb.connect(":memory:")
    try:
        table = read_json_source(backend, md.json(str(path), columns={"user_name": "user.name"}))
        assert table.execute().to_dict(orient="records") == [{"user_name": "Ada"}]
    finally:
        backend.disconnect()


def test_json_field_paths_reject_independent_array_traversals() -> None:
    with pytest.raises(ValueError, match="only one shared array path"):
        md.json(
            "events.json",
            columns={"tag": "tags[]", "app": "apps[].name"},
            records_path="$.items",
        )


def test_json_field_paths_reject_multiple_traversals_in_one_path() -> None:
    with pytest.raises(ValueError, match="more than one array traversal"):
        md.json(
            "events.json",
            columns={"name": "groups[].apps[].name"},
            records_path="$.items",
        )


def test_wrapped_json_keeps_literal_output_field_names(tmp_path: Path) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text('{"result": {"items": [{"x-y": "literal", "display name": "shown"}]}}')
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        columns={"x-y": "x-y", "display name": "display name"},
        records_path="$.result.items",
    )

    try:
        assert read_json_source(backend, source).execute().to_dict(orient="records") == [
            {"x-y": "literal", "display name": "shown"}
        ]
    finally:
        backend.disconnect()


def test_wrapped_json_records_fill_missing_fields_and_ignore_extra_fields(tmp_path: Path) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text(
        '{"result": {"items": ['
        '{"event_id": 1, "status": "paid", "extra": "ignored"},'
        '{"event_id": 2, "amount": 20, "status": "void", "metadata": null}'
        "]}}"
    )
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        columns=_EVENT_COLUMNS,
        records_path="$.result.items",
    )

    try:
        table = read_json_source(backend, source)
        result = table.execute()
        assert tuple(result.columns) == tuple(sorted(_EVENT_COLUMNS))
        assert table.filter(table.amount.isnull()).count().execute() == 1
        assert result.loc[result["event_id"] == 2, "amount"].iloc[0] == 20
    finally:
        backend.disconnect()


def test_wrapped_json_types_are_inferred_from_returned_values(tmp_path: Path) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text(
        '{"result": {"items": [{"event_id": "not-an-int", "amount": 10, "status": "paid"}]}}'
    )
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        columns={"event_id": "event_id", "amount": "amount", "status": "status"},
        records_path="$.result.items",
    )

    try:
        table = read_json_source(backend, source)
        result = table.execute()
        assert result["event_id"].tolist() == ["not-an-int"]
        assert str(table.event_id.type()) == "string"
    finally:
        backend.disconnect()


def test_wrapped_json_empty_records_array_materializes_zero_rows(tmp_path: Path) -> None:
    source_path = tmp_path / "events.json"
    source_path.write_text('{"code": 0, "result": {"items": []}}')
    backend = ibis.duckdb.connect(":memory:")
    source = md.json(
        str(source_path),
        columns={"event_id": "event_id", "amount": "amount", "status": "status"},
        records_path="$.result.items",
    )

    try:
        with pytest.raises(ValueError, match="Cannot infer a physical type"):
            read_json_source(backend, source)
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
        columns={"event_id": "event_id", "amount": "amount", "status": "status"},
        records_path="$.result.items",
    )

    try:
        with pytest.raises(
            ValueError,
            match=(
                r"md\.json records_path '\$\.result\.items' did not resolve to an array; "
                r"verify the response envelope and API authentication"
            ),
        ):
            read_json_source(backend, source)
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
        columns={"event_id": "event_id", "amount": "amount", "status": "status"},
        records_path="$.result.items",
    )

    inspection = md.inspect(md.DuckDBSpec(name="warehouse").ref, source)
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=tuple(_EVENT_SCHEMA),
        refresh=True,
    )

    assert tuple(column.name for column in inspection.schema) == tuple(sorted(_EVENT_SCHEMA))
    assert snapshot.coverage.retained_row_count == 2
    assert snapshot.profiles[0].sample_distinct_count == 2
