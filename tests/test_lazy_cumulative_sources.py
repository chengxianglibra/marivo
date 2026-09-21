"""Real cumulative Metric execution on all six backends with hand-computed totals.

Opt-in environment variables per existing headers:
``MARIVO_POSTGRES_ANALYSIS_TEST=1``, ``MARIVO_MYSQL_ANALYSIS_TEST=1``,
``MARIVO_TRINO_ANALYSIS_TEST=1``, ``MARIVO_CLICKHOUSE_ANALYSIS_TEST=1``.
DuckDB and SQLite run by default; DuckDB stays the untouched reference oracle
(``tests/test_lazy_retained_fold_matrix.py`` and
``tests/test_lazy_source_algebra.py`` own its contract) and every remote row
must equal it exactly.

Row set (self-built; shared ``ORDER_VALUES`` is untouched) over one customer,
with the plan §4 amounts 02-02 -> 10, 02-02 -> 100, 02-03 -> 30, 02-04 -> 0,
03-01 -> 5 plus a NULL-amount row on 02-03 and a NULL-day row:

- all-history running sum: 02-02 = 110.0, 02-03 = 140.0, 02-04 = 140.0,
  03-01 = 145.0 (NULLs never enter the sum; the NULL-day row leaves the axis);
- ``grain_to_date(month)``: the February rows match all-history while
  03-01 = 5.0 shows the month reset (all-history there would be 145.0);
- ``trailing(2 day)`` over the half-open window ``(end - 2d, end]``:
  02-02 = 110.0, 02-03 = 140.0, 02-04 = 30.0, 03-01 = 5.0;
- a scope start on a day-bucket boundary keeps only the later spine rows with
  every kept bucket fully covered (02-03 = 140.0 onward), and a scope endpoint
  inside the last bucket marks that bucket partial (43200.0 seconds,
  incomplete) — the folded fold-matrix "endpoint and partial selection";
- a date whose trailing window is empty shows a true 0.0 row, distinct from
  the calendar NULL-bucket semantics;
- a remote cumulative artifact continues with a local ``rollup``, and a
  cumulative-over-hidden-axis membership shape stays rejected.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.reads import part_schema, read_part_batches
from marivo.analysis.observation.contracts import EntityReducedMetricSemantics
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    coverage_columns,
)
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.registry import source_unsupported_reason
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import CumulativeAnchor, CumulativeComposition
from marivo.semantic.validator import Registry
from tests.lazy_scalar_source_fixtures import registry_for
from tests.lazy_scalar_type_fixtures import Engine, source_writer
from tests.lazy_temporal_backend_fixtures import DATE_PHYSICAL, _opt_in
from tests.lazy_temporal_fixtures import AXIS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from marivo.analysis.observation.metric import LogicalMetricDataset
    from marivo.analysis.session._lazy_sources import LazySources

ENGINES: tuple[Engine, ...] = ("duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino")
# The observation scope's exclusive endpoint must cover the last displayed day.
WINDOW = time_scope(start="2026-02-01", end="2026-03-02")
REVENUE = ref.metric("sales.revenue")
RUNNING = ref.metric("sales.running")
TIME = ref.time_dimension(AXIS)
# (id, day, amount): unequal daily multiplicity, a NULL amount and a NULL day.
ROWS: tuple[tuple[int, str | None, float | None], ...] = (
    (1, "2026-02-02", 10.0),
    (2, "2026-02-02", 100.0),
    (3, "2026-02-03", 30.0),
    (4, "2026-02-03", None),
    (5, "2026-02-04", 0.0),
    (6, "2026-03-01", 5.0),
    (7, None, 7.0),
)
ALL_HISTORY: tuple[tuple[str, float], ...] = (
    ("2026-02-02", 110.0),
    ("2026-02-03", 140.0),
    ("2026-02-04", 140.0),
    ("2026-03-01", 145.0),
)
MONTH_TO_DATE: tuple[tuple[str, float], ...] = (*ALL_HISTORY[:3], ("2026-03-01", 5.0))
TRAILING_TWO_DAYS: tuple[tuple[str, float], ...] = (
    ("2026-02-02", 110.0),
    ("2026-02-03", 140.0),
    ("2026-02-04", 30.0),
    ("2026-03-01", 5.0),
)
# The true-zero journey ports the retained fold-matrix oracle's empty coverage
# shape (``where`` selects no rows -> one published row with a NULL value, zero
# coverage seconds and zero support counts). A source-row spine cannot host an
# empty trailing window — every published bucket's own rows sit inside its
# window — so "empty means true zero, never carried forward" is exactly the
# empty-coverage fold state, and the NULL-value row is distinct from the
# calendar NULL-bucket row (which carries real sums).
TRAILING_CUT = time_scope(start="2026-02-03", end="2026-03-02")
# A late scope start clips the displayed spine but keeps the trailing windows
# intact: 02-03 still accumulates its (02-01..02-04] window (140.0). The clip
# lands on a day-bucket boundary, so every kept bucket stays fully covered.
TRAILING_CUT_EXPECTED: tuple[tuple[str, float], ...] = (
    ("2026-02-03", 140.0),
    ("2026-02-04", 30.0),
    ("2026-03-01", 5.0),
)
# A scope endpoint inside the last displayed bucket genuinely clips that
# bucket: its coverage seconds halve (02-04 ends at 12:00) and its coverage
# marking flips to incomplete, while the fully covered buckets keep whole-day
# seconds. The start bound stays before the first source day so every engine's
# civil date row set matches.
PARTIAL_SCOPE = time_scope(
    start="2026-02-01T12:00:00",
    end="2026-02-04T12:00:00",
)
PARTIAL_SCOPE_EXPECTED: tuple[tuple[str, float], ...] = (
    ("2026-02-02", 110.0),
    ("2026-02-03", 140.0),
    ("2026-02-04", 30.0),
)
AMOUNT_PHYSICAL = {
    "duckdb": "DOUBLE",
    "sqlite": "REAL",
    # The postgres writer rewrites DOUBLE -> DOUBLE PRECISION itself.
    "postgres": "DOUBLE",
    "mysql": "DOUBLE",
    "trino": "DOUBLE",
    # ClickHouse stores NULL in a plain Double as 0.0, so the NULL-amount
    # journey needs the native Nullable(Double) column, like the NULL-day one.
    "clickhouse": "Nullable(Double)",
}

pytestmark = pytest.mark.runtime


def _running(registry: Registry, anchor: CumulativeAnchor) -> Registry:
    """Add the cumulative running sum over revenue with the selected anchor."""
    metrics = dict(registry.metrics)
    metrics[RUNNING.path] = replace(
        registry.metrics["sales.conversion_rate"],
        semantic_id=RUNNING.path,
        name="running",
        composition=CumulativeComposition(REVENUE.path, AXIS, anchor),
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    return registry


def _registry(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
    database: Path,
    anchor: CumulativeAnchor,
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Bind *engine*'s declarations onto one UUID table, adding the cumulative metric."""
    if engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as postgres_registry

        registry, sidecar = postgres_registry(table, monkeypatch)
    else:
        assert engine in {"sqlite", "mysql", "trino", "clickhouse"}
        registry, sidecar = registry_for(
            database if engine == "sqlite" else Path("unused.duckdb"),
            engine=engine,
            table=table,
        )
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    return _running(registry, anchor), sidecar


