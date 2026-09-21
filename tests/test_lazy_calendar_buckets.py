"""Real semantic calendar bucket execution for the five remote source backends.

Opt-in environment variables per existing headers:
``MARIVO_POSTGRES_ANALYSIS_TEST=1``, ``MARIVO_MYSQL_ANALYSIS_TEST=1``,
``MARIVO_TRINO_ANALYSIS_TEST=1``, ``MARIVO_CLICKHOUSE_ANALYSIS_TEST=1``.
DuckDB and SQLite run by default; DuckDB stays the untouched reference oracle
so the remote executions must agree with it row for row.

The certified fiscal calendar publishes exactly two months,
FM1 = 2026-02-01..2026-02-15 and FM2 = 2026-02-16..2026-02-19 (coverage ends
at the half-open 2026-02-20). Over the fixture amounts 02-02: 10+100,
02-03: 30, 02-16: 5 and one row with a NULL day, the hand-computed
expectations are FM1=140.0, FM2=5.0 and a NULL bucket row carrying 7.0 for
the uncovered day. A DuckDB oracle journey executes the admitted
``ms.cumulative(anchor=ms.grain_to_date(grain=<fiscal_month>))`` composite
at the fiscal-month grain: the FM1 endpoint accumulates the FM1 rows
(110.0 + 30.0 = 140.0) and the FM2 endpoint restarts from FM2's own 5.0 —
the ``endpoint_reset_start`` semantic branch; a builtin-month reset would
leak 145.0 into the FM2 row.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import ibis
import pandas as pd
import pytest

import marivo.semantic as ms
from marivo._temporal import Grain, certify_period_calendar_rows
from marivo.analysis import time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.contracts import (
    MetricPayload,
    metric_definition,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from marivo.semantic._authoring_metrics import _compute_composition_hash
from marivo.semantic.ir import (
    CumulativeComposition,
    MetricIR,
    PeriodCalendarIR,
    SourceLocation,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import (
    ExecutionFixture,
    execution_fixture,
    make_execution_registry,
)
from tests.lazy_scalar_type_fixtures import Engine, source_writer
from tests.lazy_temporal_backend_fixtures import DATE_PHYSICAL, _opt_in
from tests.lazy_temporal_fixtures import AXIS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Literal

    from marivo._temporal import PeriodCalendarSnapshotV1
    from marivo.analysis.observation.metric import LogicalMetricDataset
    from marivo.semantic._expression_binding import CompiledExpressionSidecar

ENGINES = ("duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino")
CALENDAR = "sales.fiscal"
FISCAL_MONTH = "fiscal_month"
FM1 = "FM1"
FM2 = "FM2"
COVERAGE = (date(2026, 2, 1), date(2026, 2, 20))
AMOUNTS: tuple[tuple[date | None, float], ...] = (
    (date(2026, 2, 2), 10.0),
    (date(2026, 2, 2), 100.0),
    (date(2026, 2, 3), 30.0),
    (date(2026, 2, 16), 5.0),
    (None, 7.0),
)
LOCATION = SourceLocation("calendar_source_fixture.py", 1)
AMOUNT_PHYSICAL = {
    "duckdb": "DOUBLE",
    "sqlite": "REAL",
    "postgres": "DOUBLE",
    "mysql": "DOUBLE",
    "trino": "DOUBLE",
    "clickhouse": "Double",
}
DAY_BUCKETS: tuple[tuple[str | None, float], ...] = (
    ("2026-02-02", 110.0),
    ("2026-02-03", 30.0),
    ("2026-02-16", 5.0),
    (None, 7.0),
)
MONTH_BUCKETS: tuple[tuple[str | None, float], ...] = (
    ("2026-02-01", 140.0),
    ("2026-02-16", 5.0),
    (None, 7.0),
)
# Hand-computed fiscal month-to-date over the same fixture rows at the
# fiscal-month grain: the FM1 endpoint accumulates 110.0 + 30.0 = 140.0 and
# the FM2 endpoint restarts from FM2's own 5.0; a builtin-month reset would
# leak 145.0 into the FM2 row instead. The NULL-day row joins no endpoint
# window, so like every cumulative journey it publishes no NULL-bucket row.
FISCAL_CUMULATIVE: tuple[tuple[str | None, float], ...] = (
    ("2026-02-01", 140.0),
    ("2026-02-16", 5.0),
)
# The same composite observed as one scope-clipped scalar total (no displayed
# axis): the exclusive endpoint 2026-02-18 sits inside FM2, so
# ``endpoint_reset_start`` resets the accumulation at FM2's 02-16 start and
# the total is FM2's own 5.0; a builtin-month (or broken) reset leaks
# FM1's 140.0 in for 145.0.
FISCAL_MTD_SCALAR = 5.0

pytestmark = pytest.mark.runtime


def _snapshot() -> PeriodCalendarSnapshotV1:
    """Certify the two-month fiscal calendar rows exactly as publication does."""
    rows: list[dict[str, str]] = []
    cursor = COVERAGE[0]
    while cursor < COVERAGE[1]:
        rows.append(
            {
                "calendar_date": cursor.isoformat(),
                FISCAL_MONTH: FM1 if cursor < date(2026, 2, 16) else FM2,
            }
        )
        cursor += timedelta(days=1)
    assert len(rows) == 19
    return certify_period_calendar_rows(
        calendar_ref=ref.period_calendar(CALENDAR),
        boundary_timezone="UTC",
        coverage=COVERAGE,
        columns=("calendar_date", FISCAL_MONTH),
        retained_values=tuple(rows),
        date_column="calendar_date",
        levels={FISCAL_MONTH: FISCAL_MONTH},
    )


def _fiscal_registry(database: Path) -> Registry:
    """Session registry with one period calendar declared over the orders axis."""
    registry, _sidecar = make_execution_registry(database)
    calendar = PeriodCalendarIR(
        semantic_id=CALENDAR,
        domain="sales",
        name="fiscal",
        date=AXIS,
        boundary_timezone="UTC",
        coverage=(COVERAGE[0].isoformat(), COVERAGE[1].isoformat()),
        levels=((FISCAL_MONTH, f"orders.{FISCAL_MONTH}"),),
        ai_context=registry.measures["sales.orders.amount"].ai_context,
        python_symbol="fiscal",
        location=LOCATION,
    )
    registry = replace(registry, period_calendars={CALENDAR: calendar})
    registry.freeze()
    return registry


def _remote_registry(
    table: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: Path = Path("unused.duckdb"),
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Bind each remote engine's registry, adding the fiscal calendar declaration."""
    from marivo.datasource.ir import TableColumnBindingIR, TableSourceIR

    if engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as postgres_registry

        registry, sidecar = postgres_registry(table, monkeypatch)
    else:
        from tests.lazy_scalar_source_fixtures import registry_for

        assert engine in {"sqlite", "mysql", "trino", "clickhouse"}
        remote: Literal["sqlite", "mysql", "trino", "clickhouse"] = engine
        registry, sidecar = registry_for(database, engine=remote, table=table)
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (column, TableColumnBindingIR(column, "float64") if column == "amount" else binding)
                for column, binding in entity.source.columns
                if column in {"id", "day", "amount"}
            ),
        ),
    )
    calendar = PeriodCalendarIR(
        semantic_id=CALENDAR,
        domain="sales",
        name="fiscal",
        date=AXIS,
        boundary_timezone="UTC",
        coverage=(COVERAGE[0].isoformat(), COVERAGE[1].isoformat()),
        levels=((FISCAL_MONTH, f"orders.{FISCAL_MONTH}"),),
        ai_context=registry.measures["sales.orders.amount"].ai_context,
        python_symbol="fiscal",
        location=LOCATION,
    )
    registry = replace(
        registry,
        entities=entities,
        period_calendars={**registry.period_calendars, CALENDAR: calendar},
    )
    registry.freeze()
    return registry, sidecar


