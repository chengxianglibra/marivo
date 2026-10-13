"""Adversarial capability and catalog regression cases."""

from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
from marivo.datasource.authoring import DuckDBSpec
from marivo.datasource.capabilities import (
    provider_statement,
    provider_statement_log,
    render_provider_statement,
)
from marivo.datasource.engines import duckdb, postgres
from marivo.datasource.ir import JsonSourceIR
from marivo.datasource.json_source import _request_payload
from marivo.datasource.metadata import inspect_table


def test_render_does_not_reinterpret_slot_values() -> None:
    namespace = "x' OR 1=1 --"
    connection = ibis.duckdb.connect()
    try:
        connection.raw_sql(f'CREATE SCHEMA "{namespace}"')
        connection.raw_sql(f'CREATE TABLE "{namespace}".unrelated(id INTEGER)')
        connection.raw_sql(f'CREATE TABLE "{namespace}"."{{schema}}"(id INTEGER)')
        sql = render_provider_statement(
            provider_statement("duckdb", "duckdb.tables.columns"),
            duckdb.PROFILE,
            values={"database": "memory", "schema": namespace, "table": "{schema}"},
        )
        rows = connection.raw_sql(sql).fetchall()
        assert len(rows) == 1 and rows[0][0] == "id"
    finally:
        connection.disconnect()


@pytest.mark.parametrize("header", ["Authorization", "X-API-Key"])
def test_authenticated_get_does_not_follow_redirect(header: str) -> None:
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get(header)))
            if self.path == "/private/start":
                self.send_response(302)
                self.send_header("Location", "/outside")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"[]")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    backend = ibis.duckdb.connect()
    url = f"http://127.0.0.1:{server.server_port}/private/"
    backend._marivo_duckdb_http_auth = duckdb.DuckDbHttpCredentials(
        scope=url, headers=((header, "synthetic-token"),)
    )
    try:
        with pytest.raises(HTTPError) as error:
            _request_payload(backend, JsonSourceIR(path=url + "start"), {})
        assert error.value.code == 302
        assert seen == [("/private/start", "synthetic-token")]
    finally:
        backend.disconnect()
        server.shutdown()
        server.server_close()
        thread.join()


def test_duckdb_metadata_is_namespace_bound(tmp_path: Path) -> None:
    path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(path))
    try:
        backend.raw_sql("CREATE SCHEMA a; CREATE SCHEMA z")
        backend.raw_sql("CREATE TABLE a.orders(id INTEGER, payload INTEGER)")
        backend.raw_sql("CREATE TABLE z.orders(id VARCHAR PRIMARY KEY, payload VARCHAR NOT NULL)")
        backend.raw_sql("COMMENT ON TABLE a.orders IS 'target'")
        backend.raw_sql("COMMENT ON TABLE z.orders IS 'unrelated'")
        backend.raw_sql("INSERT INTO a.orders VALUES (1, 2), (1, NULL)")
    finally:
        backend.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(path)), project_root=tmp_path)
    metadata = inspect_table("warehouse", table="orders", database="a", project_root=tmp_path)
    assert metadata.comment == "target"
    assert metadata.primary_keys == ()
    assert metadata.unique_constraints == ()
    assert [column.type for column in metadata.columns] == ["INTEGER", "INTEGER"]
    assert [column.nullable for column in metadata.columns] == [True, True]
    assert [column.ordinal_position for column in metadata.columns] == [1, 2]
    assert metadata.physical_profile is not None
    assert metadata.physical_profile.row_count == 2


def test_postgres_groups_composite_constraints_by_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    def rows(
        backend: BaseBackend, statement_id: str, values: Mapping[str, object]
    ) -> tuple[dict[str, object], ...]:
        if statement_id == "postgres.constraints":
            return (
                {"constraint_id": 1, "constraint_kind": "u", "column_name": "b"},
                {"constraint_id": 1, "constraint_kind": "u", "column_name": "a"},
                {"constraint_id": 2, "constraint_kind": "u", "column_name": "c"},
            )
        if statement_id == "postgres.tables.kind":
            raise PermissionError("catalog denied")
        return ()

    monkeypatch.setattr(postgres, "_postgres_rows", rows)
    backend = ibis.duckdb.connect()
    try:
        metadata = postgres._inspect_postgres(
            datasource="probe",
            backend=backend,
            table="t",
            database="public",
            table_expr=ibis.table({"a": "int64", "b": "int64", "c": "int64"}),
            include_partitions=False,
            default_schema="public",
        )
        assert [item.columns for item in metadata.unique_constraints] == [("b", "a"), ("c",)]
        assert metadata.is_view is None
    finally:
        backend.disconnect()


def test_failed_secret_install_is_audited_without_parameter_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ibis.duckdb.connect()

    def fail(query: str, **kwargs: object) -> None:
        raise RuntimeError("synthetic-secret")

    monkeypatch.setattr(backend, "raw_sql", fail)
    try:
        with pytest.raises(RuntimeError):
            duckdb.http_credentials(
                backend,
                scope="https://example.invalid/private/",
                bearer_token="synthetic-secret",
                headers=None,
            )
        log = provider_statement_log(backend)
        assert len(log) == 1
        assert log[0].state == "failed"
        assert log[0].statement_id == "duckdb.http_secret_bearer"
        assert "synthetic-secret" not in repr(log)
    finally:
        backend.disconnect()
