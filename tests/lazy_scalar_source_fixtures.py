"""Shared declarations for MySQL, SQLite, Trino and ClickHouse scalar source acceptance."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    AggKind,
    SampleIntervalIR,
    SemiAdditive,
    TimeFoldIR,
    TimestampParse,
)
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime


def registry_for(
    database: Path,
    *,
    engine: Literal["sqlite", "mysql", "trino", "clickhouse"] = "sqlite",
    table: str = "orders",
) -> tuple[Registry, CompiledExpressionSidecar]:
    original, sidecar = make_execution_registry(database)
    entities = dict(original.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(entity, source=replace(entity.source, table=table))
    metrics = dict(original.metrics)
    for agg in ("min", "max"):
        metrics[f"sales.{agg}_amount"] = replace(
            metrics["sales.revenue"],
            semantic_id=f"sales.{agg}_amount",
            name=f"{agg}_amount",
            aggregation=agg,
        )
    fields: dict[str, str | int] = {"path": str(database)}
    env_refs: dict[str, str] = {}
    if engine == "mysql":
        fields = {
            "host": "127.0.0.1",
            "port": 23306,
            "database": "analysis",
            "user": "analysis_reader",
        }
        env_refs = {"password": "MARIVO_TEST_MYSQL_PASSWORD"}
    if engine == "clickhouse":
        fields = {
            "host": "127.0.0.1",
            "port": 18123,
            "database": "qualification",
            "user": "analysis_reader",
        }
        env_refs = {"password": "MARIVO_TEST_CLICKHOUSE_PASSWORD"}
    if engine == "trino":
        fields = {
            "host": "127.0.0.1",
            "port": 18080,
            "catalog": "iceberg",
            "schema": "analysis",
            "user": "analysis_reader",
        }
    registry = replace(
        original,
        entities=entities,
        metrics=metrics,
        datasources={
            name: replace(value, backend_type=engine, fields=fields, env_refs=env_refs)
            for name, value in original.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar


def fold_registry(
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    fold: TimeFoldIR,
    *,
    sampled: bool = True,
    aggregation: AggKind | None = None,
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Bind the orders measure to a sampled status axis with the given fold.

    Args:
        registry: Frozen base registry from ``registry_for`` (any engine).
        sidecar: Compiled expression sidecar returned with the base registry.
        fold: Status-time fold kind to bind onto the orders amount measure.
        sampled: Sampled five-minute status axis when True; day granularity
            with the registry's own parse when False.
        aggregation: Optional revenue metric aggregation rewrite for
            admission journeys that aggregate through a non-sum fold kind.

    Returns:
        A frozen registry and its sidecar with the fold additivity bound.

    Example:
        >>> base, sidecar = registry_for(database)
        >>> registry, sidecar = fold_registry(base, sidecar, TimeFoldIR("last"))
        >>> runtime.sources(semantic_registry=registry, sidecar=sidecar)

    Constraints:
        The base registry must declare ``sales.orders`` with a ``day`` column
        and the ``sales.orders.order_time`` status axis; callers keep their
        engine-specific registry construction and table binding.
    """
    dimensions = dict(registry.dimensions)
    dimensions["sales.orders.order_time"] = replace(
        registry.dimensions["sales.orders.order_time"],
        granularity="minute" if sampled else "day",
        parse=TimestampParse(timezone="UTC", sample_interval=SampleIntervalIR(5, "minute"))
        if sampled
        else registry.dimensions["sales.orders.order_time"].parse,
    )
    measures = dict(registry.measures)
    measures["sales.orders.amount"] = replace(
        registry.measures["sales.orders.amount"],
        additivity=SemiAdditive("sales.orders.order_time", fold),
    )
    metrics = dict(registry.metrics)
    if aggregation is not None:
        metrics["sales.revenue"] = replace(metrics["sales.revenue"], aggregation=aggregation)
    registry = replace(registry, dimensions=dimensions, measures=measures, metrics=metrics)
    registry.freeze()
    return registry, sidecar


def capture_receipt(
    engine: Literal["mysql", "sqlite"],
    runtime: DatasetRuntime,
    expected: list[tuple[int, str]],
    *,
    source_rows: int,
    submitted: list[dict[str, object]],
) -> None:
    """Optionally save an inspectable live large-source receipt outside default test runs."""
    import json
    import os
    from collections import Counter

    directory = os.environ.get("MARIVO_SLICE4_RECEIPTS")
    if directory is None:
        return
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{engine}.json").write_text(
        json.dumps(
            {
                "backend": engine,
                "dataset_acceptance": True,
                "journey": f"{source_rows} source rows to two ranked groups",
                "source_rows": source_rows,
                "submitted_sql_and_parameters": submitted,
                "independent_expected": expected,
                "primary_queries": runtime.statistics.primary_queries,
                "runtime_validation_operations": runtime.statistics.validation_queries,
                "executed_statement_counts": dict(
                    Counter(role for role, _ in runtime.statistics.statements)
                ),
                "transferred_rows": runtime.statistics.transferred_rows,
                "transferred_bytes": runtime.statistics.transferred_bytes,
                "server_scan_metrics": "unavailable",
                "statements": runtime.statistics.statements,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def capture_submissions(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture the selected native governed-read entry before driver transport."""
    from ibis.backends import BaseBackend

    from marivo.datasource import adapters
    from marivo.datasource.adapters import _Cursor

    submitted: list[dict[str, object]] = []
    original = adapters._native_cursor

    def native_cursor(backend: BaseBackend, backend_name: str, sql: str) -> _Cursor:
        submitted.append({"engine": backend_name, "sql": sql, "parameters": ()})
        return original(backend, backend_name, sql)

    monkeypatch.setattr(adapters, "_native_cursor", native_cursor)
    return submitted


def duckdb_grouped_totals(rows: list[tuple[int, float, str]]) -> list[tuple[float, str]]:
    """Additional engine comparator; explicit arithmetic remains the primary oracle."""
    import duckdb
    import pyarrow as pa

    table = pa.table(
        {
            "id": [r[0] for r in rows],
            "amount": [r[1] for r in rows],
            "channel": [r[2] for r in rows],
        }
    )
    with duckdb.connect() as connection:
        connection.register("source_rows", table)
        return connection.execute(
            "SELECT sum(amount), channel FROM source_rows GROUP BY channel "
            "ORDER BY sum(amount) DESC, channel ASC LIMIT 2"
        ).fetchall()
