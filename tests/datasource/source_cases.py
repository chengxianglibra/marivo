"""Owned source fixtures and portable assertion receipts."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from uuid import uuid4

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _pytest.mark.structures import ParameterSet
from psycopg import sql
from typing_extensions import override

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
from marivo.datasource.runtime.adapters import SourceSession, provider_for
from marivo.datasource.runtime.read_types import SourceIR
from marivo.datasource.runtime.session_factory import open_source_session
from tests.datasource.environment import clickhouse_analysis as ch
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.environment import postgres_analysis as pg
from tests.datasource.environment import trino_analysis as trino
from tests.datasource.environment.credentials import password
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
    "trino": ("iceberg", "non-iceberg"),
    "clickhouse": ("mergetree", "distributed"),
}

ROWS: list[dict[str, object]] = [
    {"id": 10, "amount": 2, "tenant": "a", "revision": 1},
    {"id": 11, "amount": None, "tenant": "a", "revision": 2},
    {"id": 11, "amount": 4, "tenant": "b", "revision": 1},
]
FLAGS = {
    "postgres": "MARIVO_POSTGRES_ANALYSIS_TEST",
    "mysql": "MARIVO_MYSQL_ANALYSIS_TEST",
    "trino": "MARIVO_TRINO_ANALYSIS_TEST",
    "clickhouse": "MARIVO_CLICKHOUSE_ANALYSIS_TEST",
}

MYSQL_RUNTIME_PROFILES = [
    pytest.param("innodb-table", id="mysql", marks=pytest.mark.runtime),
    pytest.param("mariadb", id="mariadb", marks=pytest.mark.runtime),
]


def source_params(
    cases: Iterable[str | tuple[object, ...]], *, backend_index: int = 0
) -> list[ParameterSet]:
    """Route actual service cases to Runtime while keeping embedded sources daily."""
    parameters: list[ParameterSet] = []
    for case in cases:
        values = (case,) if isinstance(case, str) else case
        backend = values[backend_index]
        parameters.append(
            pytest.param(*values, marks=pytest.mark.runtime if backend in FLAGS else ())
        )
    return parameters


def datasource(
    backend: str, fields: dict[str, object], env: dict[str, str] | None = None
) -> DatasourceIR:
    return DatasourceIR(
        semantic_id="source_profile",
        name="source_profile",
        backend_type=backend,
        wall_clock_timezone="UTC",
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

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _duckdb_source_case(
    profile: str,
    name: str,
    columns: str,
    values: str,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    rows: list[dict[str, object]] | None,
) -> Iterator[Case]:
    backend = "duckdb"
    path = root / f"source.{backend}"
    admin = ibis.duckdb.connect(path)
    try:
        admin.raw_sql(f"CREATE TABLE {name} ({columns})")
        admin.raw_sql(f"INSERT INTO {name} VALUES {values}")
        admin.raw_sql(f"CREATE VIEW {name}_view AS SELECT * FROM {name}")

    finally:
        admin.disconnect()
    fields: dict[str, object] = {"path": str(path), "read_only": True}
    ds = datasource(backend, fields)
    with open_source_session(provider_for(backend), ds) as session:
        environment: dict[str, object] = {"backend": backend, "read_only": True}
        if profile.startswith("http-json"):
            auth = profile.endswith("auth")
            with json_server(auth, rows) as (url, seen):
                if auth:
                    session.close()
                    monkeypatch.setenv(
                        "MARIVO_SOURCE_PROFILE_HTTP_TOKEN", "synthetic-source_profile"
                    )
                    ds = datasource(
                        backend,
                        {
                            "path": str(path),
                            "read_only": True,
                            "secrets": [{"type": "http", "scope": url + "/private/"}],
                        },
                        {f"http@{url}/private/.token": "MARIVO_SOURCE_PROFILE_HTTP_TOKEN"},
                    )
                with (
                    open_source_session(provider_for(backend), ds)
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
                        http_session.bind(JsonSourceIR(url + "/outside"), source_identity="outside")
                        assert seen[-1] == ("/outside", False)
                    http_source = JsonSourceIR(
                        url + "/private/data", query_params=(("page", SourceParamIR("page")),)
                    )
                    environment["http_requests"] = seen
                    yield Case(http_session, http_source, environment, {"page": 7})
                    assert seen[-1] == ("/private/data?page=7", auth)
                    environment["http_requests"] = [[path, authorized] for path, authorized in seen]
            return
        source: SourceIR = TableSourceIR(name + ("_view" if "view" in profile else ""))
        if profile == "csv":
            file = root / "facts.csv"
            file.write_text("id,amount,tenant,revision\n10,2,a,1\n11,,a,2\n11,4,b,1\n")
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


def _postgres_source_case(
    profile: str, name: str, columns: str, values: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Case]:
    backend = "postgres"
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
            # Public fixtures inherit SELECT from pg.setup(); isolated schemas need their own grant.
            if schema != "public":
                admin.execute(
                    sql.SQL("GRANT SELECT ON {}.{}, {}.{} TO analysis_reader").format(
                        sql.Identifier(schema),
                        sql.Identifier(name),
                        sql.Identifier(schema),
                        sql.Identifier(name + "_view"),
                    )
                )
            ds = datasource(
                backend,
                {"host": pg.HOST, "port": pg.PORT, "database": pg.DATABASE, "user": pg.READER},
                {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
            )
            with open_source_session(provider_for(backend), ds) as session:
                yield Case(
                    session,
                    TableSourceIR(name + ("_view" if "view" in profile else ""), database=schema),
                    environment,
                )
        finally:
            if schema != "public":
                admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            else:
                admin.execute(f"DROP VIEW IF EXISTS public.{name}_view")
            admin.execute(f"DROP TABLE IF EXISTS public.{name}")


@contextmanager
def source_case(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: SourceData | None = None,
) -> Iterator[Case]:
    if backend in FLAGS:
        flag = (
            "MARIVO_MARIADB_ANALYSIS_TEST"
            if backend == "mysql" and profile == "mariadb"
            else FLAGS[backend]
        )
        if os.environ.get(flag) != "1":
            pytest.skip(f"Explicit ready service required: {flag}=1")
    name = "source_profile_" + uuid4().hex
    columns = "id BIGINT, amount BIGINT, tenant VARCHAR(10), revision BIGINT"
    values = "(10,2,'a',1),(11,NULL,'a',2),(11,4,'b',1)"
    rows = ROWS
    ch_columns = "id Int64, amount Nullable(Int64), tenant LowCardinality(String), revision Int64"
    if data is not None:
        columns, values, rows, ch_columns = (
            data.columns,
            data.values,
            data.rows,
            data.clickhouse_columns,
        )
    if backend == "duckdb":
        yield from _duckdb_source_case(
            profile, name, columns, values, monkeypatch, root, rows if data is not None else None
        )
        return
    if backend == "postgres":
        yield from _postgres_source_case(profile, name, columns, values, monkeypatch)
        return
    if backend == "mysql":
        port = mysql.MARIADB_PORT if profile == "mariadb" else mysql.PORT
        environment = mysql.setup(port=port)
        monkeypatch.setenv("MARIVO_SOURCE_PROFILE_PASSWORD", mysql.password())
        with mysql.connection(admin=True, port=port) as admin, admin.cursor() as cursor:
            try:
                cursor.execute(f"CREATE TABLE {name} ({columns}) ENGINE=InnoDB")
                cursor.execute(f"INSERT INTO {name} VALUES {values}")
                cursor.execute(f"CREATE VIEW {name}_view AS SELECT * FROM {name}")
                ds = datasource(
                    backend,
                    {
                        "host": mysql.HOST,
                        "port": port,
                        "database": mysql.DATABASE,
                        "user": mysql.READER,
                    },
                    {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
                )
                with open_source_session(provider_for(backend), ds) as session:
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
        catalog = "iceberg" if profile == "iceberg" else "noniceberg"
        environment = trino.setup() if catalog == "iceberg" else trino.setup_non_iceberg()
        environment["connector"] = "iceberg" if catalog == "iceberg" else "memory"
        with trino.connection(admin=True, catalog=catalog) as trino_admin:
            cursor = trino_admin.cursor()
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
                    },
                )
                with open_source_session(provider_for(backend), ds) as session:
                    yield Case(
                        session, TableSourceIR(name, database=(catalog, "analysis")), environment
                    )
            finally:
                cursor.execute(f"DROP TABLE IF EXISTS {catalog}.analysis.{name}").fetchall()
                cursor.close()
        return

    distributed = profile == "distributed"
    environment = ch.setup()
    database = "qualification"
    monkeypatch.setenv("MARIVO_SOURCE_PROFILE_PASSWORD", password())
    with ch.connection(admin=True) as client:
        try:
            client.command(
                f"CREATE TABLE {database}.{name} ({ch_columns}) ENGINE=MergeTree ORDER BY tuple()"
            )
            client.insert(
                f"{database}.{name}",
                [list(row.values()) for row in rows],
                column_names=list(rows[0]),
            )
            if distributed:
                client.command(
                    f"CREATE TABLE {database}.{name}_distributed AS {database}.{name} "
                    f"ENGINE=Distributed({ch.CLUSTER}, {database}, {name}, rand())"
                )
            ds = datasource(
                backend,
                {
                    "host": "127.0.0.1",
                    "port": ch.port(),
                    "database": database,
                    "user": "analysis_reader",
                },
                {"password": "MARIVO_SOURCE_PROFILE_PASSWORD"},
            )
            with open_source_session(provider_for(backend), ds) as session:
                yield Case(
                    session,
                    TableSourceIR(
                        name + ("_distributed" if distributed else ""), database=database
                    ),
                    environment,
                )
        finally:
            if distributed:
                client.command(f"DROP TABLE IF EXISTS {database}.{name}_distributed")
            client.command(f"DROP TABLE IF EXISTS {database}.{name}")


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
