"""Calendar buckets stay on civil boundaries when the ClickHouse server zone is not UTC."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value

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
    points: list[datetime] | None = None,
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
        "id Int64, point Nullable(DateTime64(6, 'UTC')), amount Int64",
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
        factory(
            {
                "datasources/warehouse.py": _datasource_model("clickhouse", case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point', entity=facts, column='point', sample_interval=(5,'minute'))\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("ch-shanghai-calendar", timezone="Asia/Shanghai")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        assert isinstance(point, ms.TimeDimension) and isinstance(amount, ms.Measure)
        yield session, point, amount


def test_day_grain_single_day_scope_is_one_civil_day_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with _shanghai_session(tmp_path, monkeypatch, semantic_project_factory) as (
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