@contextmanager
def _declare(engine: Engine, table: str, database: Path) -> Iterator[None]:
    """Admin prepares one UUID cumulative source table; the reader only selects."""
    suffix = " ENGINE=MergeTree ORDER BY tuple()" if engine == "clickhouse" else ""
    day_physical = DATE_PHYSICAL[engine]
    if engine == "clickhouse" and any(day is None for _index, day, _amount in ROWS):
        # ClickHouse stores NULL in a plain Date as the epoch date, so the
        # NULL-day journey needs the native Nullable(Date) column.
        day_physical = f"Nullable({day_physical})"
    try:
        with source_writer(engine, database) as write:
            write(
                f"CREATE TABLE {table} (id BIGINT, day {day_physical}, "
                f"amount {AMOUNT_PHYSICAL[engine]}){suffix}"
            )
            if engine == "postgres":
                # The postgres declaration binds amount as decimal(18, 2).
                write(f"ALTER TABLE {table} ALTER COLUMN amount TYPE NUMERIC(18,2)")
            for index, day, amount in ROWS:
                literal = (
                    "NULL"
                    if day is None
                    else (f"DATE '{day}'" if engine == "trino" else f"'{day}'")
                )
                amount_literal = "NULL" if amount is None else repr(amount)
                write(f"INSERT INTO {table} VALUES ({index},{literal},{amount_literal})")
        yield
    finally:
        with source_writer(engine, database) as write:
            write(f"DROP TABLE IF EXISTS {table}")


