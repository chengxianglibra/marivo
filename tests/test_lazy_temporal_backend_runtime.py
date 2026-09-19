"""Independent calendar and instant expectations across actual source readers."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import StrptimeParse, TimestampParse
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_scalar_type_fixtures import Engine, source_writer
from tests.lazy_temporal_backend_fixtures import (
    ENGINES,
    hour_prefix_source,
    strptime_source,
    temporal_source,
)
from tests.lazy_temporal_fixtures import AXIS

pytestmark = pytest.mark.runtime

# Backends with live execution evidence for the parsed-time gate.  PostgreSQL
# joined the gate in the same commit as its ``*_support`` flag, and the two
# remaining engines stay closed: Trino has no reachable service.
PARSED_ENGINES: tuple[Engine, ...] = ("duckdb", "sqlite", "mysql", "clickhouse", "postgres")
HOUR_AXIS = "sales.orders.hour"


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", ["day", "gap", "fold"])
def test_native_temporal_buckets(
    engine: Engine, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values, report, start, end, unit, expected = {
        "day": (
            ("2026-07-01 15:59:59.999999", "2026-07-01 16:00:00.000000"),
            "Asia/Shanghai",
            "2026-07-01",
            "2026-07-03",
            "day",
            {"2026-07-01 00:00:00": 2, "2026-07-02 00:00:00": 3},
        ),
        "gap": (
            ("2026-03-08 06:30:00.000000", "2026-03-08 07:30:00.000000"),
            "America/New_York",
            "2026-03-08",
            "2026-03-09",
            "hour",
            {"2026-03-08 01:00:00": 2, "2026-03-08 03:00:00": 3},
        ),
        "fold": (
            ("2026-11-01 05:30:00.000000", "2026-11-01 06:30:00.000000"),
            "America/New_York",
            "2026-11-01",
            "2026-11-02",
            "hour",
            {"2026-11-01 01:00:00": 5},
        ),
    }[case]
    with temporal_source(engine, tmp_path, monkeypatch, values) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "temporal", report_timezone=report)
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"), time_scope=time_scope(start=start, end=end))
            .with_time_axis(
                ref.time_dimension(AXIS), grain=grain("day" if unit == "day" else "hour")
            )
            .aggregate()
        )
        from tests.lazy_scalar_source_fixtures import capture_submissions

        captured = capture_submissions(monkeypatch)
        result = logical.execute().to_pandas()
        if engine in {"sqlite", "mysql", "clickhouse", "trino"}:
            assert [item["sql"] for item in captured] == [
                item.sql for item in runtime.statistics.submissions
            ]
        assert {str(row.order_time): row.revenue for row in result.itertuples()} == expected
        from tests.lazy_temporal_backend_fixtures import save_receipt

        save_receipt(engine, case, runtime, expected, captured)
        assert runtime.statistics.primary_queries == 1
        assert all(item.state == "succeeded" for item in runtime.statistics.submissions)
        assert not any(item.role == "source_timezone" for item in runtime.statistics.submissions)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("value", ["2026-03-08 02:30:00.000001", "2026-11-01 01:30:00.000001"])
def test_naive_dst_gap_and_fold_are_rejected(
    engine: Engine, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    with temporal_source(engine, tmp_path, monkeypatch, (value,), zone="America/New_York") as (
        registry,
        sidecar,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "invalid-time", report_timezone="UTC")
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "clickhouse"])
def test_physical_instants_skip_reader_probe(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporal_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2026-07-01 15:59:59.999999", "2026-07-01 16:00:00.000000"),
        aware=True,
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "instants", report_timezone="Asia/Shanghai"
        )
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        result = logical.execute().to_pandas()
        assert {str(row.order_time): row.revenue for row in result.itertuples()} == {
            "2026-07-01 00:00:00": 2,
            "2026-07-02 00:00:00": 3,
        }
        assert not any(item.role == "source_timezone" for item in runtime.statistics.submissions)


@pytest.mark.parametrize("engine", ENGINES)
def test_microsecond_scope_predicate_and_cold_read(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    from marivo.analysis.observation.predicates import all_of, eq, gte, is_null, lt
    from tests.lazy_scalar_type_fixtures import arrow_result, cold_check

    with temporal_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2024-02-29 12:00:00.000000", "2024-02-29 12:00:00.000001", None),
    ) as (registry, sidecar):
        project = tmp_path / "project"
        runtime = DatasetRuntime.create(project, "microsecond", report_timezone="UTC")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        scoped = (
            sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start="2024-02-29T12:00:00.000001+00:00", end="2024-02-29T12:00:00.000002+00:00"
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
            .execute()
        )
        assert scoped.to_pandas().revenue.tolist() == [3]
        by_value = (
            sources.observe(ref.metric("sales.revenue"))
            .with_dimensions(ref.dimension("sales.orders.stamp"))
            .aggregate()
        )
        selected = by_value.where(
            eq(ref.dimension("sales.orders.stamp"), datetime(2024, 2, 29, 12, 0, 0, 1))
        ).execute()
        assert selected.to_pandas().revenue.tolist() == [3]
        import sqlglot
        from sqlglot import expressions as exp

        primaries = [item for item in runtime.statistics.submissions if item.role == "primary"]
        assert len(primaries) == 1 and primaries[0].domain == "source"
        query = sqlglot.parse_one(primaries[0].sql, read=engine)
        assert any(
            isinstance(condition.left, exp.Column)
            and condition.left.name == "stamp"
            and not isinstance(condition.right, exp.Column)
            for clause in query.find_all(exp.Where)
            for condition in clause.find_all(exp.EQ)
        )
        stamp = ref.dimension("sales.orders.stamp")
        ranged = by_value.where(
            all_of(
                gte(stamp, datetime(2024, 2, 29, 12, 0, 0, 1)),
                lt(stamp, datetime(2024, 2, 29, 12, 0, 0, 2)),
            )
        ).execute()
        assert ranged.to_pandas().revenue.tolist() == [3]
        assert by_value.where(is_null(stamp)).execute().to_pandas().revenue.tolist() == [4]
        artifact = scoped.state.artifact_ref.ref
        table = arrow_result(runtime, artifact)
    cold_check(project, runtime.session_ref, artifact, table, tmp_path)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("report", ["Asia/Kathmandu", "UTC+05:45"])
def test_non_hour_report_timezone(
    engine: Engine, report: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporal_source(
        engine, tmp_path, monkeypatch, ("2026-07-01 18:14:59.999999", "2026-07-01 18:15:00.000000")
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "offset", report_timezone=report)
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert {str(row.order_time): row.revenue for row in result.itertuples()} == {
            "2026-07-01 00:00:00": 2,
            "2026-07-02 00:00:00": 3,
        }


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "clickhouse"])
@pytest.mark.parametrize("precision", [0, 3])
def test_native_precision_is_preserved_in_time_methods(
    engine: Engine, precision: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = ("2026-07-01 15:59:59.000000", "2026-07-01 16:00:00.000000")
    with temporal_source(engine, tmp_path, monkeypatch, values, precision=precision) as (
        registry,
        sidecar,
    ):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "precision", report_timezone="Asia/Shanghai"
        )
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert {str(row.order_time): row.revenue for row in result.itertuples()} == {
            "2026-07-01 00:00:00": 2,
            "2026-07-02 00:00:00": 3,
        }


def test_clickhouse_physical_non_utc_instants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporal_source(
        "clickhouse",
        tmp_path,
        monkeypatch,
        ("2026-07-01 23:59:59.999999", "2026-07-02 00:00:00.000000"),
        aware=True,
        physical_zone="Asia/Shanghai",
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "physical-zone", report_timezone="Asia/Shanghai"
        )
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start="2026-07-01T16:00:00+00:00", end="2026-07-01T16:00:00.000001+00:00"
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert result.revenue.tolist() == [3]


@pytest.mark.parametrize("engine", ENGINES)
def test_source_offline_cold_hour_to_day_fold(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    import sys

    with temporal_source(
        engine, tmp_path, monkeypatch, ("2026-03-08 06:30:00.000000", "2026-03-08 07:30:00.000000")
    ) as (registry, sidecar):
        project = tmp_path / "project"
        runtime = DatasetRuntime.create(project, "fold", report_timezone="America/New_York")
        produced = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
            .execute()
        )
        artifact = produced.state.artifact_ref.ref
    code = """
