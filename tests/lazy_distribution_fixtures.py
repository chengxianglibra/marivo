"""Small governed percentile fixtures shared by contracts, compiler and Runtime."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends.duckdb import Backend

from marivo.analysis.observation.distribution_contracts import FREQUENCY, VALUE
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_observation_fixtures import make_semantic_registry

METRIC = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
REGION = ref.dimension("sales.customers.region")
VALUES = (
    (1, 1, "web", 1.0, "2026-01-02"),
    (2, 1, "web", 1.0, "2026-01-02"),
    (3, 2, "store", 9.0, "2026-01-02"),
    (4, 2, "store", 5.0, "2026-01-02"),
    (5, 1, "web", 2.0, "2026-02-02"),
    (6, 1, "web", 8.0, "2026-02-02"),
    (7, 2, "store", 4.0, "2026-02-02"),
    (8, 2, "store", 4.0, "2026-02-02"),
)


def make_distribution_registry(
    database: Path | None = None, *, q: float = 0.5
) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = (
        make_semantic_registry() if database is None else make_execution_registry(database)
    )
    metrics = dict(registry.metrics)
    metrics[METRIC.path] = replace(metrics[METRIC.path], aggregation=("percentile", q))
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    return registry, sidecar


def seed_distribution_database(database: Path) -> None:
    seed_execution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as con:
        con.execute("delete from orders")
        con.execute("delete from customers")
        con.executemany(
            "insert into customers (id, region) values (?, ?)", ((1, "EU"), (2, "US"), (3, None))
        )
        con.executemany(
            "insert into orders (id, customer_id, channel, amount, day) values (?, ?, ?, ?, ?)",
            VALUES,
        )


@contextmanager
def guard_distribution_transport() -> Iterator[None]:
    """Reject numeric distribution payloads at both source Arrow transfer entrypoints."""
    original_table = Backend.to_pyarrow
    original_batches = Backend.to_pyarrow_batches

    def table(
        backend: Backend,
        expression: ir.Expr,
        /,
        *,
        params: Mapping[ir.Scalar, object] | None = None,
        limit: int | str | None = None,
        **kwargs: object,
    ) -> pa.Table:
        result: pa.Table = original_table(backend, expression, params=params, limit=limit, **kwargs)
        assert result.num_rows == 0 or not {VALUE, FREQUENCY}.intersection(result.column_names)
        return result

    def batches(
        backend: Backend,
        expression: ir.Expr,
        /,
        *,
        params: Mapping[ir.Scalar, object] | None = None,
        limit: int | str | None = None,
        chunk_size: int = 1_000_000,
        **kwargs: object,
    ) -> pa.RecordBatchReader:
        reader: pa.RecordBatchReader = original_batches(
            backend, expression, params=params, limit=limit, chunk_size=chunk_size, **kwargs
        )

        def checked() -> Iterator[pa.RecordBatch]:
            for batch in reader:
                assert batch.num_rows == 0 or not {VALUE, FREQUENCY}.intersection(
                    batch.schema.names
                )
                yield batch

        return pa.RecordBatchReader.from_batches(reader.schema, checked())

    with (
        patch.object(Backend, "to_pyarrow", table),
        patch.object(Backend, "to_pyarrow_batches", batches),
    ):
        yield
