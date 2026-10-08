"""Owned source fixtures and portable assertion receipts."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.datasource.adapters import SourceIR, SourceSession, provider_for
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from tests.support import json as freeze

PROFILES: dict[str, tuple[str, ...]] = {
    "duckdb": (
        "table",
        "view",
        "csv",
        "parquet",
        "local-json",
        "http-json-public",
        "http-json-auth",
    ),
    "postgres": ("table", "view", "namespace-table", "namespace-view"),
    "mysql": ("innodb-table", "view"),
    "sqlite": ("main-table", "main-view"),
    "trino": ("iceberg", "non-iceberg"),
    "clickhouse": ("mergetree", "distributed"),
}

ROWS: list[dict[str, object]] = [
    {"id": 9007199254740992, "amount": 2, "tenant": "a", "revision": 1},
    {"id": 9007199254740993, "amount": None, "tenant": "a", "revision": 2},
    {"id": 9007199254740993, "amount": 4, "tenant": "b", "revision": 1},
]
FLAGS = {
    "postgres": "MARIVO_POSTGRES_ANALYSIS_TEST",
    "mysql": "MARIVO_MYSQL_ANALYSIS_TEST",
    "trino": "MARIVO_TRINO_ANALYSIS_TEST",
    "clickhouse": "MARIVO_CLICKHOUSE_ANALYSIS_TEST",
}


def datasource(
    backend: str, fields: dict[str, object], env: dict[str, str] | None = None
) -> DatasourceIR:
    return DatasourceIR(
        semantic_id="source_profile",
        name="source_profile",
        backend_type=backend,
        fields=fields,
        env_refs=env or {},
        ai_context=AiContextIR(),
        python_symbol="source_profile",
        location=DatasourceSourceLocation("source_profile.py", 1),
    )


@dataclass
class Case:
    session: SourceSession
    source: SourceIR
    environment: dict[str, object]
    params: dict[str, int] | None = None


@dataclass(frozen=True)
class SourceData:
    columns: str
    values: str
    clickhouse_columns: str
    rows: list[dict[str, object]]
    sqlite_type_map: dict[str, str] | None = None


@contextmanager
def json_server(
    auth: bool, rows: list[dict[str, object]] | None = None
) -> Iterator[tuple[str, list[tuple[str, bool]]]]:
    seen: list[tuple[str, bool]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            authorized = self.headers.get("Authorization") == "Bearer synthetic-source_profile"
            seen.append((self.path, authorized))
            if auth and self.path.startswith("/private/") and not authorized:
                self.send_response(401)
                self.end_headers()
                return
            if self.path.startswith("/private/redirect"):
                self.send_response(302)
                self.send_header("Location", "/outside")
                self.end_headers()
                return
            payload = json.dumps(ROWS if rows is None else rows, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def source_case(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: SourceData | None = None,
) -> Iterator[Case]:
    if backend in FLAGS:
        flag = "MARIVO_CLICKHOUSE_CLUSTER_TEST" if profile == "distributed" else FLAGS[backend]
        if os.environ.get(flag) != "1":
            pytest.skip(f"Explicit ready service required: {flag}=1")
    name = "source_profile_" + uuid4().hex
    columns = "id BIGINT, amount BIGINT, tenant VARCHAR(10), revision BIGINT"
    values = "(9007199254740992,2,'a',1),(9007199254740993,NULL,'a',2),(9007199254740993,4,'b',1)"
    rows = ROWS
    ch_columns = "id Int64, amount Nullable(Int64), tenant LowCardinality(String), revision Int64"
    if data is not None:
        columns, values, rows, ch_columns = (
            data.columns,
            data.values,
            data.rows,
            data.clickhouse_columns,
        )
    if backend in {"duckdb", "sqlite"}:
        path = root / f"source.{backend}"
        admin = ibis.duckdb.connect(path) if backend == "duckdb" else ibis.sqlite.connect(path)
        try:
            admin.raw_sql(f"CREATE TABLE {name} ({columns})")
            admin.raw_sql(f"INSERT INTO {name} VALUES {values}")
            admin.raw_sql(f"CREATE VIEW {name}_view AS SELECT * FROM {name}")
            if backend == "sqlite":
                admin.con.commit()
        finally:
            admin.disconnect()
        fields: dict[str, object] = {"path": str(path), "read_only": True}
        if backend == "sqlite" and data is not None and data.sqlite_type_map is not None:
            fields["type_map"] = data.sqlite_type_map
        ds = datasource(backend, fields)
        with provider_for(backend).open(ds) as session:
            environment: dict[str, object] = {"backend": backend, "read_only": True}
            if profile.startswith("http-json"):
                from urllib.error import HTTPError

                auth = profile.endswith("auth")
                with json_server(auth, rows if data is not None else None) as (url, seen):
                    if auth:
                        session.close()
                        monkeypatch.setenv(
                            "MARIVO_SOURCE_PROFILE_HTTP_TOKEN", "synthetic-source_profile"
                        )
                        ds = datasource(
                            backend,
                            {"path": str(path), "read_only": True, "http_scope": url + "/private/"},
                            {"http_bearer_token": "MARIVO_SOURCE_PROFILE_HTTP_TOKEN"},
                        )
                    with (
                        provider_for(backend).open(ds)
                        if auth
                        else nullcontext(session) as http_session
                    ):
                        if auth:
                            with pytest.raises(HTTPError) as failure:
                                http_session.bind(
                                    JsonSourceIR(url + "/private/redirect"),
                                    source_identity="redirect",
                                )
                            assert failure.value.code == 302
                            assert not any(path == "/outside" for path, _ in seen)
                            http_session.bind(
                                JsonSourceIR(url + "/outside"), source_identity="outside"
                            )
                            assert seen[-1] == ("/outside", False)
                        http_source = JsonSourceIR(
                            url + "/private/data", query_params=(("page", SourceParamIR("page")),)
                        )
                        environment["http_requests"] = seen
                        yield Case(http_session, http_source, environment, {"page": 7})
                        assert seen[-1] == ("/private/data?page=7", auth)
                        environment["http_requests"] = [
                            [path, authorized] for path, authorized in seen
                        ]
                return
            source: SourceIR = TableSourceIR(name + ("_view" if "view" in profile else ""))
            if profile == "csv":
                file = root / "facts.csv"
                file.write_text(
                    "id,amount,tenant,revision\n9007199254740992,2,a,1\n9007199254740993,,a,2\n9007199254740993,4,b,1\n"
                )
                source = CsvSourceIR(str(file))
            elif profile == "parquet":
                file = root / "facts.parquet"
                pq.write_table(pa.Table.from_pylist(ROWS), file)
                source = ParquetSourceIR(str(file))
            elif profile == "local-json":
                file = root / "facts.json"
                file.write_text(json.dumps(ROWS))
                source = JsonSourceIR(str(file))
            yield Case(session, source, environment)
        return
    if backend == "postgres":
        from psycopg import sql

        from tests.datasource.environment import postgres_analysis as pg

        environment = pg.setup()
        monkeypatch.setenv("MARIVO_SOURCE_PROFILE_PASSWORD", pg.password())
        with pg.connection(admin=True) as admin:
            schema = name if profile.startswith("namespace") else "public"
            try:
                if schema != "public":
                    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                    admin.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA {} TO analysis_reader").format(
                            sql.Identifier(schema)
                        )
                    )
                    admin.execute(f"CREATE TABLE public.{name} (wrong TEXT)")
                admin.execute(f"CREATE TABLE {schema}.{name} ({columns})")
                admin.execute(f"INSERT INTO {schema}.{name} VALUES {values}")
                admin.execute(f"CREATE VIEW {schema}.{name}_view AS SELECT * FROM {schema}.{name}")
                admin.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO analysis_reader")
                ds = datasource(
                    backend,
                    {"host": pg.HOST, "port": pg.PORT, "database": pg.DATABASE, "user": pg.READER},
                    {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
                )
                with provider_for(backend).open(ds) as session:
                    yield Case(
                        session,
                        TableSourceIR(
                            name + ("_view" if "view" in profile else ""), database=schema
                        ),
                        environment,
                    )
            finally:
                if schema != "public":
                    admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                else:
                    admin.execute(f"DROP VIEW IF EXISTS public.{name}_view")
                admin.execute(f"DROP TABLE IF EXISTS public.{name}")
        return
    if backend == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        environment = mysql.setup()
        monkeypatch.setenv("MARIVO_SOURCE_PROFILE_PASSWORD", mysql.password())
        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            try:
                cursor.execute(f"CREATE TABLE {name} ({columns}) ENGINE=InnoDB")
                cursor.execute(f"INSERT INTO {name} VALUES {values}")
                cursor.execute(f"CREATE VIEW {name}_view AS SELECT * FROM {name}")
                ds = datasource(
                    backend,
                    {
                        "host": mysql.HOST,
                        "port": mysql.PORT,
                        "database": mysql.DATABASE,
                        "user": mysql.READER,
                    },
                    {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
                )
                with provider_for(backend).open(ds) as session:
                    yield Case(
                        session,
                        TableSourceIR(name + ("_view" if profile == "view" else "")),
                        environment,
                    )
            finally:
                cursor.execute(f"DROP VIEW IF EXISTS {name}_view")
                cursor.execute(f"DROP TABLE IF EXISTS {name}")
        return
    if backend == "trino":
        from tests.datasource.environment import trino_analysis as trino

        catalog = "iceberg" if profile == "iceberg" else "noniceberg"
        environment = trino.setup() if catalog == "iceberg" else trino.setup_non_iceberg()
        environment["connector"] = "iceberg" if catalog == "iceberg" else "memory"
        with trino.connection(admin=True, catalog=catalog) as admin:
            cursor = admin.cursor()
            try:
                cursor.execute(f"CREATE TABLE {catalog}.analysis.{name} ({columns})").fetchall()
                cursor.execute(f"INSERT INTO {catalog}.analysis.{name} VALUES {values}").fetchall()
                ds = datasource(
                    backend,
                    {
                        "host": "127.0.0.1",
                        "port": 18080,
                        "catalog": catalog,
                        "schema": "analysis",
                        "user": "analysis_reader",
                        "timezone": "UTC",
                    },
                )
                with provider_for(backend).open(ds) as session:
                    yield Case(
                        session, TableSourceIR(name, database=(catalog, "analysis")), environment
                    )
            finally:
                cursor.execute(f"DROP TABLE IF EXISTS {catalog}.analysis.{name}").fetchall()
                cursor.close()
        return
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    cluster = profile == "distributed"
    environment = ch.setup_cluster() if cluster else ch.setup()
    ports = ch.CLUSTER_HTTP_PORTS if cluster else (18123,)
    database = "qualification_cluster" if cluster else "qualification"
    monkeypatch.setenv("MARIVO_SOURCE_PROFILE_PASSWORD", password())
    try:
        for index, port in enumerate(ports):
            with ch.connection(admin=True, port=port) as admin:
                admin.command(
                    f"CREATE TABLE {database}.{name} ({ch_columns}) ENGINE=MergeTree ORDER BY tuple()"
                )
                admin.insert(
                    f"{database}.{name}",
                    [list(row.values()) for row in rows[index :: len(ports)]],
                    column_names=list(rows[0]),
                )
                if cluster:
                    admin.command(
                        f"CREATE TABLE {database}.{name}_distributed AS {database}.{name} ENGINE=Distributed({ch.CLUSTER}, {database}, {name}, rand())"
                    )
        ds = datasource(
            backend,
            {
                "host": "127.0.0.1",
                "port": ports[0],
                "database": database,
                "user": "analysis_reader",
            },
            {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
        )
        with provider_for(backend).open(ds) as session:
            yield Case(
                session,
                TableSourceIR(name + ("_distributed" if cluster else ""), database=database),
                environment,
            )
    finally:
        for port in ports:
            with ch.connection(admin=True, port=port) as admin:
                admin.command(f"DROP TABLE IF EXISTS {database}.{name}_distributed")
                admin.command(f"DROP TABLE IF EXISTS {database}.{name}")


def receipt(name: str, value: dict[str, object]) -> None:
    directory = os.environ.get("MARIVO_R92_EVIDENCE_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_bytes(freeze.encode(json_value(value)) + b"\n")


def json_value(value: object) -> freeze.Json:
    if isinstance(value, Decimal | datetime):
        return {"type": type(value).__name__, "value": str(value)}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    return freeze.checked(value)
