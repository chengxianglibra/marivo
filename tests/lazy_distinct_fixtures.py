"""Authored exact distinct Metrics and independent two-period membership rows."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends.duckdb import Backend

from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.refs import SemanticKind, _create_ref, ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody
from marivo.semantic.ir import CumulativeComposition, MetricIR, RatioComposition
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")
DISTINCT_BUYERS = ref.metric("sales.distinct_buyers")
DISTINCT_ORDERS = ref.metric("sales.distinct_orders")

# A key may repeat, cross region/channel partitions, or occur on both sides.
DISTINCT_ORDER_VALUES = (
    (1, "shared", 1, "web", "2026-01-02"),
    (2, "shared", 1, "web", "2026-01-02"),
    (3, "shared", 2, "store", "2026-01-03"),
    (4, "old", 1, "store", "2026-01-03"),
    (5, None, 3, None, "2026-01-04"),
    (6, "shared", 1, "web", "2026-02-02"),
    (7, "shared", 2, "store", "2026-02-02"),
    (8, "new", 1, "store", "2026-02-03"),
    (9, "new", 2, "web", "2026-02-03"),
    (10, "extra", 3, None, "2026-02-04"),
    (11, None, 3, None, "2026-02-04"),
)


def make_distinct_registry(
    database: Path | None = None,
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Extend established source declarations with distinct scalar and Entity roots."""
    original, old_sidecar = (
        make_semantic_registry() if database is None else make_execution_registry(database)
    )
    buyer = "sales.orders.buyer_key"
    buyer_ref = ref.measure(buyer)
    body = ExpressionBody.for_column("tenant")
    measure = replace(
        original.measures["sales.orders.amount"],
        semantic_id=buyer,
        name="buyer_key",
        python_symbol="buyer_key",
        unit=None,
        body_ast_hash=body.body_ast_hash,
    )
    template = original.metrics["sales.order_count"]
    metrics: dict[str, MetricIR] = dict(original.metrics)
    for name, target, kind, entity in (
        ("distinct_buyers", buyer, "measure", "sales.orders"),
        ("distinct_orders", "sales.orders", "entity", "sales.orders"),
        ("distinct_composite", "sales.composite", "entity", "sales.composite"),
    ):
        metrics[f"sales.{name}"] = replace(
            template,
            semantic_id=f"sales.{name}",
            name=name,
            python_symbol=name,
            aggregation="count_distinct",
            aggregation_target=target,
            aggregation_target_kind="measure" if kind == "measure" else "entity",
            measure=target if kind == "measure" else None,
            entities=(entity,),
        )
    metrics["sales.distinct_web_buyers"] = replace(
        metrics["sales.distinct_buyers"],
        semantic_id="sales.distinct_web_buyers",
        name="distinct_web_buyers",
        python_symbol="distinct_web_buyers",
        filter=((CHANNEL.path, "web"),),
    )
    for name, composition in (
        (
            "cumulative_distinct_buyers",
            CumulativeComposition("sales.distinct_buyers", "sales.orders.order_time"),
        ),
        ("distinct_buyer_ratio", RatioComposition("sales.distinct_buyers", "sales.order_count")),
    ):
        metrics[f"sales.{name}"] = replace(
            original.metrics["sales.conversion_rate"],
            semantic_id=f"sales.{name}",
            name=name,
            python_symbol=name,
            composition=composition,
        )
    registry = replace(original, measures={**original.measures, buyer: measure}, metrics=metrics)
    registry.freeze()
    sidecar = CompiledExpressionSidecar(
        bodies={**old_sidecar.bodies, buyer_ref: body},
        field_owners={**old_sidecar.field_owners, buyer_ref: ref.entity("sales.orders")},
        catalog_refs=old_sidecar.catalog_refs
        | frozenset({buyer_ref})
        | frozenset(_create_ref(SemanticKind.METRIC, name) for name in metrics),
    )
    return registry, sidecar


def make_distinct_sources() -> LazySources:
    registry, sidecar = make_distinct_registry()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct",
        store_id="store-distinct",
    )


def seed_distinct_database(database: Path, *, dense_time: bool = False) -> None:
    """Seed a new isolated database with exact overlaps and null distinct inputs."""
    seed_execution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("DELETE FROM customers")
        connection.executemany(
            "INSERT INTO customers (id, region) VALUES (?, ?)",
            ((1, "EU"), (2, "US"), (3, None)),
        )
        connection.executemany(
            "INSERT INTO orders (id, tenant, customer_id, channel, day) VALUES (?, ?, ?, ?, ?)",
            DISTINCT_ORDER_VALUES,
        )
        if dense_time:
            padding = []
            identity = 100
            for month in ("01", "02"):
                for day in ("02", "03", "04"):
                    for customer, channel in (
                        (1, "web"),
                        (1, "store"),
                        (2, "web"),
                        (2, "store"),
                        (3, None),
                    ):
                        padding.append((identity, customer, channel, f"2026-{month}-{day}"))
                        identity += 1
            connection.executemany(
                "INSERT INTO orders (id, customer_id, channel, day) VALUES (?, ?, ?, ?)",
                padding,
            )


def assert_no_raw_keys(value: object) -> None:
    """Check public rows, Evidence, metadata and statement inventories for source keys."""
    encoded = json.dumps(value, sort_keys=True, default=str)
    for key in {str(row[1]) for row in DISTINCT_ORDER_VALUES if row[1] is not None}:
        assert json.dumps(key) not in encoded
        assert json.dumps(json.dumps(key))[1:-1] not in encoded


@contextmanager
def guard_membership_transport() -> Iterator[None]:
    """Allow zero-row schema inspection but reject raw-key Arrow collection."""
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
        assert DISTINCT_KEY_COLUMN not in result.column_names or result.num_rows == 0
        assert_no_raw_keys(result.to_pylist())
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
                assert DISTINCT_KEY_COLUMN not in batch.schema.names or batch.num_rows == 0
                assert_no_raw_keys(batch.to_pylist())
                yield batch

        return pa.RecordBatchReader.from_batches(reader.schema, checked())

    with (
        patch.object(Backend, "to_pyarrow", table),
        patch.object(Backend, "to_pyarrow_batches", batches),
    ):
        yield
