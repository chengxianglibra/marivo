"""Calendar buckets stay on civil boundaries when the ClickHouse server zone is not UTC."""

import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
import sqlglot
import sqlglot.expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.runtime

# One fact per five minutes across the Asia/Shanghai civil day 2026-10-28, plus
# one fact just before and one at the exclusive end of that day.
DAY_START = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
POINTS = [DAY_START + timedelta(minutes=5 * index) for index in range(-1, 289)]


@contextmanager
def _shanghai_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    points: Sequence[date | datetime | None] | None = None,
    *,
    physical_type: Literal[
        "Date", "Nullable(Date)", "Nullable(DateTime)", "Nullable(DateTime64(6, 'UTC'))"
    ] = "Nullable(DateTime64(6, 'UTC'))",
    report_timezone: str = "Asia/Shanghai",
) -> Iterator[tuple[mv.Session, ms.TimeDimension, ms.Measure]]:
    port = os.environ.get("MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT")
    if port is None:
        pytest.skip(
            "Explicit Asia/Shanghai ClickHouse server required: MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT"
        )
    monkeypatch.setenv("MARIVO_CLICKHOUSE_TEST_PORT", port)
    data = SourceData(
        "",
        "",
        f"id Int64, point {physical_type}, amount Int64",
        [
            {"id": index, "point": point, "amount": 1}
            for index, point in enumerate(POINTS if points is None else points)
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        header = case.session._backend.con.query("SELECT timezone()").first_row
        assert header == ("Asia/Shanghai",)
        # Date cases describe business dates in the report's civil timezone.
        time_options = (
            ", sample_interval=(5,'minute')"
            if "DateTime" in physical_type
            else f", parse=ms.date(timezone={report_timezone!r})"
        )
        factory(
            {
                "datasources/warehouse.py": _datasource_model("clickhouse", case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + f"point=ms.time_dimension_column(name='point', entity=facts, column='point', is_default=True{time_options})\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("ch-shanghai-calendar", timezone=report_timezone)
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        assert isinstance(point, ms.TimeDimension) and isinstance(amount, ms.Measure)
        yield session, point, amount


@pytest.mark.parametrize("physical_type", ["Nullable(DateTime)", "Nullable(DateTime64(6, 'UTC'))"])
def test_day_grain_single_day_scope_is_one_civil_day_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    physical_type: Literal["Nullable(DateTime)", "Nullable(DateTime64(6, 'UTC'))"],
) -> None:
    with _shanghai_session(
        tmp_path, monkeypatch, semantic_project_factory, physical_type=physical_type
    ) as (
        session,
        point,
        amount,
    ):
        axis = session.time_scope(start="2026-10-28", end="2026-10-29").by(mv.grain("day"))
        frame = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute().to_pandas()
        assert frame["calendar:day"].tolist() == [
            pd.Timestamp("2026-10-28 00:00:00+08:00").tz_convert("Asia/Shanghai")
        ]
        assert frame.total.tolist() == [288]


def test_five_minute_grid_single_day_scope_has_no_rows_before_the_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _shanghai_session(tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        point,
        amount,
    ):
        axis = session.time_scope(start="2026-10-28", end="2026-10-29").by(
            mv.grain("minute", count=5)
        )
        totals = (
            session.observe({"total": amount.sum(time=point)}, by=(axis,))
            .compute()
            .to_pandas()
            .total.tolist()
        )
        assert totals == [1] * 288


def _civil(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}+08:00").astimezone(timezone.utc)


@pytest.mark.parametrize(
    ("unit", "count", "start", "end", "facts", "expected"),
    [
        (
            "week",
            1,
            "2026-10-19",
            "2026-11-02",
            [
                ("2026-10-19", "00:00:00"),
                ("2026-10-25", "17:00:00"),
                ("2026-10-26", "00:00:00"),
                ("2026-10-31", "12:00:00"),
            ],
            [2, 2],
        ),
        (
            "month",
            1,
            "2026-10-01",
            "2026-12-01",
            [("2026-10-01", "00:00:00"), ("2026-10-31", "17:00:00"), ("2026-11-01", "00:00:00")],
            [2, 1],
        ),
        (
            "hour",
            3,
            "2026-10-28",
            "2026-10-29",
            [
                ("2026-10-28", "00:10:00"),
                ("2026-10-28", "02:59:00"),
                ("2026-10-28", "03:00:00"),
                ("2026-10-28", "13:45:00"),
                ("2026-10-28", "23:00:00"),
            ],
            [2, 1, 0, 0, 1, 0, 0, 1],
        ),
    ],
    ids=["week-sunday-evening", "month-last-evening", "hour3-width-not-dividing-offset"],
)
def test_calendar_buckets_follow_civil_boundaries_on_shanghai_server(
    unit: Literal["week", "month", "hour"],
    count: int,
    start: str,
    end: str,
    facts: list[tuple[str, str]],
    expected: list[int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    points = [_civil(day, clock) for day, clock in facts]
    with _shanghai_session(tmp_path, monkeypatch, semantic_project_factory, points) as (
        session,
        point,
        amount,
    ):
        axis = session.time_scope(start=start, end=end).by(mv.grain(unit, count=count))
        totals = (
            session.observe({"total": amount.sum(time=point)}, by=(axis,))
            .compute()
            .to_pandas()
            .total.tolist()
        )
        assert totals == expected


@pytest.mark.parametrize(
    "report_timezone",
    [
        pytest.param("Asia/Shanghai", id="shanghai"),
        pytest.param("UTC", id="utc"),
        pytest.param("America/Los_Angeles", id="los-angeles"),
    ],
)
@pytest.mark.parametrize("physical_type", ["Date", pytest.param("Nullable(Date)", id="nullable")])
def test_date_calendar_day_keeps_dates_and_completes_only_missing_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    physical_type: Literal["Date", "Nullable(Date)"],
    report_timezone: str,
    source_trace: SourceTrace,
) -> None:
    points: list[date | datetime | None] = [date(2026, 10, day) for day in (27, 28, 28, 30, 31)]
    if physical_type == "Nullable(Date)":
        points.append(None)
    with _shanghai_session(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        points,
        physical_type=physical_type,
        report_timezone=report_timezone,
    ) as (session, point, amount):
        single = session.time_scope(start="2026-10-28", end="2026-10-29").by(mv.grain("day"))
        first_submission = len(source_trace.native_sql)
        rows = session.observe({"total": amount.sum()}, by=(single,)).compute().to_pandas()
        assert rows["calendar:day"].tolist() == [pd.Timestamp("2026-10-28", tz=report_timezone)]
        assert rows.total.tolist() == [2]
        fact_sql = [sql for sql in source_trace.native_sql[first_submission:] if '"amount"' in sql]
        assert fact_sql
        assert all("formatDateTime" not in sql for sql in fact_sql), fact_sql

        first_submission = len(source_trace.native_sql)
        selected = (
            session.observe(
                {"total": amount.sum(time=point)},
                by=(point,),
                where=(point >= session.instant("2026-10-28"))
                & (point < session.instant("2026-10-31")),
            )
            .compute()
            .to_pandas()
        )
        assert selected.total.tolist() == [2, 1]
        fact_sql = source_trace.native_sql[first_submission:]
        assert fact_sql
        for sql in fact_sql:
            for where in sqlglot.parse_one(sql, read="clickhouse").find_all(sge.Where):
                assert "\"point\" >= TODATE('2026-10-28')" in where.sql(), sql
                # The upper LA cutoff is within 48 hours of the DST change.
                assert ("__field_" in where.sql()) == (report_timezone == "America/Los_Angeles"), (
                    sql
                )

        first_submission = len(source_trace.native_sql)
        labels = (
            session.observe(
                {"total": amount.sum(time=point)},
                by=(point,),
                where=(point >= "2026-10-28") & (point < "2026-10-31"),
            )
            .compute()
            .to_pandas()
        )
        # Bare dates and Session anchors share the report's civil boundaries.
        assert labels.total.tolist() == [2, 1]
        for sql in source_trace.native_sql[first_submission:]:
            for where in sqlglot.parse_one(sql, read="clickhouse").find_all(sge.Where):
                assert ("__field_" in where.sql()) == (report_timezone == "America/Los_Angeles"), (
                    sql
                )

        axis = session.time_scope(start="2026-10-28", end="2026-10-31").by(mv.grain("day"))
        frame = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute()
        rows = frame.to_pandas(time_details=True)
        expected = [pd.Timestamp(f"2026-10-{day}", tz=report_timezone) for day in (28, 29, 30)]
        assert rows["calendar:day"].tolist() == expected
        assert rows["calendar:day.label"].tolist() == ["2026-10-28", "2026-10-29", "2026-10-30"]
        assert rows.total.tolist() == [2, 0, 1]
        assert source_trace.native_sql
        assert all("todate32(" not in sql.lower() for sql in source_trace.native_sql), (
            source_trace.native_sql
        )

        subdays: tuple[tuple[Literal["second", "minute", "hour"], str], ...] = (
            ("second", "2026-10-28T00:00:01"),
            ("minute", "2026-10-28T00:01:00"),
            ("hour", "2026-10-28T01:00:00"),
        )
        for unit, end in subdays:
            subday = session.time_scope(start="2026-10-28", end=end).by(mv.grain(unit))
            with pytest.raises(AnalysisError, match="subday output"):
                session.observe({"total": amount.sum(time=point)}, by=(subday,)).compute()
        partial = session.time_scope(start="2026-10-28T01:00:00", end="2026-10-29").by(
            mv.grain("day")
        )
        with pytest.raises(AnalysisError, match="is not local midnight"):
            session.observe({"total": amount.sum(time=point)}, by=(partial,)).compute()


@pytest.mark.parametrize("report_timezone", ["Asia/Shanghai", "UTC", "America/Los_Angeles"])
def test_date_calendar_larger_grains_keep_civil_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    report_timezone: str,
    source_trace: SourceTrace,
) -> None:
    points = [date(2026, 10, day) for day in (25, 26, 31)] + [
        date(2026, 11, 1),
        date(2026, 12, 31),
        date(2027, 1, 1),
        None,
    ]
    with _shanghai_session(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        points,
        physical_type="Nullable(Date)",
        report_timezone=report_timezone,
    ) as (session, point, amount):
        cases: list[
            tuple[Literal["week", "month", "quarter", "year"], str, str, list[str], list[int]]
        ] = [
            ("week", "2026-10-19", "2026-11-02", ["2026-10-19", "2026-10-26"], [1, 3]),
            ("month", "2026-10-01", "2026-12-01", ["2026-10-01", "2026-11-01"], [3, 1]),
            ("quarter", "2026-10-01", "2027-04-01", ["2026-10-01", "2027-01-01"], [5, 1]),
            ("year", "2026-01-01", "2028-01-01", ["2026-01-01", "2027-01-01"], [5, 1]),
        ]
        for unit, start, end, labels, totals in cases:
            axis = session.time_scope(start=start, end=end).by(mv.grain(unit))
            rows = (
                session.observe({"total": amount.sum(time=point)}, by=(axis,))
                .compute()
                .to_pandas(time_details=True)
            )
            assert rows[f"calendar:{unit}"].tolist() == [
                pd.Timestamp(label, tz=report_timezone) for label in labels
            ]
            assert rows[f"calendar:{unit}.label"].tolist() == labels
            assert rows.total.tolist() == totals
            assert source_trace.native_sql
            assert all("todate32(" not in sql.lower() for sql in source_trace.native_sql), (
                source_trace.native_sql
            )
