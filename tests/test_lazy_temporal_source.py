"""Independent numeric and boundary oracles for private temporal compilation."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.observation.temporal import ReportTimeAuthority
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.ir import (
    CumulativeComposition,
    DateParse,
    HourPrefixParse,
    StrptimeParse,
    TimestampParse,
)
from tests.lazy_execution_fixtures import assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_temporal_fixtures import AXIS, temporal_fixture


@pytest.mark.parametrize("representation", ["declared_utc", "native_naive", "strptime"])
def test_report_days(representation: str, tmp_path: Path) -> None:
    string = representation == "strptime"
    parse = (
        StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC")
        if string
        else (TimestampParse(timezone="UTC") if representation == "declared_utc" else None)
    )
    with temporal_fixture(
        tmp_path,
        physical="VARCHAR" if string else "TIMESTAMP",
        declared="string" if string else "timestamp(6)",
        parse=parse,
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert_compiled_validations(compiled.validations)
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert {str(row["order_time"])[:10]: row["revenue"] for row in rows} == {
            "2026-07-01": 1.0,
            "2026-07-02": 2.0,
        }
        assert compiled.temporal_execution is not None
        assert compiled.temporal_execution.axes[0].read_timezone == "UTC"
        assert compiled.temporal_execution.report.timezone == "Asia/Shanghai"


@pytest.mark.parametrize("partial,expected", [(False, 84), (True, 85)])
def test_hour_partition_endpoints(tmp_path: Path, partial: bool, expected: int) -> None:
    start = datetime(2024, 10, 11)
    with temporal_fixture(
        tmp_path,
        physical="VARCHAR",
        declared="string",
        report_zone="UTC",
        parse=StrptimeParse("%Y%m%d%H"),
        granularity="hour",
        values=tuple((start + timedelta(hours=i)).strftime("%Y%m%d%H") for i in range(85)),
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start=start.isoformat(),
                    end=(start + timedelta(hours=84, minutes=30 if partial else 0)).isoformat(),
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert len(rows) == expected
        comparison = logical.compare(logical)
        delta = compile_dataset(comparison, fixture.tables(comparison), read_timezone="UTC")
        paired = delta.expression.to_pyarrow().to_pylist()
        assert len(paired) == expected
        assert {row["coordinate_presence"] for row in paired} == {"matched"}
        assert all(
            row["current_value"] == row["baseline_value"] and row["delta"] == 0 for row in paired
        )
        assert {row["comparison_ordinal"] for row in paired} == set(range(expected))
        assert sum(row["revenue"] for row in rows) == expected * (expected + 1) / 2


def test_native_dst_hour_buckets(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path,
        report_zone="America/New_York",
        parse=TimestampParse(timezone="UTC"),
        values=("2026-03-08 06:30:00", "2026-03-08 07:30:00"),
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start="2026-03-08",
                    end="2026-03-09",
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert [row["order_time"].hour for row in rows] == [1, 3]
        assert [row["revenue"] for row in rows] == [1, 2]


@pytest.mark.parametrize(
    "physical,declared,parse,values",
    [
        ("DATE", "date", DateParse(), ("2026-07-01", "2026-07-02")),
        ("BIGINT", "int64", StrptimeParse("%Y%m%d"), ("20260701", "20260702")),
        ("VARCHAR", "string", StrptimeParse("%d/%m/%Y"), ("01/07/2026", "02/07/2026")),
        (
            "TIMESTAMPTZ",
            "timestamp('UTC', 6)",
            None,
            ("2026-07-01 15:59:00+00", "2026-07-01 16:01:00+00"),
        ),
    ],
)
def test_civil_dates_and_absolute_instants(
    tmp_path: Path,
    physical: str,
    declared: str,
    parse: DateParse | StrptimeParse | None,
    values: tuple[str, ...],
) -> None:
    with temporal_fixture(
        tmp_path, physical=physical, declared=declared, parse=parse, values=values
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start="2026-07-01",
                    end="2026-07-03",
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="America/New_York")
        rows = result.expression.to_pyarrow().to_pylist()
        assert [str(row["order_time"])[:10] for row in rows] == ["2026-07-01", "2026-07-02"]
        assert [row["revenue"] for row in rows] == [1, 2]


def test_exact_microsecond_endpoint_and_read_timezone_precedence(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path,
        parse=TimestampParse(timezone="UTC"),
        values=(
            "2026-07-01 16:00:00.000001",
            "2026-07-01 16:00:00.000002",
        ),
    ) as fixture:
        logical = fixture.sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(
                start="2026-07-02T00:00:00.000001",
                end="2026-07-02T00:00:00.000002",
            ),
        ).aggregate()
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="Asia/Shanghai")
        assert result.expression.to_pyarrow().to_pylist()[0]["revenue"] == 1
        assert result.temporal_execution is not None
        assert result.temporal_execution.axes[0].source == "declared"


def test_missing_read_authority_is_not_silently_utc(tmp_path: Path) -> None:
    with temporal_fixture(tmp_path) as fixture:
        logical = fixture.sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(
                start="2026-07-01",
                end="2026-07-03",
            ),
        ).aggregate()
        with pytest.raises(DatasetCompilationError, match="read timezone"):
            compile_dataset(logical, fixture.tables(logical))
        result = compile_dataset(
            logical,
            fixture.tables(logical),
            read_timezone="Asia/Shanghai",
            read_timezone_source="system_fallback",
        )
        assert result.temporal_execution is not None
        assert result.temporal_execution.axes[0].source == "system_fallback"


def test_report_timezone_changes_definition_without_source_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with temporal_fixture(tmp_path) as fixture:

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("construction attempted I/O")

        monkeypatch.setattr(fixture.backend, "execute", forbidden)
        monkeypatch.setattr(fixture.backend, "raw_sql", forbidden)
        outputs = []
        for zone in ("UTC", "Asia/Shanghai"):
            sources = make_lazy_sources(
                semantic_registry=fixture.registry,
                sidecar=fixture.sidecar,
                action_port=NoIoActionPort(),
                session_id="one",
                store_id="one",
                report_time=ReportTimeAuthority(timezone=zone),
            )
            outputs.append(sources.observe(ref.metric("sales.revenue")).definition_fingerprint)
        assert outputs[0] != outputs[1]


@pytest.mark.parametrize(
    "anchor,expected",
    [("all_history", 6), (("trailing", 1, "day"), 5), (("grain_to_date", "month"), 6)],
)
def test_cumulative_dst_endpoint_uses_fixed_trailing_duration(
    tmp_path: Path,
    anchor: str | tuple[str, int, str] | tuple[str, str],
    expected: int,
) -> None:
    with temporal_fixture(
        tmp_path,
        parse=TimestampParse(timezone="UTC"),
        report_zone="America/New_York",
        values=(
            "2026-03-07 03:59:59",
            "2026-03-08 04:30:00",
            "2026-03-09 03:30:00",
        ),
    ) as fixture:
        registry = replace(fixture.registry, metrics=dict(fixture.registry.metrics))
        from marivo.semantic.ir import CumulativeAnchor

        anchors: tuple[CumulativeAnchor, ...] = (
            "all_history",
            ("trailing", 1, "day"),
            ("grain_to_date", "month"),
        )
        selected = next(item for item in anchors if item == anchor)
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition("sales.revenue", AXIS, selected),
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="one",
            store_id="one",
            report_time=fixture.sources._owner.report_time,
        )
        logical = (
            sources.observe(
                ref.metric("sales.running"),
                time_scope=time_scope(
                    start="2026-03-08",
                    end="2026-03-09",
                ),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert result.expression.to_pyarrow().to_pylist()[0]["running"] == expected


def test_composite_date_hour_axis_is_localized_before_day_bucket(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path,
        physical="DATE",
        declared="date",
        parse=DateParse(),
        granularity="day",
        values=("2026-07-01", "2026-07-01"),
    ) as fixture:
        fixture.backend.raw_sql("UPDATE orders SET order_id = CASE WHEN id = 1 THEN 15 ELSE 16 END")
        registry = replace(fixture.registry, dimensions=dict(fixture.registry.dimensions))
        registry.dimensions[AXIS] = replace(registry.dimensions[AXIS], is_default=False)
        hour = "sales.orders.hour"
        registry.dimensions[hour] = replace(
            registry.dimensions[AXIS],
            semantic_id=hour,
            name="hour",
            is_default=True,
            granularity="hour",
            parse=HourPrefixParse(AXIS),
            source_column="order_id",
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="one",
            store_id="one",
            report_time=fixture.sources._owner.report_time,
        )
        logical = (
            sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(
                    start="2026-07-01",
                    end="2026-07-03",
                ),
            )
            .with_time_axis(ref.time_dimension(hour), grain=grain("day"))
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert_compiled_validations(result.validations)
        rows = result.expression.to_pyarrow().to_pylist()
        assert [str(row["hour"])[:10] for row in rows] == ["2026-07-01", "2026-07-02"]
        assert [row["revenue"] for row in rows] == [1, 2]


def test_submicrosecond_source_is_never_silently_truncated(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path,
        physical="TIMESTAMP_NS",
        declared="timestamp(9)",
        parse=TimestampParse(timezone="UTC"),
    ) as fixture:
        logical = fixture.sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(
                start="2026-07-01",
                end="2026-07-03",
            ),
        ).aggregate()
        with pytest.raises(DatasetCompilationError, match="precision"):
            compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")


def test_validity_selection_uses_report_endpoint_and_source_instants(tmp_path: Path) -> None:
    from marivo.datasource.ir import TableSourceIR

    with temporal_fixture(tmp_path) as fixture:
        fixture.backend.raw_sql("DELETE FROM validity")
        fixture.backend.raw_sql("ALTER TABLE validity ALTER start TYPE TIMESTAMP")
        fixture.backend.raw_sql('ALTER TABLE validity ALTER "end" TYPE TIMESTAMP')
        fixture.backend.raw_sql("""INSERT INTO validity (id, start, "end") VALUES
            (1, TIMESTAMP '2026-07-01 15:00:00', TIMESTAMP '2026-07-01 16:00:00'),
            (2, TIMESTAMP '2026-07-01 16:00:00', NULL)""")
        registry = replace(
            fixture.registry,
            entities=dict(fixture.registry.entities),
            dimensions=dict(fixture.registry.dimensions),
        )
        entity = registry.entities["sales.validity"]
        source = entity.source
        assert isinstance(source, TableSourceIR)
        registry.entities[entity.semantic_id] = replace(
            entity,
            source=replace(
                source,
                columns=tuple(
                    (
                        name,
                        replace(binding, data_type="timestamp(6)")
                        if name in ("start", "end")
                        else binding,
                    )
                    for name, binding in source.columns
                ),
            ),
        )
        for name in ("valid_from", "valid_to"):
            path = "sales.validity." + name
            registry.dimensions[path] = replace(
                registry.dimensions[path],
                parse=TimestampParse(timezone="UTC"),
                granularity="second",
            )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="one",
            store_id="one",
            report_time=fixture.sources._owner.report_time,
        )
        logical = sources.population(
            ref.entity("sales.validity"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-02"),
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert_compiled_validations(result.validations)
        rows = result.expression.to_pyarrow().to_pylist()
        assert rows == [{"entity_identity": {"id": 1}}]


def test_fixed_offset_report_authority_is_explicit(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path, report_zone="UTC+08:00", parse=TimestampParse(timezone="UTC")
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert [
            str(row["order_time"])[:10] for row in result.expression.to_pyarrow().to_pylist()
        ] == ["2026-07-01", "2026-07-02"]


@pytest.mark.parametrize(
    "end,expected", [("2026-11-01T05:30:00+00:00", 1), ("2026-11-01T06:00:00+00:00", 3)]
)
def test_fall_back_filters_compare_absolute_instants(
    tmp_path: Path, end: str, expected: int
) -> None:
    with temporal_fixture(
        tmp_path,
        parse=TimestampParse(timezone="UTC"),
        report_zone="America/New_York",
        values=(
            "2026-11-01 05:15:00",
            "2026-11-01 05:45:00",
            "2026-11-01 06:15:00",
        ),
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-11-01T05:00:00+00:00", end=end),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("hour"))
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        rows = result.expression.to_pyarrow().to_pylist()
        assert len(rows) == 1 and rows[0]["order_time"].hour == 1
        assert rows[0]["revenue"] == expected


def test_calendar_boundary_timezone_is_independent_of_source_read_timezone(tmp_path: Path) -> None:
    from datetime import date

    from marivo._temporal import certify_period_calendar, semantic_grain
    from marivo.semantic.ir import PeriodCalendarIR

    with temporal_fixture(tmp_path, parse=TimestampParse(timezone="UTC")) as fixture:
        calendar = ref.period_calendar("sales.fiscal")
        snapshot = certify_period_calendar(
            calendar_ref=calendar,
            boundary_timezone="America/New_York",
            coverage=(date(2026, 7, 1), date(2026, 7, 3)),
            rows=(
                {"date": date(2026, 7, 1), "period": "a"},
                {"date": date(2026, 7, 2), "period": "b"},
            ),
            levels={"reporting_period": "period"},
        )
        registry = replace(
            fixture.registry, period_calendars=dict(fixture.registry.period_calendars)
        )
        metric = registry.metrics["sales.revenue"]
        registry.period_calendars[calendar.path] = PeriodCalendarIR(
            calendar.path,
            "sales",
            "fiscal",
            AXIS,
            "America/New_York",
            ("2026-07-01", "2026-07-03"),
            (("reporting_period", "sales.orders.channel"),),
            metric.ai_context,
            "fiscal",
            metric.location,
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="one",
            store_id="one",
            report_time=fixture.sources._owner.report_time,
            period_calendar_snapshots=(snapshot,),
        )
        logical = (
            sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-07-01T12:00:00", end="2026-07-03T12:00:00"),
            )
            .with_time_axis(
                ref.time_dimension(AXIS),
                grain=semantic_grain(calendar=calendar, level="reporting_period"),
            )
            .aggregate()
        )
        result = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert_compiled_validations(result.validations)
        rows = result.expression.to_pyarrow().to_pylist()
        assert len(rows) == 1 and rows[0]["revenue"] == 3
        assert str(rows[0]["order_time"])[:10] == "2026-07-01"
