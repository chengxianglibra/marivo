"""Real DuckDB datasource declarations and independent deterministic input rows."""

from __future__ import annotations

from dataclasses import replace
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import duckdb

from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.analysis.numeric.observation_fixtures import make_semantic_registry

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
                columns=tuple((name, name) for name, _physical_name in source.columns),
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