@contextmanager
def _duckdb_fixture(
    tmp_path: Path, snapshot: PeriodCalendarSnapshotV1 | None
) -> Iterator[ExecutionFixture]:
    """One local DuckDB oracle source carrying the fiscal calendar authority."""
    from marivo.analysis.materialization.store import SessionStore

    with execution_fixture(tmp_path) as fixture:
        fixture.backend.raw_sql("DELETE FROM orders")
        for index, (day, amount) in enumerate(AMOUNTS, 1):
            fixture.backend.con.execute(
                "INSERT INTO orders (id, customer_id, day, amount) VALUES (?, 1, ?, ?)",
                [index, None if day is None else day.isoformat(), amount],
            )
        registry = _fiscal_registry(fixture.database)
        store = SessionStore(tmp_path)
        session = store.create_session("calendar", report_timezone_name="UTC")
        runtime = DatasetRuntime(store, session.session_ref)
        sources = runtime.sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            period_calendar_snapshots=() if snapshot is None else (snapshot,),
        )
        fixture.backend.disconnect()
        yield replace(fixture, registry=registry, sources=sources)


@contextmanager
def _remote_fixture(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: PeriodCalendarSnapshotV1,
) -> Iterator[tuple[DatasetRuntime, Registry, CompiledExpressionSidecar]]:
    """One UUID fiscal source per backend: admin writes, reader execution reads."""
    _opt_in(engine)
    table = "c6cal_" + uuid4().hex
    database = tmp_path / f"{engine}-source.db"
    registry, sidecar = _remote_registry(
        table,
        engine,
        monkeypatch,
        database=database if engine == "sqlite" else Path("unused.duckdb"),
    )
    runtime = DatasetRuntime.create(tmp_path / engine, engine)
    suffix = " ENGINE=MergeTree ORDER BY tuple()" if engine == "clickhouse" else ""
    day_physical = DATE_PHYSICAL[engine]
    if engine == "clickhouse" and any(day is None for day, _amount in AMOUNTS):
        # ClickHouse stores NULL in a plain Date as the epoch date, so the
        # uncovered-day journey needs the native Nullable(Date) column.
        day_physical = f"Nullable({day_physical})"
    try:
        with source_writer(engine, database) as write:
            write(
                f"CREATE TABLE {table} (id BIGINT, day {day_physical}, "
                f"amount {AMOUNT_PHYSICAL[engine]}){suffix}"
            )
            for index, (day, amount) in enumerate(AMOUNTS, 1):
                literal = "NULL"
                if day is not None:
                    literal = (
                        f"DATE '{day.isoformat()}'" if engine == "trino" else f"'{day.isoformat()}'"
                    )
                write(f"INSERT INTO {table} VALUES ({index},{literal},{amount})")
        yield runtime, registry, sidecar
    finally:
        with source_writer(engine, database) as write:
            write(f"DROP TABLE IF EXISTS {table}")


