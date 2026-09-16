"""Shared declarations for MySQL, SQLite, Trino and ClickHouse scalar source acceptance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

import pytest

from marivo.analysis.materialization.execution import Parameter
from marivo.analysis.materialization.scalar_sql_execution import Cursor, ScalarExecutionAdapter
from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
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


class _CursorFactory(Protocol):
    def __call__(self, adapter: ScalarExecutionAdapter, *, stream: bool) -> Cursor: ...


def capture_submissions(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture the actual driver call arguments in acceptance, without changing Store."""
    from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter
    from marivo.analysis.materialization.mysql_execution import MySQLExecutionAdapter
    from marivo.analysis.materialization.sqlite_execution import SQLiteExecutionAdapter
    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    submitted: list[dict[str, object]] = []

    def wrap(original: _CursorFactory) -> _CursorFactory:
        def cursor(adapter: ScalarExecutionAdapter, *, stream: bool) -> Cursor:
            native = original(adapter, stream=stream)

            class RecordedCursor:
                def execute(self, sql: str, parameters: tuple[Parameter, ...] = ()) -> object:
                    submitted.append(
                        {"engine": adapter.engine, "sql": sql, "parameters": parameters}
                    )
                    return native.execute(sql, parameters) if parameters else native.execute(sql)

                def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]:
                    return native.fetchmany(size)

                def close(self) -> None:
                    return native.close()

            return RecordedCursor()

        return cursor

    for adapter_type in (
        MySQLExecutionAdapter,
        SQLiteExecutionAdapter,
        TrinoExecutionAdapter,
        ClickHouseExecutionAdapter,
    ):
        monkeypatch.setattr(adapter_type, "cursor", wrap(adapter_type.cursor))
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
