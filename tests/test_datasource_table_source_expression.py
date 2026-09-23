"""Table projections preserve physical names and discover their actual types."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir
import pytest

import marivo.datasource as md
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.table_source import supports_table_lookup, table_source_expression


class _RecordingBackend:
    def __init__(self) -> None:
        self.table_calls: list[tuple[str, str | tuple[str, ...] | None]] = []
        self.physical = {
            "payload.user.id": "int64",
            'event"timestamp': "timestamp",
            "value; DROP TABLE audit": "float64",
        }

    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ir.Table:
        self.table_calls.append((name, database))
        return ibis.table(self.physical, name=name)


def test_table_declaration_and_projection_are_lazy_and_alias_physical_columns() -> None:
    source = md.table(
        'raw".events',
        database=("analytics", "sales"),
        columns={
            "user.id": "payload.user.id",
            "event_time": 'event"timestamp',
            "safe_value": "value; DROP TABLE audit",
        },
    )
    backend = _RecordingBackend()

    assert backend.table_calls == []
    expression = table_source_expression(backend, source)

    assert backend.table_calls == [('raw".events', ("analytics", "sales"))]
    assert expression.schema().names == ("event_time", "safe_value", "user.id")
    assert str(expression.event_time.type()) == "timestamp"
    assert str(expression["user.id"].type()) == "int64"
    assert str(expression.safe_value.type()) == "float64"
    assert supports_table_lookup(backend)


def test_unprojected_table_keeps_exact_lookup_path() -> None:
    backend = _RecordingBackend()

    expression = table_source_expression(backend, md.table("raw.events", database="sales"))

    assert expression.get_name() == "raw.events"
    assert backend.table_calls == [("raw.events", "sales")]


def test_missing_table_lookup_fails_before_source_execution() -> None:
    with pytest.raises(DatasourceSourceCapabilityError) as exc_info:
        table_source_expression(object(), md.table("events"))

    error = exc_info.value
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert error.received == "object without callable table()"
    assert error.location == "table source 'events'"


def test_real_duckdb_executes_projection_aliases_with_discovered_schema() -> None:
    backend = ibis.duckdb.connect(":memory:")
    try:
        backend.raw_sql(
            'CREATE TABLE "raw.events" ('
            '"event.timestamp" TIMESTAMP, '
            '"schema" VARCHAR, '
            '"score" DOUBLE)'
        )
        backend.raw_sql(
            'INSERT INTO "raw.events" VALUES '
            "('2026-08-17 09:00:00', 'alpha', 1.5), "
            "('2026-08-16 09:00:00', 'beta', 2.5)"
        )
        source = md.table(
            "raw.events",
            columns={
                "event_time": "event.timestamp",
                "schema_name": "schema",
                "score": "score",
            },
        )

        expression = table_source_expression(backend, source)
        filtered = expression.filter(expression.event_time >= "2026-08-17").select(
            "schema_name", "score"
        )

        rows = filtered.execute().to_dict(orient="records")
        assert rows == [{"schema_name": "alpha", "score": 1.5}]
        assert str(expression.event_time.type()) == "timestamp(6)"
    finally:
        backend.disconnect()