import sys
from pathlib import Path
from marivo.analysis import grain
from marivo.analysis.materialization.admission import DatasetRuntime
import marivo.analysis.materialization.admission as admission

def no_source(*args, **kwargs):
    raise AssertionError('cold continuation accessed source')
admission._build_backend_from_effective = no_source
runtime = DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2])
source = runtime.artifact(sys.argv[3])
original = runtime.store.artifact(sys.argv[3]).descriptor.temporal_execution
folded = source.rollup(grain=grain('day')).execute()
assert folded.to_pandas().revenue.tolist() == [5]
assert not any(item.domain == 'source' for item in runtime.statistics.submissions)
assert runtime.store.artifact(folded.state.artifact_ref.ref).descriptor.temporal_execution == original
"""
    subprocess.run(
        [sys.executable, "-c", code, str(project), runtime.session_ref, artifact],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": "Pacific/Honolulu"},
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_reader_timezone_and_null_scope(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    with temporal_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2025-12-31 23:59:59.999999", "2026-01-01 00:00:00.000000", None),
        zone=None,
    ) as (registry, sidecar):
        from tests.lazy_scalar_source_fixtures import capture_submissions
        from tests.lazy_temporal_backend_fixtures import save_receipt

        captured = capture_submissions(monkeypatch)
        runtime = DatasetRuntime.create(tmp_path / "project", "reader", report_timezone="UTC")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-01-01", end="2026-01-02"),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
            .execute()
        )
        assert result.to_pandas().revenue.tolist() == [3]
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None and record.descriptor.temporal_execution is not None
        assert {
            axis.source
            for temporal in record.descriptor.temporal_execution
            for axis in temporal.axes
        } == {"system_fallback" if engine == "sqlite" else "engine"}
        assert sum(item.role == "source_timezone" for item in runtime.statistics.submissions) == (
            0 if engine == "sqlite" else 1
        )
        if engine in {"sqlite", "mysql", "clickhouse", "trino"}:
            assert [item["sql"] for item in captured] == [
                item.sql for item in runtime.statistics.submissions
            ]
        save_receipt(engine, "reader", runtime, {"2026-01-01": 3}, captured)
        population = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .population(
                ref.entity("sales.orders"),
                time_scope=time_scope(start="2026-01-01", end="2026-01-02"),
            )
            .execute()
        )
        assert population.to_pandas().entity_identity.tolist() == [(2,)]


@pytest.mark.parametrize("year", [1960, 2200])
def test_clickhouse_datetime64_bucket_does_not_narrow_to_datetime(
    year: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporal_source(
        "clickhouse", tmp_path, monkeypatch, (f"{year}-07-01 16:00:00.000001",)
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "wide-datetime", report_timezone="Asia/Shanghai"
        )
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert str(result.order_time.iloc[0]) == f"{year}-07-02 00:00:00"
        assert result.revenue.tolist() == [2]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("value", ["2026-11-01 01:30:00.000001", "2026-07-01 12:00:00.000001"])
@pytest.mark.parametrize("authority", ["source", "report"])
def test_runtime_zoneinfo_disagreement_fails_before_primary(
    engine: Engine, value: str, authority: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import tzinfo
    from zoneinfo import ZoneInfo

    from marivo.analysis.materialization import temporal_validation
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.observation.temporal import time_zone as original

    def different_rules(name: str) -> tzinfo:
        # Inject a different runtime rule set without depending on the machine's
        # tzdata release. The engine continues to use its own Casablanca rules.
        return ZoneInfo("America/New_York") if name == "Africa/Casablanca" else original(name)

    monkeypatch.setattr(temporal_validation, "time_zone", different_rules)
    with temporal_source(
        engine,
        tmp_path,
        monkeypatch,
        (value,),
        zone="Africa/Casablanca" if authority == "source" else "UTC",
    ) as (
        registry,
        sidecar,
    ):
        runtime = DatasetRuntime.create(
            tmp_path / "project",
            "rule-disagreement",
            report_timezone="UTC" if authority == "source" else "Africa/Casablanca",
        )
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        with pytest.raises(MaterializationError, match="runtime ZoneInfo"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
        assert any(
            item.role == "engine_check.temporal_rules" for item in runtime.statistics.submissions
        )


@pytest.mark.parametrize("engine", ENGINES)
def test_non_hour_naive_fold_is_rejected(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    with temporal_source(
        engine, tmp_path, monkeypatch, ("2026-04-05 01:45:00.000001",), zone="Australia/Lord_Howe"
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "half-hour-fold", report_timezone="UTC"
        )
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0


def _daily(
    runtime: DatasetRuntime, registry: Registry, sidecar: CompiledExpressionSidecar
) -> LogicalMetricDataset:
    """Observe one Metric on the order_time axis as a day axis."""
    return (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"))
        .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
        .aggregate()
    )


@contextmanager
def _validity_source(
    path: Path,
    rows: tuple[tuple[str, str], ...],
    *,
    parse: StrptimeParse | TimestampParse,
) -> Iterator[tuple[Registry, CompiledExpressionSidecar]]:
    """Declare ``sales.validity`` on DuckDB with ``parse`` on both endpoints.

    DuckDB is the only engine that admits a time-bearing version axis: the other
    five refuse one through ``semantic version selection requires qualified
    native civil-date axes``, so this defect has no reachable path there.
    """
    database = path / "source.db"
    name = "c3c_" + uuid4().hex
    text = isinstance(parse, StrptimeParse)
    physical = "VARCHAR" if text else "TIMESTAMP"
    declared = "string" if text else "timestamp(6)"
    with source_writer("duckdb", database) as execute:
        execute(f'CREATE TABLE {name} (id BIGINT, start {physical}, "end" {physical})')
        execute(
            f"INSERT INTO {name} VALUES "
            + ",".join(f"({index},'{start}','{end}')" for index, (start, end) in enumerate(rows, 1))
        )
    registry, sidecar = make_execution_registry(database)
    entity = registry.entities["sales.validity"]
    assert isinstance(entity.source, TableSourceIR)
    registry = replace(
        registry,
        entities={
            **registry.entities,
            entity.semantic_id: replace(
                entity,
                source=replace(
                    entity.source,
                    table=name,
                    columns=tuple(
                        (key, replace(binding, data_type=declared))
                        if key in {"start", "end"}
                        else (key, binding)
                        for key, binding in entity.source.columns
                    ),
                ),
            ),
        },
        dimensions={
            **registry.dimensions,
            **{
                f"sales.validity.{bound}": replace(
                    registry.dimensions[f"sales.validity.{bound}"],
                    parse=parse,
                    granularity="second",
                )
                for bound in ("valid_from", "valid_to")
            },
        },
    )
    registry.freeze()
    try:
        yield registry, sidecar
    finally:
        with source_writer("duckdb", database) as execute:
            execute(f"DROP TABLE IF EXISTS {name}")


def _composite(
    runtime: DatasetRuntime,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    *,
    start: str,
    end: str,
) -> LogicalMetricDataset:
    return (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"), time_scope=time_scope(start=start, end=end))
        .with_time_axis(ref.time_dimension(HOUR_AXIS), grain=grain("hour"))
        .aggregate()
    )


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_strptime_date_only_axis_executes(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date-only format is a civil date, so the source day is the report day.

    Every expectation is hand-computed from the authored cells: no timezone can
    move a civil date across a day boundary, so the buckets are exactly the
    three declared days.
    """
    with strptime_source(
        engine, tmp_path, monkeypatch, ("20260701", "20260702", "20260703"), fmt="%Y%m%d"
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "parsed-date", report_timezone="UTC")
        result = _daily(runtime, registry, sidecar).execute()
        assert {str(row.order_time): row.revenue for row in result.to_pandas().itertuples()} == {
            "2026-07-01": 2,
            "2026-07-02": 3,
            "2026-07-03": 4,
        }
        assert runtime.statistics.primary_queries == 1


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_strptime_time_bearing_axis_executes(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A time-bearing format is an instant in the declared zone, not a wall clock.

    Both cells are inside one UTC day but straddle Shanghai midnight, so a UTC
    report merges them into one bucket of 5 while a Shanghai report keeps them
    apart.  That difference is what proves the axis is an instant rather than a
    wall clock read in the report zone.
    """
    with strptime_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2026-07-01 15:59:00", "2026-07-01 16:01:00"),
        fmt="%Y-%m-%d %H:%M:%S",
        timezone="UTC",
    ) as (registry, sidecar):
        shanghai = DatasetRuntime.create(
            tmp_path / "shanghai", "parsed-instant", report_timezone="Asia/Shanghai"
        )
        local = _daily(shanghai, registry, sidecar).execute()
        assert {
            str(row.order_time)[:10]: row.revenue for row in local.to_pandas().itertuples()
        } == {"2026-07-01": 2, "2026-07-02": 3}
        utc = DatasetRuntime.create(tmp_path / "utc", "parsed-instant", report_timezone="UTC")
        instant = _daily(utc, registry, sidecar).execute()
        assert {
            str(row.order_time)[:10]: row.revenue for row in instant.to_pandas().itertuples()
        } == {"2026-07-01": 5}


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_strptime_malformed_cell_fails_before_publication(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cell the declared format cannot read never becomes a NULL coordinate.

    SQLite, MySQL and ClickHouse answer an unreadable cell with NULL, so without
    the compiled assertion the row would silently leave the time axis.  The
    failure must be Marivo's own error, never a driver's.

    MySQL reports the cell through its adapter's pre-existing result-warning
    guard rather than through the axis assertion: ``STR_TO_DATE`` answers NULL
    *and* raises an ``Incorrect datetime value`` warning, and the adapter turns
    any warning into a structured error when the statement's rows are submitted,
    so the assertion's own row count is never read.  The outcome is the same
    contract -- structured, before publication and without an Artifact -- so the
    engine keeps one owner per fact instead of gaining a second check.

    PostgreSQL is the recorded exception and is asserted separately below: its
    ``TO_TIMESTAMP`` is lenient, so this cell is outside the scope of this test.
    """
    if engine == "postgres":
        pytest.skip("PostgreSQL TO_TIMESTAMP leniency is asserted by its own test")
    with strptime_source(
        engine, tmp_path, monkeypatch, ("20260701", "2026070x", "20260703"), fmt="%Y%m%d"
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "parsed-bad", report_timezone="UTC")
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        with pytest.raises(Exception) as failure:
            logical.execute()
        if engine == "duckdb":
            # DuckDB raises on the same cell, so no NULL exists to assert.
            assert "parse" in str(failure.value).lower()
        elif engine == "mysql":
            assert isinstance(failure.value, MaterializationError)
            assert failure.value.received == "MySQL reported 1 statement warnings"
        else:
            assert isinstance(failure.value, MaterializationError)
            assert failure.value.received == (
                "source validation failed: temporal.strptime_format." + AXIS
            )
        assert runtime.statistics.primary_queries == 0
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


def test_postgres_lenient_to_timestamp_merges_an_unreadable_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded PostgreSQL limitation, pinned so a change to it is visible.

    ``to_timestamp('2026070x', 'YYYYMMDD')`` answers ``2026-01-07``: the
    unreadable tail is dropped and the leading ``07`` is read as a month, so the
    cell publishes at a wrong instant instead of failing.  No post-parse NULL
    check can see that, and Marivo deliberately does not round-trip the source to
    compare parsed values, so the acceptance record states this as a limitation
    rather than a guard.  This test exists to make a future PostgreSQL behavior
    change fail loudly instead of silently altering what that record means.
    """
    with strptime_source(
        "postgres", tmp_path, monkeypatch, ("20260701", "2026070x", "20260703"), fmt="%Y%m%d"
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "parsed-lenient", report_timezone="UTC"
        )
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        assert {
            str(row.order_time): row.revenue for row in logical.execute().to_pandas().itertuples()
        } == {"2026-07-01": 5, "2026-07-03": 4}


# The engines whose declared strptime zone reaches execution as an authority.
# ClickHouse refuses a parse declaring a zone other than the physical column's
# own (``matching declared and physical timezones``); its rejected cell is a
# compile-time refusal, so there is no unpublished-Artifact path to assert.
DECLARED_ZONE_ENGINES: tuple[Engine, ...] = ("duckdb", "sqlite", "mysql", "postgres")


@pytest.mark.parametrize("engine", DECLARED_ZONE_ENGINES)
@pytest.mark.parametrize(
    ("cell", "case"), [("2026-11-01 01:30:00", "fold"), ("2026-03-08 02:30:00", "gap")]
)
def test_parsed_naive_dst_gap_and_fold_are_rejected(
    engine: Engine, cell: str, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parsed wall clock owns the same gap/fold contract as a native one.

    The declared parse zone is the source's read authority, so the repeated
    ``case`` hour and the missing one are exactly what the native axis already
    refuses; the physical column being text must not change the verdict.
    """
    with strptime_source(
        engine,
        tmp_path,
        monkeypatch,
        (cell,),
        fmt="%Y-%m-%d %H:%M:%S",
        timezone="America/New_York",
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "parsed-dst", report_timezone="UTC")
        logical = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize(
    ("cell", "case"), [("2026-11-01 01:30:00", "fold"), ("2026-03-08 02:30:00", "gap")]
)
def test_parsed_validity_endpoint_dst_gap_and_fold_are_rejected(
    cell: str, case: str, tmp_path: Path
) -> None:
    """A selected validity endpoint is a wall clock the guard must reach.

    No payload field names this axis: the population version filter reads
    ``valid_to`` straight through the axis' own parser, so a repeated or missing
    civil time there can silently pick an instant exactly as on a time axis.
    DuckDB is the only engine that admits a time-bearing validity axis, and the
    declaration matches the parsed time-axis case so the two verdicts agree.
    The ``case`` id is what names the ambiguous row in the parameter list.
    """
    with _validity_source(
        tmp_path,
        (("2026-01-01 00:00:00", cell),),
        parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="America/New_York"),
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "validity-dst", report_timezone="UTC")
        logical = runtime.sources(semantic_registry=registry, sidecar=sidecar).population(
            ref.entity("sales.validity"),
            time_scope=time_scope(start="2025-01-01", end="2027-01-01"),
        )
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