@contextmanager
def _duckdb(tmp_path: Path, anchor: CumulativeAnchor) -> Iterator[LazySources]:
    """One local DuckDB oracle source (the untouched reference implementation)."""
    from marivo.analysis.materialization.store import SessionStore

    with _declare("duckdb", "orders", tmp_path / "warehouse.duckdb"):
        from tests.lazy_execution_fixtures import make_execution_registry

        registry, sidecar = make_execution_registry(tmp_path / "warehouse.duckdb")
        entity = registry.entities["sales.orders"]
        assert isinstance(entity.source, TableSourceIR)
        entities = dict(registry.entities)
        entities["sales.orders"] = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (name, binding)
                    for name, binding in entity.source.columns
                    if name in {"id", "day", "amount"}
                ),
            ),
        )
        registry = replace(registry, entities=entities)
        store = SessionStore(tmp_path)
        session = store.create_session("cumulative", report_timezone_name="UTC")
        runtime = DatasetRuntime(store, session.session_ref)
        yield runtime.sources(semantic_registry=_running(registry, anchor), sidecar=sidecar)


@contextmanager
def _remote(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    anchor: CumulativeAnchor,
) -> Iterator[LazySources]:
    """One UUID remote source per backend: admin writes, the reader selects."""
    _opt_in(engine)
    table = "c6cum_" + uuid4().hex
    database = tmp_path / f"{engine}-source.db"
    with _declare(engine, table, database):
        registry, sidecar = _registry(engine, monkeypatch, table, database, anchor)
        runtime = DatasetRuntime.create(tmp_path / engine, engine)
        yield runtime.sources(semantic_registry=registry, sidecar=sidecar)


@contextmanager
def _sources(
    tmp_path: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    anchor: CumulativeAnchor,
) -> Iterator[LazySources]:
    if engine == "duckdb":
        with _duckdb(tmp_path, anchor) as sources:
            yield sources
    else:
        with _remote(tmp_path, engine, monkeypatch, anchor) as sources:
            yield sources


def _timed(
    sources: LazySources,
    *,
    scope: object = WINDOW,
) -> LogicalMetricDataset:
    """Observe the running total over the fixture date axis at day grain."""
    observed = sources.observe(RUNNING, time_scope=scope)
    return observed.with_time_axis(TIME, grain=grain("day")).aggregate()


def _rows(frame: pd.DataFrame) -> list[tuple[str, float]]:
    ordered = frame.sort_values("order_time")
    return [
        (None if pd.isna(value) else str(value)[:10], float(total))
        for value, total in zip(ordered.order_time, ordered.running, strict=True)
    ]


