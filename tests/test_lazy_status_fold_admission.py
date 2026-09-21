"""Status-time fold qualification: admission boundaries and the local SQLite journey."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.operators.registry import implementation
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import AggKind, CumulativeComposition, TimeFoldIR
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_scalar_source_fixtures import registry_for

ENGINES = ("postgres", "mysql", "sqlite", "trino", "clickhouse")
# The per-backend fold kinds qualified for execution; every other engine x kind
# pair keeps its structured rejection until live probe evidence opens it.
OPENED: dict[str, frozenset[str]] = {
    "postgres": frozenset({"first", "last", "mean", "min", "max"}),
    "mysql": frozenset({"mean", "min", "max"}),
    "sqlite": frozenset({"first", "last", "mean", "min", "max"}),
    "trino": frozenset({"first", "last", "mean", "min", "max"}),
    "clickhouse": frozenset({"first", "last", "mean", "min", "max"}),
}
ENGINE: Literal["sqlite"] = "sqlite"
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
STATUS_AXIS = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-01", end="2026-02-05")

# Hand-computed from the seeded rows: per channel each distinct status
# timestamp is one bucket, so the per-channel amounts are a=(10, 30) and
# b=(100, 40); channel c holds only the null-amount status, whose spatial sum
# stays null. Fold first gives (10, 100), last (30, 40), mean (20, 70),
# min (10, 40), max (30, 100); the spatial sums are 110, 70, 90, 50 and 130.
KIND_TOTALS: dict[str, float] = {
    "first": 110.0,
    "last": 70.0,
    "mean": 90.0,
    "min": 50.0,
    "max": 130.0,
}
# Per-channel folded amounts behind the totals above.
KIND_CHANNELS: dict[str, list[float]] = {
    "first": [10.0, 100.0],
    "last": [30.0, 40.0],
    "mean": [20.0, 70.0],
    "min": [10.0, 40.0],
    "max": [30.0, 100.0],
}
# The all-null status channel publishes null under every fold kind.


def _fold_registry(
    engine: Literal["postgres", "mysql", "sqlite", "trino", "clickhouse"],
    fold: TimeFoldIR,
    *,
    sampled: bool = True,
    aggregation: AggKind = "sum",
    database: Path | None = None,
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Bind the orders measure to a sampled status axis with the given fold."""
    from tests.lazy_scalar_source_fixtures import fold_registry

    registry, sidecar = registry_for(
        database if database is not None else Path("unused.sqlite"), engine=engine
    )
    return fold_registry(
        registry,
        sidecar,
        fold,
        sampled=sampled,
        aggregation=aggregation,
    )


def _sources(registry: Registry, sidecar: CompiledExpressionSidecar):  # -> LazySources
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="status-fold-admission",
        store_id="status-fold-admission",
    )


