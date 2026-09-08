"""Real DuckDB datasource declarations and independent deterministic input rows."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import duckdb
import ibis
import ibis.expr.types as ir
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.contracts import ObservationActionPort
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, TableColumnBindingIR, TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

# IDs, amounts, weights and relationships deliberately have unequal multiplicity.
ORDER_VALUES = (
    (1, 1, 10.0, 1.0, "2026-02-02"),
    (2, 1, 30.0, 3.0, "2026-02-03"),
    (3, 2, 100.0, 2.0, "2026-02-02"),
    (4, 2, None, 4.0, "2026-02-03"),
    (5, 3, 0.0, 0.0, "2026-02-04"),
    (6, 3, 7.0, None, None),
)
LINE_VALUES = ((1, 1, 2.0), (2, 1, 3.0), (3, 2, 10.0), (4, 3, 20.0), (5, 3, 30.0))


def assert_compiled_validations(validations: tuple[CompiledValidation, ...]) -> None:
    """Execute all named checks together, sharing their compiled expression graph."""
    if not validations:
        return
    checks = ibis.union(
        *(
            check.expression.mutate(validation_ordinal=ibis.literal(index))
            for index, check in enumerate(validations)
        )
    ).order_by("validation_ordinal")
    results = checks.to_pyarrow().to_pylist()
    assert len(results) == len(validations)
    for check, result in zip(validations, results, strict=True):
        assert result["violations"] == 0, check.name


def make_execution_registry(
    database: Path, *, api_url: str | None = None
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Adapt authored IR to declared tables; production normalizers remain unchanged."""
    original, sidecar = make_semantic_registry()
    entities = {}
    for path, entity in original.entities.items():
        source = entity.source
        if isinstance(source, JsonSourceIR):
            source = replace(source, path=api_url) if api_url is not None else source
        elif isinstance(source, CsvSourceIR):
            source = TableSourceIR(
                entity.name,
                columns=tuple(
                    (name, TableColumnBindingIR(name, logical_type))
                    for name, logical_type in source.schema
                ),
            )
        entities[path] = replace(entity, source=source)
    registry = replace(
        original,
        entities=entities,
        datasources={
            name: replace(datasource, fields={"path": str(database)})
            for name, datasource in original.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar


def seed_execution_database(database: Path) -> None:
    """Copy immutable physical input tables into a new isolated database."""
    with database.open("xb") as output:
        output.write(_execution_database_template("v1"))


@cache
def _execution_database_template(version: Literal["v1"]) -> bytes:
    """Keep closed database bytes in-process; never share a writable connection."""
    with TemporaryDirectory(prefix=f"marivo-execution-{version}-") as directory:
        database = Path(directory) / "template.duckdb"
        _build_execution_database(database)
        return database.read_bytes()


def _build_execution_database(database: Path) -> None:
    connection = duckdb.connect(str(database), config={"threads": 1})
    try:
        for name in (
            "orders",
            "customers",
            "lines",
            "snapshots",
            "validity",
            "unkeyed",
            "composite",
        ):
            connection.execute(
                f'CREATE TABLE {name} (id BIGINT, tenant VARCHAR, customer_id BIGINT, order_id BIGINT, amount DOUBLE, weight DOUBLE, region VARCHAR, channel VARCHAR, day DATE, start DATE, "end" DATE)'
            )
        connection.executemany(
            "INSERT INTO orders (id, customer_id, amount, weight, day, channel) VALUES (?, ?, ?, ?, ?, 'web')",
            ORDER_VALUES,
        )
        connection.executemany(
            "INSERT INTO lines (id, order_id, amount) VALUES (?, ?, ?)", LINE_VALUES
        )
        connection.executemany(
            "INSERT INTO customers (id, region) VALUES (?, ?)",
            ((1, "EU"), (2, None), (3, "EU"), (4, None)),
        )
        connection.execute("INSERT INTO composite (tenant, id) VALUES ('b', 1), ('a', 2), ('a', 1)")
        connection.execute(
            "INSERT INTO snapshots (id, day) VALUES (1, DATE '2026-02-27'), (1, DATE '2026-02-28'), (2, DATE '2026-02-28')"
        )
        connection.execute(
            "INSERT INTO validity (id, start, \"end\") VALUES (1, DATE '2026-01-01', DATE '2026-02-10'), (1, DATE '2026-02-10', NULL), (2, DATE '2026-02-15', DATE '2026-03-01')"
        )
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class ExecutionFixture:
    database: Path
    registry: Registry
    sidecar: CompiledExpressionSidecar
    sources: LazySources
    backend: Backend

    def tables(self, dataset: LogicalDataset) -> dict[str, ir.Table]:
        return {
            entity.ref.path: self.backend.table(entity.source.table).select(
                *(name for name, _ in entity.columns)
            )
            for entity in required_entities(dataset)
            if isinstance(entity.source, TableSourceIR)
        }


@contextmanager
def execution_fixture(
    path: Path, *, action_port: ObservationActionPort | None = None
) -> Iterator[ExecutionFixture]:
    database = path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=action_port if action_port is not None else NoIoActionPort(),
        session_id="session-compiler",
        store_id="store-compiler",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        yield ExecutionFixture(database, registry, sidecar, sources, backend)
    finally:
        backend.disconnect()


@dataclass(frozen=True, slots=True)
class ControlledJsonSource:
    """Local source address and test-only raw GET records."""

    url: str
    requests: list[str]


@contextmanager
def controlled_json_source(
    *, tenant_values: Mapping[str, float] | None = None
) -> Iterator[ControlledJsonSource]:
    """Serve declared JSON rows; tenant alpha/beta select independent source values."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit

    requests: list[str] = []
    amounts = dict(tenant_values) if tenant_values is not None else {"alpha": 10.0, "beta": 25.0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            tenant = parse_qs(urlsplit(self.path).query).get("tenant", [""])[0]
            if tenant not in amounts:
                self.send_response(400)
                self.end_headers()
                return
            amount = amounts[tenant]
            rows = [
                {
                    "id": 1,
                    "tenant": tenant,
                    "customer_id": 1,
                    "order_id": 1,
                    "amount": amount,
                    "weight": 2.0,
                    "region": "EU",
                    "channel": "web",
                    "day": "2026-02-02",
                    "start": "2026-02-01",
                    "end": "2026-03-01",
                }
            ]
            body = json.dumps(rows).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ControlledJsonSource(f"http://127.0.0.1:{server.server_port}/facts", requests)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