def _retained_rows(
    sources: LazySources, checkpoint: MaterializedMetricDataset
) -> list[dict[str, object]]:
    """Read one scoped checkpoint's retained part rows from local artifact state.

    The check-pointed artifact keeps exactly one part whose columns are the
    published spine plus the fold state: per-component sums and counts keyed by
    per-session digests, plus the cumulative coverage columns from
    ``coverage_columns``. Rows are ordered by their civil date spine value.
    """
    runtime = sources._owner.action_port
    assert isinstance(runtime, DatasetRuntime)
    semantics = checkpoint.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    record = runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 1
    part = record.descriptor.retained_parts[0]
    schema = part_schema(runtime.store.project_root, part)
    rows = [
        row
        for batch in read_part_batches(runtime.store.project_root, part, expected_schema=schema)
        for row in batch.to_pylist()
    ]
    return sorted(rows, key=lambda row: str(row["order_time"]))


def _fold_authority(checkpoint: MaterializedMetricDataset) -> MetricFoldAuthorityV1:
    """Return the single cumulative fold authority of a scoped checkpoint."""
    semantics = checkpoint.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    authority = semantics.metric_folds[0]
    assert authority.cumulative
    return authority


def _assert_complete_coverage(
    checkpoint: MaterializedMetricDataset, rows: list[dict[str, object]]
) -> None:
    """Pin whole-day complete coverage and the fold counts per retained row."""
    authority = _fold_authority(checkpoint)
    endpoint, _start, _end, seconds, complete = coverage_columns(authority)
    non_null: list[object] = []
    total: list[object] = []
    for row in rows:
        assert row[seconds] == 86400.0
        assert row[complete] is True
        assert row[endpoint] is not None
        for name, value in row.items():
            if name.endswith("non_null_count"):
                non_null.append(value)
            elif name.endswith("row_count"):
                total.append(value)
    assert non_null == [3, 2, 1]
    assert total == [4, 3, 1]