@pytest.fixture
def sqlite_fold_database(tmp_path: Path) -> Path:
    import sqlite3

    database = tmp_path / "fold.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE orders (id INTEGER, tenant TEXT, customer_id INTEGER, "
            "order_id INTEGER, amount REAL, weight REAL, region TEXT, channel TEXT, "
            'day TIMESTAMP, start DATE, "end" DATE)'
        )
        connection.executemany(
            "INSERT INTO orders(id, customer_id, amount, weight, channel, day) "
            "VALUES (?,?,?,?,?,?)",
            [
                (1, 1, 10.0, 1.0, "a", "2026-02-02 09:00:00.000000"),
                (2, 1, 30.0, 2.0, "a", "2026-02-02 17:00:00.000000"),
                (3, 2, 100.0, 3.0, "b", "2026-02-03 12:00:00.000000"),
                (4, 2, None, 4.0, "c", "2026-02-03 08:15:00.000000"),
                (5, 2, 40.0, 5.0, "b", "2026-02-03 14:30:00.000000"),
            ],
        )
    return database


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "fold",
    [
        TimeFoldIR("mean"),
        TimeFoldIR("min"),
        TimeFoldIR("max"),
        TimeFoldIR("first"),
        TimeFoldIR("last"),
        TimeFoldIR("percentile", 0.9),
    ],
    ids=["mean", "min", "max", "first", "last", "percentile"],
)
def test_status_fold_admission_matches_qualified_kinds(
    engine: Literal["postgres", "mysql", "sqlite", "trino", "clickhouse"], fold: TimeFoldIR
) -> None:
    """Until a backend qualifies a fold kind its rejection stays structured."""
    registry, sidecar = _fold_registry(engine, fold)
    logical = (
        _sources(registry, sidecar)
        .observe(REVENUE, time_scope=WINDOW)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    registered = implementation(logical).for_backend(engine) is not None
    assert registered == (fold.kind in OPENED[engine])


@pytest.mark.parametrize("engine", ENGINES)
def test_cumulative_over_fold_base_keeps_structured_rejection(
    engine: Literal["postgres", "mysql", "sqlite", "trino", "clickhouse"],
) -> None:
    """The fold exemption never widens the cumulative requirement boundary.

    A cumulative composition whose base is a semi-additive measure carries both
    the cumulative and the status-time fold source requirements; only the
    cumulative endpoint-window lowering is shared, so the fold requirement
    keeps this shape rejected even on backends with qualified folds.
    """
    registry, sidecar = _fold_registry(engine, TimeFoldIR("last"))
    metrics = dict(registry.metrics)
    metrics["sales.running_last"] = replace(
        metrics["sales.conversion_rate"],
        semantic_id="sales.running_last",
        name="running_last",
        composition=CumulativeComposition(REVENUE.path, STATUS_AXIS.path),
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    logical = (
        _sources(registry, sidecar)
        .observe(ref.metric("sales.running_last"), time_scope=WINDOW)
        .aggregate()
    )
    from marivo.analysis.operators.registry import source_unsupported_reason

    reason = source_unsupported_reason(logical, engine)
    assert reason == "this Metric requires source-private state this backend has not qualified"


def test_percentile_fold_is_rejected_while_scalar_folds_may_open(
    tmp_path: Path,
) -> None:
    """The percentile tuple fold belongs to a later slice and stays rejected."""
    registry, sidecar = _fold_registry(ENGINE, TimeFoldIR("percentile", 0.9))
    logical = (
        _sources(registry, sidecar)
        .observe(REVENUE, time_scope=WINDOW)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    assert implementation(logical).for_backend(ENGINE) is None


def test_unsampled_status_axis_follows_the_same_qualified_kinds() -> None:
    """Fold admission is orthogonal to the axis parse sample interval."""
    registry, sidecar = _fold_registry(ENGINE, TimeFoldIR("last"), sampled=False)
    logical = _sources(registry, sidecar).observe(REVENUE, time_scope=WINDOW).aggregate()
    registered = implementation(logical).for_backend(ENGINE) is not None
    assert registered == ("last" in OPENED[ENGINE])


def _journey(
    tmp_path: Path,
    sqlite_fold_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: Literal["postgres", "mysql", "sqlite", "trino", "clickhouse"],
    kind: str,
) -> None:
    """Execute the real fold lowering end to end with hand-computed constants."""
    from tests.lazy_scalar_source_fixtures import capture_submissions

    submitted = capture_submissions(monkeypatch)
    registry, sidecar = _fold_registry(engine, TimeFoldIR(kind), database=sqlite_fold_database)
    runtime = DatasetRuntime.create(tmp_path / "project", f"fold-{engine}-{kind}")
    observed = runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
        REVENUE, time_scope=WINDOW
    )
    frame = observed.with_dimensions(CHANNEL).aggregate().execute().to_pandas()
    ordered = frame.sort_values("channel")
    assert ordered.revenue.tolist()[:2] == pytest.approx(KIND_CHANNELS[kind])
    assert ordered.revenue.iloc[2] is None or ordered.revenue.isna().iloc[2]
    # The spatial sum across channels after the temporal fold.
    assert float(ordered.revenue.dropna().sum()) == pytest.approx(KIND_TOTALS[kind])
    # The fold's status gate executed as a source validation and the primary
    # query carries the lowering's status column.
    assert any(
        role == "validation_batch" and "__mv_status" in sql
        for role, sql in runtime.statistics.statements
    )
    primary = [sql for role, sql in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "__mv_status" in primary[0]


@pytest.mark.parametrize("kind", ["first", "last", "mean", "min", "max"])
def test_sqlite_fold_spatial_sum_matches_hand_computed_constants(
    tmp_path: Path, sqlite_fold_database: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _journey(tmp_path, sqlite_fold_database, monkeypatch, "sqlite", kind)