def test_native_validity_endpoint_dst_fold_is_rejected(tmp_path: Path) -> None:
    """The native validity control: the same cell on a timestamp column.

    It already reaches the guard through ``temporal_execution``; pinning it here
    keeps the parsed case above honest about what the widened dispatch added.
    """
    with _validity_source(
        tmp_path,
        (("2026-01-01 00:00:00", "2026-11-01 01:30:00"),),
        parse=TimestampParse(timezone="America/New_York"),
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "validity-native", report_timezone="UTC"
        )
        logical = runtime.sources(semantic_registry=registry, sidecar=sidecar).population(
            ref.entity("sales.validity"),
            time_scope=time_scope(start="2025-01-01", end="2027-01-01"),
        )
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0


def test_parsed_validity_endpoint_with_unambiguous_cells_executes(tmp_path: Path) -> None:
    """The widened guard does not refuse an unambiguous parsed validity endpoint.

    ``2026-07-01 12:00:00`` exists exactly once in the declared zone, so the only
    endpoint check left is the ordinary closure comparison, and the scope end
    falls inside the declared interval so the row is selected.
    """
    with _validity_source(
        tmp_path,
        (("2026-01-01 00:00:00", "2026-07-01 12:00:00"),),
        parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="America/New_York"),
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(
            tmp_path / "project", "validity-clean", report_timezone="UTC"
        )
        population = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .population(
                ref.entity("sales.validity"),
                time_scope=time_scope(start="2026-01-01", end="2026-06-01"),
            )
            .execute()
        )
        assert population.to_pandas().entity_identity.tolist() == [(1,)]


