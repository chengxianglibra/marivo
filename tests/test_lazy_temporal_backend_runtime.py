"""Independent calendar and instant expectations across actual source readers."""

from pathlib import Path

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
from tests.lazy_scalar_type_fixtures import Engine
from tests.lazy_temporal_backend_fixtures import ENGINES, temporal_source
from tests.lazy_temporal_fixtures import AXIS

pytestmark = pytest.mark.runtime


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
