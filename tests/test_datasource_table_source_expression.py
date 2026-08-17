"""Generated Ibis relations for typed physical table projections."""

from __future__ import annotations

import math
from collections.abc import Mapping

import ibis
import ibis.expr.types as ir
import pytest

import marivo.datasource as md
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.table_source import (
    supports_table_lookup,
    supports_table_sql,
    table_source_expression,
)


class _RecordingBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.table_calls: list[tuple[str, str | tuple[str, ...] | None]] = []
        self.sql_calls: list[tuple[str, dict[str, str]]] = []

    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ir.Table:
        self.table_calls.append((name, database))
        return ibis.table({"catalog_column": "string"}, name=name)

    def sql(
        self,
        query: str,
        /,
        *,
        schema: Mapping[str, str],
    ) -> ir.Table:
        normalized_schema = dict(schema)
        self.sql_calls.append((query, normalized_schema))
        return ibis.table(normalized_schema, name="projected_source")


@pytest.mark.parametrize(
    ("backend_name", "quote"),
    [
        ("duckdb", '"'),
        ("sqlite", '"'),
        ("postgres", '"'),
        ("trino", '"'),
        ("mysql", "`"),
        ("clickhouse", "`"),
    ],
)
def test_projected_table_generates_one_atomic_identifier_query(
    backend_name: str,
    quote: str,
) -> None:
    backend = _RecordingBackend(backend_name)
    source = md.table(
        f"raw{quote}.events",
        database=("analytics", f"schema{quote}.part"),
        columns={
            "user.id": md.source_column("payload.user.id", data_type="varchar"),
            f"sel{quote}ect": md.source_column(
                f"event{quote}timestamp",
                data_type="timestamp",
            ),
            "x; DROP TABLE audit": md.source_column(
                "value; DROP TABLE audit",
                data_type="double",
            ),
        },
    )

    expression = table_source_expression(backend, source)

    escaped_quote = quote * 2
    expected_query = (
        f"SELECT {quote}event{escaped_quote}timestamp{quote} AS "
        f"{quote}sel{escaped_quote}ect{quote}, "
        f"{quote}payload.user.id{quote} AS {quote}user.id{quote}, "
        f"{quote}value; DROP TABLE audit{quote} AS "
        f"{quote}x; DROP TABLE audit{quote} "
        f"FROM {quote}analytics{quote}.{quote}schema{escaped_quote}.part{quote}."
        f"{quote}raw{escaped_quote}.events{quote}"
    )
    assert backend.table_calls == []
    assert backend.sql_calls == [
        (
            expected_query,
            {
                f"sel{quote}ect": "timestamp",
                "user.id": "string",
                "x; DROP TABLE audit": "float64",
            },
        )
    ]
    assert expression.schema().names == (
        f"sel{quote}ect",
        "user.id",
        "x; DROP TABLE audit",
    )
    assert "*" not in expected_query
    assert " AS t" not in expected_query
    assert supports_table_lookup(backend)
    assert supports_table_sql(backend)


def test_unprojected_table_keeps_exact_lookup_path() -> None:
    backend = _RecordingBackend("duckdb")
    source = md.table("raw.events", database=("warehouse", "sales"))

    expression = table_source_expression(backend, source)

    assert expression.get_name() == "raw.events"
    assert backend.table_calls == [("raw.events", ("warehouse", "sales"))]
    assert backend.sql_calls == []


def test_unprojected_table_without_database_omits_database_keyword() -> None:
    class Backend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def table(self, name: str, /, **options: object) -> ir.Table:
            self.calls.append((name, options))
            return ibis.table({"value": "string"}, name=name)

    backend = Backend()

    table_source_expression(backend, md.table("events"))

    assert backend.calls == [("events", {})]


@pytest.mark.parametrize(
    ("source", "backend", "capability"),
    [
        (md.table("events"), object(), "table"),
        (
            md.table(
                "events",
                columns={"event_time": md.source_column("event.timestamp", data_type="timestamp")},
            ),
            type(
                "LookupOnlyBackend",
                (),
                {
                    "name": "duckdb",
                    "table": lambda self, name, **kwargs: ibis.table(
                        {"event_time": "timestamp"}, name=name
                    ),
                },
            )(),
            "sql",
        ),
    ],
)
def test_missing_table_capability_fails_before_execution(
    source: md.TableSource,
    backend: object,
    capability: str,
) -> None:
    with pytest.raises(DatasourceSourceCapabilityError) as exc_info:
        table_source_expression(backend, source)

    error = exc_info.value
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert error.received == f"{type(backend).__name__} without callable {capability}()"
    assert error.location == f"table source {source.table!r}"
    assert error.repair is not None
    assert error.repair.kind == "configure"
    assert error.repair.help_target.canonical_id == "table"
    assert error.repair.preserves_evidence is False


def test_real_duckdb_executes_projected_aliases_and_outer_filter() -> None:
    backend = ibis.duckdb.connect(":memory:")
    try:
        backend.raw_sql(
            'CREATE TABLE "raw.events" ('
            '"event.timestamp" TIMESTAMP, '
            '"schema" VARCHAR, '
            '"score""value" DOUBLE, '
            '"nullable.value" DOUBLE)'
        )
        backend.raw_sql(
            'INSERT INTO "raw.events" VALUES '
            "('2026-08-17 09:00:00', 'alpha', 1.5, NULL), "
            "('2026-08-16 09:00:00', 'beta', 2.5, 3.0)"
        )
        source = md.table(
            "raw.events",
            columns={
                "event_time": md.source_column("event.timestamp", data_type="timestamp"),
                "nullable_value": md.source_column("nullable.value", data_type="float64"),
                "schema_name": md.source_column("schema", data_type="string"),
                "score": md.source_column('score"value', data_type="float64"),
            },
        )

        expression = table_source_expression(backend, source)
        filtered = expression.filter(expression.event_time >= "2026-08-17").select(
            "schema_name",
            "score",
            "nullable_value",
        )

        compiled = str(backend.compile(filtered))
        assert 'FROM "raw.events") AS "t0" WHERE "t0"."event_time" >= ' in compiled
        assert '"event.timestamp" AS "event_time"' in compiled
        assert '"score""value" AS "score"' in compiled
        rows = filtered.execute().to_dict(orient="records")
        assert len(rows) == 1
        assert rows[0]["schema_name"] == "alpha"
        assert rows[0]["score"] == 1.5
        assert math.isnan(rows[0]["nullable_value"])
    finally:
        backend.disconnect()