@pytest.mark.parametrize(("prefix", "hour"), [("2026-11-01", "01"), ("2026-03-08", "02")])
def test_hour_prefix_naive_dst_gap_and_fold_are_rejected(
    prefix: str, hour: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reconstructed ``prefix + hour`` cell is the wall clock under test.

    ``2026-11-01 01`` repeats and ``2026-03-08 02`` does not exist in the read
    zone, so the composite axis must refuse both instead of adding the stored
    hour to the prefix midnight inside the engine's own localization rules.

    Only SQLite reaches this path here: it has no timezone probe, so its reader
    authority is the process zone this test pins.  The remaining engines report
    their session zone (UTC), where neither cell is a gap or a fold.
    """
    with hour_prefix_source("sqlite", tmp_path, monkeypatch, (prefix,), (hour,)) as (
        registry,
        sidecar,
    ):
        monkeypatch.setenv("TZ", "America/New_York")
        runtime = DatasetRuntime.create(tmp_path / "project", "prefix-dst", report_timezone="UTC")
        logical = _composite(runtime, registry, sidecar, start="2026-01-01", end="2027-01-01")
        with pytest.raises(MaterializationError, match=r"temporal\.local_time"):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_hour_prefix_composite_axis_executes(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composite axis is the civil-date prefix plus the stored hour.

    Expectations are hand-computed: the prefix is a native civil date and the
    hour a plain integer literal, so each reported hour is exactly prefix
    midnight shifted by that stored hour.
    """
    with hour_prefix_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2026-07-01", "2026-07-01", "2026-07-02"),
        ("15", "16", "15"),
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "prefix", report_timezone="UTC")
        result = _composite(
            runtime, registry, sidecar, start="2026-07-01", end="2026-07-04"
        ).execute()
        assert {str(row.hour): row.revenue for row in result.to_pandas().itertuples()} == {
            "2026-07-01 15:00:00": 2,
            "2026-07-01 16:00:00": 3,
            "2026-07-02 15:00:00": 4,
        }
        assert runtime.statistics.primary_queries == 1