def _grain(level: str) -> Grain:
    return ms.calendar_grain(calendar=ms.ref.period_calendar(CALENDAR), level=level)


def _bucketed(sources: LazySources, *, level: str) -> LogicalMetricDataset:
    """Observe revenue over the fixture date axis under one semantic grain."""
    return (
        sources.observe(ref.metric("sales.revenue"))
        .with_time_axis(ref.time_dimension(AXIS), grain=_grain(level))
        .aggregate()
    )


def _bucket_rows(frame: pd.DataFrame) -> list[tuple[str | None, float]]:
    ordered = frame.sort_values("order_time", na_position="last")
    return [
        (None if pd.isna(value) else str(value)[:10], float(total))
        for value, total in zip(ordered.order_time, ordered.revenue, strict=True)
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_calendar_day_buckets_execute_on_source(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Day-level semantic grain keeps every physical date as its own bucket."""
    fiscal = _snapshot()
    if engine == "duckdb":
        with _duckdb_fixture(tmp_path, fiscal) as fixture:
            frame = _bucketed(fixture.sources, level="day").execute().to_pandas()
    else:
        with _remote_fixture(tmp_path, engine, monkeypatch, fiscal) as (runtime, registry, side):
            sources = runtime.sources(
                semantic_registry=registry,
                sidecar=side,
                period_calendar_snapshots=(fiscal,),
            )
            frame = _bucketed(sources, level="day").execute().to_pandas()
    assert _bucket_rows(frame) == list(DAY_BUCKETS)


@pytest.mark.parametrize("engine", ENGINES)
def test_calendar_month_buckets_execute_on_source(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The published fiscal-month level sums FM1=140.0 and FM2=5.0."""
    fiscal = _snapshot()
    if engine == "duckdb":
        with _duckdb_fixture(tmp_path, fiscal) as fixture:
            frame = _bucketed(fixture.sources, level=FISCAL_MONTH).execute().to_pandas()
    else:
        with _remote_fixture(tmp_path, engine, monkeypatch, fiscal) as (runtime, registry, side):
            sources = runtime.sources(
                semantic_registry=registry,
                sidecar=side,
                period_calendar_snapshots=(fiscal,),
            )
            frame = _bucketed(sources, level=FISCAL_MONTH).execute().to_pandas()
    assert _bucket_rows(frame) == list(MONTH_BUCKETS)


@pytest.mark.parametrize("engine", ENGINES)
def test_out_of_period_date_keeps_null_bucket_row(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CASE lowering yields NULL for dates outside certified coverage."""
    fiscal = _snapshot()
    if engine == "duckdb":
        with _duckdb_fixture(tmp_path, fiscal) as fixture:
            frame = _bucketed(fixture.sources, level=FISCAL_MONTH).execute().to_pandas()
    else:
        with _remote_fixture(tmp_path, engine, monkeypatch, fiscal) as (runtime, registry, side):
            sources = runtime.sources(
                semantic_registry=registry,
                sidecar=side,
                period_calendar_snapshots=(fiscal,),
            )
            frame = _bucketed(sources, level=FISCAL_MONTH).execute().to_pandas()
    rows = _bucket_rows(frame)
    assert rows[-1] == (None, 7.0)
    assert rows[:2] == list(MONTH_BUCKETS[:2])


@pytest.mark.parametrize("engine", ENGINES)
def test_observe_without_certified_snapshot_fails_at_construction(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unpublished calendar keeps the existing readiness structured failure."""
    if engine == "duckdb":
        with _duckdb_fixture(tmp_path, None) as fixture:
            observed = fixture.sources.observe(
                ref.metric("sales.revenue"),
            )
    else:
        with _remote_fixture(tmp_path, engine, monkeypatch, _snapshot()) as (
            runtime,
            registry,
            side,
        ):
            observed = runtime.sources(semantic_registry=registry, sidecar=side).observe(
                ref.metric("sales.revenue"),
            )
    with pytest.raises(ObservationConstructionError, match="missing calendar authority"):
        observed.with_time_axis(ref.time_dimension(AXIS), grain=_grain(FISCAL_MONTH))


def test_mismatched_snapshot_calendar_ref_fails_at_compile(tmp_path: Path) -> None:
    """A certified snapshot for another calendar keeps its compile-time guard."""
    other = certify_period_calendar_rows(
        calendar_ref=ref.period_calendar("sales.other"),
        boundary_timezone="UTC",
        coverage=COVERAGE,
        columns=("calendar_date", FISCAL_MONTH),
        retained_values=tuple(
            {
                "calendar_date": (COVERAGE[0] + timedelta(days=offset)).isoformat(),
                FISCAL_MONTH: FM1 if offset < 15 else FM2,
            }
            for offset in range(19)
        ),
        date_column="calendar_date",
        levels={FISCAL_MONTH: FISCAL_MONTH},
    )
    with _duckdb_fixture(tmp_path, _snapshot()) as fixture:
        observed = fixture.sources.observe(
            ref.metric("sales.revenue"),
        )
        # Bind the fiscal grain through a session holding the matching fiscal
        # snapshot (construction cannot proceed otherwise), then splice the
        # other calendar's certified snapshot onto the definition: bucket()
        # must refuse the ref mismatch at compile time.
        timed = observed.with_time_axis(ref.time_dimension(AXIS), grain=_grain(FISCAL_MONTH))
        root = timed._root
        assert isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload)
        definition = replace(metric_definition(timed), temporal_snapshot=other)
        logical = construct_operator(
            owner=owner_of(timed),
            registry=timed._registry,
            operator_id="metric.with_time_axis",
            contract_versions=producer_contract("metric.with_time_axis").versions,
            inputs=(observed,),
            row_contract=timed.row_contract,
            row_set_contract=timed.row_set_contract,
            payload=MetricPayload(_token=_CORE_TOKEN, definition=definition, captures=()),
        )
        with pytest.raises(DatasetCompilationError, match="missing period authority"):
            compile_dataset(
                logical,
                {
                    entity.ref.path: ibis.table(dict(entity.columns), name=entity.source.table)
                    for entity in required_entities(logical)
                },
                read_timezone="UTC",
            )


@pytest.mark.parametrize("engine", ENGINES)
def test_calendar_artifact_rolls_up_to_fiscal_month(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote artifact under the day semantic grain rebuckets to fiscal month."""
    fiscal = _snapshot()
    if engine == "duckdb":
        with _duckdb_fixture(tmp_path, fiscal) as fixture:
            checkpoint = _bucketed(fixture.sources, level="day").execute()
    else:
        with _remote_fixture(tmp_path, engine, monkeypatch, fiscal) as (runtime, registry, side):
            sources = runtime.sources(
                semantic_registry=registry,
                sidecar=side,
                period_calendar_snapshots=(fiscal,),
            )
            checkpoint = _bucketed(sources, level="day").execute()
    from marivo.analysis.observation.metric import MaterializedMetricDataset

    assert isinstance(checkpoint, MaterializedMetricDataset)
    monthly = checkpoint.rollup(grain=_grain(FISCAL_MONTH)).execute()
    assert _bucket_rows(monthly.to_pandas()) == list(MONTH_BUCKETS)


def _cumulative_rows(frame: pd.DataFrame) -> list[tuple[str | None, float]]:
    """Order cumulative spine rows keyed by their fiscal-month start.

    At the fiscal-month query grain each displayed endpoint is one fiscal
    month; the FM2 flow value (5.0, not 145.0) is what distinguishes the
    semantic reset from a builtin-month leak.
    """
    ordered = frame.sort_values(["order_time", "fiscal_mtd"], na_position="last")
    return [
        (None if pd.isna(value) else str(value)[:10], float(total))
        for value, total in zip(ordered.order_time, ordered.fiscal_mtd, strict=True)
    ]


def test_fiscal_grain_to_date_cumulative_resets_at_fiscal_month_start(
    tmp_path: Path,
) -> None:
    """The fiscal-grain cumulative composite executes with its semantic reset.

    ``ms.cumulative(anchor=ms.grain_to_date(grain=<fiscal month>))`` is a
    documented authoring shape admitted on every backend; this DuckDB oracle
    journey executes it end to end at the fiscal-month grain. The
    ``endpoint_reset_start`` semantic branch derives each endpoint's reset
    bound from the certified fiscal calendar: the FM1 endpoint accumulates
    the FM1 rows (110.0 + 30.0 = 140.0), the FM2 endpoint restarts from
    FM2's own 5.0 where a builtin month reset would leak 145.0, and the
    NULL-day row joins no endpoint window. The same composite without a
    displayed axis executes the scalar scope-clip path, where the exclusive
    endpoint 2026-02-18 lands inside FM2 and the total is FM2's own 5.0.
    """
    loader = LoaderContext(default_domain="sales")
    _LOADER_CTX.set(loader)
    try:
        fiscal_grain = ms.calendar_grain(
            calendar=ms.ref.period_calendar(CALENDAR), level=FISCAL_MONTH
        )
        ms.cumulative(
            name="fiscal_mtd",
            base=ms.ref.metric("sales.revenue"),
            over=ms.ref.time_dimension(AXIS),
            anchor=ms.grain_to_date(grain=fiscal_grain),
        )
    finally:
        _LOADER_CTX.set(None)
    # The declared metric's composition is exactly the canonical one the
    # compiler keys on: ms.cumulative lowers the anchor into
    # ("grain_to_date", <fiscal grain>) over the base revenue metric.
    declared = next(
        item.definition
        for item in loader.pending_definitions
        if isinstance(item.definition, MetricIR)
        and item.definition.semantic_id == "sales.fiscal_mtd"
    )
    composition = declared.composition
    assert isinstance(composition, CumulativeComposition)
    assert composition.base == "sales.revenue" and composition.over == AXIS
    assert composition.anchor == ("grain_to_date", fiscal_grain)
    assert declared.body_ast_hash == _compute_composition_hash(composition)

    with _duckdb_fixture(tmp_path, _snapshot()) as fixture:
        metrics = dict(fixture.registry.metrics)
        metrics["sales.fiscal_mtd"] = replace(
            metrics["sales.conversion_rate"],
            semantic_id="sales.fiscal_mtd",
            name="fiscal_mtd",
            composition=composition,
        )
        registry = replace(fixture.registry, metrics=metrics)
        registry.freeze()
        session_store = SessionStore(tmp_path)
        session = session_store.create_session("fiscal-mtd", report_timezone_name="UTC")
        runtime = DatasetRuntime(session_store, session.session_ref)
        sources = runtime.sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            period_calendar_snapshots=(_snapshot(),),
        )
        frame = (
            sources.observe(
                ref.metric("sales.fiscal_mtd"),
                time_scope=time_scope(start="2026-02-01", end="2026-02-20"),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=_grain(FISCAL_MONTH))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert frame.columns.tolist() == ["order_time", "fiscal_mtd"]
        assert _cumulative_rows(frame) == list(FISCAL_CUMULATIVE)
        # The same composite without a displayed axis: the scope endpoint
        # alone selects the reset window through the semantic
        # ``endpoint_reset_start`` branch (FM2 start 2026-02-16).
        scalar = (
            sources.observe(
                ref.metric("sales.fiscal_mtd"),
                time_scope=time_scope(start="2026-02-01", end="2026-02-18"),
            )
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert scalar["fiscal_mtd"].astype(float).tolist() == [FISCAL_MTD_SCALAR]