@pytest.mark.parametrize("engine", ENGINES)
def test_all_history_running_total_executes_on_source(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every prior contribution accumulates; NULLs and the NULL-day row stay out."""
    with _sources(tmp_path, engine, monkeypatch, "all_history") as sources:
        assert _rows(_timed(sources).execute().to_pandas()) == list(ALL_HISTORY)


@pytest.mark.parametrize("engine", ENGINES)
def test_grain_to_date_month_resets_across_months(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """March restarts at 5.0 where all-history would show 145.0."""
    with _sources(tmp_path, engine, monkeypatch, ("grain_to_date", "month")) as sources:
        assert _rows(_timed(sources).execute().to_pandas()) == list(MONTH_TO_DATE)


@pytest.mark.parametrize("engine", ENGINES)
def test_trailing_two_day_window_executes_on_source(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half-open (end - 2d, end] window keeps only its own contributions."""
    with _sources(tmp_path, engine, monkeypatch, ("trailing", 2, "day")) as sources:
        assert _rows(_timed(sources).execute().to_pandas()) == list(TRAILING_TWO_DAYS)


@pytest.mark.parametrize("engine", ENGINES)
def test_scope_start_cuts_accumulation_before_window(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late boundary-aligned scope start clips the spine, never the coverage.

    The scope start lands exactly on a day-bucket boundary, so the clip drops
    whole buckets without partially covering any kept one: every retained row
    keeps whole-day complete coverage seconds and its exact source fold counts.
    """
    with _sources(tmp_path, engine, monkeypatch, ("trailing", 2, "day")) as sources:
        checkpoint = _timed(sources, scope=TRAILING_CUT).execute()
        assert isinstance(checkpoint, MaterializedMetricDataset)
        assert _rows(checkpoint.to_pandas()) == list(TRAILING_CUT_EXPECTED)
        state = _retained_rows(sources, checkpoint)
        assert [str(row["order_time"])[:10] for row in state] == [
            row[0] for row in TRAILING_CUT_EXPECTED
        ]
        _assert_complete_coverage(checkpoint, state)


@pytest.mark.parametrize("engine", ENGINES)
def test_scope_endpoint_inside_last_bucket_marks_partial_coverage(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-bucket scope endpoint clips the last bucket's coverage only.

    With the exclusive endpoint at 2026-02-04 12:00, the kept 02-04 day bucket
    is only half covered: its coverage spans 02-04 00:00 -> 02-04 12:00 with
    43200.0 seconds and an incomplete marking, while the fully covered buckets
    keep whole-day seconds and stay complete. The displayed totals equal the
    unscoped trailing run because each bucket's own rows precede the endpoint.
    """
    with _sources(tmp_path, engine, monkeypatch, ("trailing", 2, "day")) as sources:
        checkpoint = _timed(sources, scope=PARTIAL_SCOPE).execute()
        assert isinstance(checkpoint, MaterializedMetricDataset)
        assert _rows(checkpoint.to_pandas()) == list(PARTIAL_SCOPE_EXPECTED)
        authority = _fold_authority(checkpoint)
        endpoint, start, end, seconds, complete = coverage_columns(authority)
        state = _retained_rows(sources, checkpoint)
        assert [str(row["order_time"])[:10] for row in state] == [
            row[0] for row in PARTIAL_SCOPE_EXPECTED
        ]
        expected_coverage = (
            (86400.0, True, "2026-02-02 00:00:00", "2026-02-03 00:00:00"),
            (86400.0, True, "2026-02-03 00:00:00", "2026-02-04 00:00:00"),
            (43200.0, False, "2026-02-04 00:00:00", "2026-02-04 12:00:00"),
        )
        non_null: list[object] = []
        total: list[object] = []
        for row, (day_seconds, day_complete, day_start, day_end) in zip(
            state, expected_coverage, strict=True
        ):
            assert row[seconds] == day_seconds
            assert row[complete] is day_complete
            assert str(row[start]) == day_start and str(row[end]) == day_end
            assert str(row[endpoint]) == day_end
            for name, value in row.items():
                if name.endswith("non_null_count"):
                    non_null.append(value)
                elif name.endswith("row_count"):
                    total.append(value)
        assert non_null == [2, 3, 2]
        assert total == [2, 4, 3]


@pytest.mark.parametrize("engine", ENGINES)
def test_empty_selection_publishes_true_zero_row(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty selected coverage state publishes one true-zero row, not absence.

    This is the retained fold-matrix oracle's empty shape: a ``where`` selecting
    no rows folds to exactly one row whose value is NULL, whose coverage seconds
    are exactly 0.0 and incomplete, and whose support counts are zero — the
    "empty windows are true zero, never carried forward" contract, distinct from
    the calendar NULL-bucket row that still carries real sums.
    """
    with _sources(tmp_path, engine, monkeypatch, "all_history") as sources:
        runtime = sources._owner.action_port
        checkpoint = _timed(sources).execute()
        assert isinstance(checkpoint, MaterializedMetricDataset)
        empty = checkpoint.where(gt(RUNNING, 1000)).rollup(drop_time=True).execute()
        frame = empty.to_pandas()
        assert frame.shape[0] == 1 and frame["running"].isna().all()
        authority = empty.row_contract.family_semantics.metric_folds[0]
        endpoint, _start, _end, seconds, complete = coverage_columns(authority)
        record = runtime.store.artifact(empty.state.artifact_ref.ref)
        assert record is not None and len(record.descriptor.retained_parts) == 1
        part = record.descriptor.retained_parts[0]
        schema = part_schema(runtime.store.project_root, part)
        batches = tuple(read_part_batches(runtime.store.project_root, part, expected_schema=schema))
        assert sum(batch.num_rows for batch in batches) == 1
        state = next(batch.to_pylist()[0] for batch in batches if batch.num_rows)
        assert all(state[name] is None for name in (endpoint,))
        assert state[seconds] == 0.0 and state[complete] is False
        assert all(value == 0 for name, value in state.items() if name.endswith("count"))


@pytest.mark.parametrize("engine", ENGINES)
def test_remote_cumulative_artifact_rolls_up_locally(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote daily cumulative artifact folds its exact coverage locally."""
    with _sources(tmp_path, engine, monkeypatch, "all_history") as sources:
        checkpoint = _timed(sources).execute()
    assert isinstance(checkpoint, MaterializedMetricDataset)
    folded = checkpoint.rollup(drop_time=True).execute()
    assert folded.to_pandas()["running"].tolist() == [145.0]


REMOTE_ENGINES: tuple[Engine, ...] = ("sqlite", "postgres", "mysql", "clickhouse", "trino")


@pytest.mark.parametrize("engine", REMOTE_ENGINES)
def test_distinct_membership_axis_with_cumulative_stays_rejected(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cumulative over a hidden-axis distinct membership keeps :421's refusal."""
    if engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as postgres_registry

        registry, sidecar = postgres_registry("unused_cumulative", monkeypatch)
    else:
        assert engine in {"sqlite", "mysql", "trino", "clickhouse"}
        registry, sidecar = registry_for(
            tmp_path / "unused.db" if engine == "sqlite" else Path("unused.duckdb"),
            engine=engine,
            table="unused_cumulative",
        )
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    metrics = dict(registry.metrics)
    metrics["sales.distinct_orders"] = replace(
        metrics["sales.order_count"],
        semantic_id="sales.distinct_orders",
        name="distinct_orders",
        aggregation="count_distinct",
        measure=None,
        aggregation_target="sales.orders",
        aggregation_target_kind="entity",
    )
    metrics[RUNNING.path] = replace(
        metrics["sales.conversion_rate"],
        semantic_id=RUNNING.path,
        name="running",
        composition=CumulativeComposition("sales.distinct_orders", AXIS),
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    from marivo.analysis.materialization.admission import DatasetRuntime as Runtime

    runtime = Runtime.create(Path("unused-project") / engine, engine)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    observed = sources.observe(RUNNING, time_scope=WINDOW).with_time_axis(TIME, grain=grain("day"))
    reason = source_unsupported_reason(observed.aggregate(), engine)
    assert reason is not None and "membership" in reason


REMOTE_ENGINES_ONLY: tuple[Engine, ...] = ("sqlite", "postgres", "mysql", "clickhouse", "trino")


@pytest.mark.parametrize("engine", REMOTE_ENGINES_ONLY)
def test_source_private_quantile_next_to_cumulative_stays_rejected(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exact median beside a cumulative keeps the source-private refusal.

    The corrected diagnostic describes only the source-private state: the
    cumulative metric itself is admitted on this backend, so a refusal naming
    cumulative would be stale.
    """
    if engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as postgres_registry

        registry, sidecar = postgres_registry("unused_median", monkeypatch)
    else:
        assert engine in {"sqlite", "mysql", "trino", "clickhouse"}
        registry, sidecar = registry_for(
            tmp_path / "unused.db" if engine == "sqlite" else Path("unused.duckdb"),
            engine=engine,
            table="unused_median",
        )
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    metrics = dict(registry.metrics)
    metrics["sales.median_amount"] = replace(
        metrics["sales.revenue"],
        semantic_id="sales.median_amount",
        name="median_amount",
        aggregation="median",
    )
    metrics[RUNNING.path] = replace(
        metrics["sales.conversion_rate"],
        semantic_id=RUNNING.path,
        name="running",
        composition=CumulativeComposition(REVENUE.path, AXIS),
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path / "unused-project" / engine, engine)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    observed = sources.observe((RUNNING, ref.metric("sales.median_amount")), time_scope=WINDOW)
    reason = source_unsupported_reason(observed.aggregate(), engine)
    assert reason is not None
    assert "source-private" in reason and "cumulative" not in reason