@pytest.mark.parametrize("engine", PARSED_ENGINES)
@pytest.mark.parametrize("cell", ["oops", "99", "-3", "", " 15", "007"])
def test_illegal_hour_cell_fails_before_publication_on_every_parsed_backend(
    engine: Engine, cell: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One 0-23 integer-literal contract for both hour column families.

    SQLite and MySQL share a silent-coercion family whose implicit cast turns a
    malformed cell into hour 0, so the verdict is made on the raw cell.  The
    rejection must name the contract rather than surface a driver exception.
    """
    with hour_prefix_source(
        engine, tmp_path, monkeypatch, ("2026-07-01", "2026-07-01"), (cell, "16")
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "prefix-bad", report_timezone="UTC")
        with pytest.raises(MaterializationError) as failure:
            (_composite(runtime, registry, sidecar, start="2026-07-01", end="2026-07-04").execute())
        assert failure.value.received == "source validation failed: temporal.hour_range"
        assert "0-23" in (failure.value.expected or "")
        assert HOUR_AXIS in str(failure.value.hint)
        assert failure.value.__cause__ is None
        assert runtime.statistics.primary_queries == 0
        assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_string_hour_cells_are_never_published_as_hour_zero(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coercion family must not publish a malformed cell as hour 00.

    A malformed cell is the only witness separating a rejected value from a
    coerced one, and the well-formed neighbour proves the axis is reachable.
    """
    with hour_prefix_source(
        engine, tmp_path, monkeypatch, ("2026-07-01", "2026-07-01"), ("oops", "16")
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "coercion", report_timezone="UTC")
        with pytest.raises(MaterializationError):
            (_composite(runtime, registry, sidecar, start="2026-07-01", end="2026-07-04").execute())
        assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_out_of_range_integer_hour_column_is_rejected(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An integer hour column keeps the same open 0-23 range as the text column."""
    with hour_prefix_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2026-07-01", "2026-07-01", "2026-07-01"),
        (15, 16, 24),
        hour_physical="BIGINT",
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "int-hour", report_timezone="UTC")
        with pytest.raises(MaterializationError) as failure:
            (_composite(runtime, registry, sidecar, start="2026-07-01", end="2026-07-04").execute())
        assert failure.value.received == "source validation failed: temporal.hour_range"
        assert runtime.statistics.primary_queries == 0


@pytest.mark.parametrize("engine", PARSED_ENGINES)
def test_integer_hour_cells_in_range_execute(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Physical type must not change the admitted 0-23 value domain."""
    with hour_prefix_source(
        engine,
        tmp_path,
        monkeypatch,
        ("2026-07-01", "2026-07-02"),
        (0, 23),
        hour_physical="BIGINT",
    ) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "int-hour-ok", report_timezone="UTC")
        result = _composite(
            runtime, registry, sidecar, start="2026-07-01", end="2026-07-04"
        ).execute()
        assert {str(row.hour): row.revenue for row in result.to_pandas().itertuples()} == {
            "2026-07-01 00:00:00": 2,
            "2026-07-02 23:00:00": 3,
        }
