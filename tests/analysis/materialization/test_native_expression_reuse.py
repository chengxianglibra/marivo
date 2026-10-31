"""Native rewrites share only equal expressions in one prepared invocation."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pytest
from ibis.backends.clickhouse import Backend as ClickHouseBackend
from ibis.backends.duckdb import Backend as DuckDBBackend

from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import ProjectionRead, SourceRead
from marivo.analysis.compiler.source_time import localize
from marivo.analysis.materialization import temporal_sql
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _native_query_expression,
)
from marivo.datasource.adapters import SourceSession, provider_for
from tests.datasource.source_cases import datasource


@contextmanager
def _source(engine: str) -> Iterator[SourceSession]:
    backend = ClickHouseBackend() if engine == "clickhouse" else DuckDBBackend()
    source = SourceSession(
        provider_for(engine), datasource(engine, {}), backend, owns_backend=False
    )
    try:
        yield source
    finally:
        source.close()


@pytest.fixture
def temporal_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[ops.Node, str]]:
    calls: list[tuple[ops.Node, str]] = []
    original = temporal_sql.lower_temporal

    def lower(expression: ir.Expr, engine: str) -> ir.Expr:
        calls.append((expression.op(), engine))
        return original(expression, engine)

    monkeypatch.setattr(temporal_sql, "lower_temporal", lower)
    return calls


def _stage() -> SourceRead:
    return SourceRead("read", "native", (), (), (), ProjectionRead((), None), (), (), None)


def test_native_rewrite_reuses_expression_not_stage_or_invocation(
    temporal_calls: list[tuple[ops.Node, str]],
) -> None:
    table = ibis.memtable({"id": [1, 2], "clock": ["2026-01-01 05:30:00", "2026-01-02 05:30:00"]})
    expression = table.select(
        id=table.id, instant=localize("Asia/Kolkata", table.clock.cast("timestamp"))
    )
    stage = _stage()
    read = LoweredRead(stage.stage_id, expression)
    with _source("duckdb") as source:
        prepared = PreparedSources(source, (), {}, {})
        first = _native_query_expression(prepared, stage, read)
        # Qualification and execution can consume equal trees under different IDs.
        second = _native_query_expression(prepared, replace(stage, stage_id="other"), read)
        backend = ibis.duckdb.connect()
        try:
            rows = backend.execute(second)
            assert rows.id.tolist() == [1, 2]
            assert rows.instant.tolist() == [
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            ]
            assert first.equals(second)
            assert len(temporal_calls) == 1
            # A newly bound parameter changes the tree without changing the stage.
            changed = replace(read, expression=expression.filter(expression.id > 1))
            filtered = _native_query_expression(prepared, stage, changed)
            assert backend.execute(filtered).id.tolist() == [2]
            assert len(temporal_calls) == 2
            fresh = PreparedSources(source, (), {}, {})
            _native_query_expression(fresh, stage, read)
            assert len(temporal_calls) == 3
        finally:
            backend.disconnect()


def test_native_rewrite_keeps_backend_specific_temporal_operations(
    temporal_calls: list[tuple[ops.Node, str]],
) -> None:
    table = ibis.table({"clock": "timestamp"}, name="clocks")
    expression = table.select(instant=localize("Asia/Kolkata", table.clock))
    stage = _stage()
    read = LoweredRead(stage.stage_id, expression)
    with _source("duckdb") as duckdb_source, _source("clickhouse") as clickhouse_source:
        prepared = PreparedSources(
            duckdb_source, (), {}, {}, sources={"clickhouse": clickhouse_source}
        )
        duckdb = _native_query_expression(prepared, stage, read)
        clickhouse = _native_query_expression(
            prepared, replace(stage, source_id="clickhouse"), read
        )
        assert "TOUTCTIMESTAMP" in ibis.to_sql(clickhouse, dialect="clickhouse").upper()
        assert "TOUTCTIMESTAMP" not in ibis.to_sql(duckdb, dialect="duckdb").upper()
        assert [engine for _, engine in temporal_calls] == ["duckdb", "clickhouse"]
        _native_query_expression(prepared, replace(stage, source_id="clickhouse"), read)
        assert len(temporal_calls) == 2


@pytest.mark.parametrize("engine", ("duckdb", "postgres", "mysql", "trino", "clickhouse"))
def test_nontemporal_expression_does_not_rebuild_graph(engine: str) -> None:
    table = ibis.table({"id": "int64", "amount": "float64"}, name="facts")
    expression = table.filter((table.id > 1) & table.amount.notnull()).aggregate(
        total=table.amount.sum(), mean=table.amount.mean()
    )
    assert temporal_sql.lower_temporal(expression, engine) is expression


def test_timestamp_literal_still_acquires_native_precision() -> None:
    expression = ibis.timestamp("2026-01-01").name("instant").as_table()
    lowered = temporal_sql.lower_temporal(expression, "duckdb")
    assert isinstance(lowered, ir.Table)
    assert lowered.schema()["instant"].scale == 6


def test_clickhouse_timestamp_truncation_still_uses_wide_native_range() -> None:
    table = ibis.table({"instant": "timestamp(6)"}, name="facts")
    expression = table.select(day=table.instant.truncate("D"))
    lowered = temporal_sql.lower_temporal(expression, "clickhouse")
    sql = ibis.to_sql(lowered, dialect="clickhouse")
    assert "dateTrunc" in sql
    assert "toDateTime(" not in sql
