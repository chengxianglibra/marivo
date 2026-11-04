"""Canonical parsing and version admission on actual ClickHouse connections."""

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.source_time import parse_strptime
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.semantic.ir import StrptimeParse
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment.clickhouse_analysis import connection
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import rendered_help
from tests.support.source_trace import SourceTrace

FORMATS = (
    "%Y%m%d",
    "%Y-%m-%d",
    "%Y%m%d%H",
    "%Y-%m-%d %H",
    "%Y%m%d%H%M",
    "%Y-%m-%d %H:%M",
    "%Y%m%d%H%M%S",
    "%Y-%m-%d %H:%M:%S",
)


def test_help_discloses_clickhouse_parser_version_boundary() -> None:
    help_text = rendered_help("semantic.strptime")
    assert "22.3" in help_text and "23.3" in help_text
    assert "Date32" in help_text and "DateTime64" in help_text


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
)
@pytest.mark.parametrize(
    "unit,expected",
    [
        ("s", datetime(1960, 5, 18, 12, 34, 56)),
        ("m", datetime(1960, 5, 18, 12, 34)),
        ("h", datetime(1960, 5, 18, 12)),
        ("D", datetime(1960, 5, 18)),
        ("W", datetime(1960, 5, 16)),
        ("M", datetime(1960, 5, 1)),
        ("Q", datetime(1960, 4, 1)),
        ("Y", datetime(1960, 1, 1)),
    ],
)
def test_historical_truncation_never_wraps_through_datetime(unit: str, expected: datetime) -> None:
    point = ibis.literal(
        datetime(1960, 5, 18, 12, 34, 56, 123456, tzinfo=timezone.utc), type="timestamp('UTC', 6)"
    )
    expression = lower_temporal(point.truncate(unit).as_table(), "clickhouse")
    with connection() as client:
        actual = client.query(str(ibis.to_sql(expression, dialect="clickhouse"))).result_rows[0][0]
    assert actual.replace(tzinfo=None) == expected


@pytest.mark.parametrize("fmt", FORMATS)
def test_canonical_clickhouse_compilation_uses_wide_native_carriers(fmt: str) -> None:
    table = ibis.table({"point": "string"}, name="events")
    parsed = parse_strptime("clickhouse", table.point, StrptimeParse(fmt))
    sql = str(ibis.to_sql(parsed, dialect="clickhouse"))
    assert ("toDateTime64OrNull" if "%H" in fmt else "toDate32OrNull") in sql
    assert "parseDateTimeOrNull" not in sql


@pytest.mark.runtime
@pytest.mark.parametrize(
    "fmt,integer",
    [
        (fmt, integer)
        for fmt in FORMATS
        for integer in ((False, True) if "-" not in fmt else (False,))
    ],
)
def test_canonical_projection_matches_python_on_historical_dates_and_nulls(
    fmt: str, integer: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = [
        point.strftime(fmt)
        for point in (
            datetime(1960, 2, 29, 12, 34, 56),
            datetime(2024, 2, 29, 1, 2, 3),
            datetime(2026, 10, 28, 23, 59, 59),
        )
    ]
    points = [int(text) for text in texts] if integer else texts
    rows = [{"id": index, "point": point} for index, point in enumerate([*points, None])]
    data = SourceData("", "", f"id Int64, point Nullable({'Int64' if integer else 'String'})", rows)
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="canonical-parse").relation
        parsed = parse_strptime("clickhouse", table.point.cast("string"), StrptimeParse(fmt))
        result = case.session.collect_bounded(
            lower_temporal(table.select("id", parsed=parsed).order_by("id"), "clickhouse"),
            purpose="parser.canonical",
            max_rows=4,
        )
        expected: list[date | datetime | None] = [
            datetime.strptime(text, fmt) if "%H" in fmt else datetime.strptime(text, fmt).date()
            for text in texts
        ]
        expected.append(None)
        actual = [
            None
            if pd.isna(value)
            else pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)
            if "%H" in fmt
            else pd.Timestamp(value).date()
            for value in result.parsed
        ]
        assert actual == expected


@pytest.mark.runtime
def test_nonordered_parser_is_qualified_before_business_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = SourceData("", "", "id Int64, point String", [{"id": 1, "point": "28/10/2026"}])
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="nonordered-parse").relation
        parsed = parse_strptime("clickhouse", table.point, StrptimeParse("%d/%m/%Y"))
        query = table.select(parsed=parsed)
        before = len(case.session.submissions)
        if tuple(int(part) for part in case.session._backend.version.split(".")[:2]) < (23, 3):
            with pytest.raises(DatasourceSourceCapabilityError) as failure:
                case.session.collect_bounded(query, purpose="parser.nonordered", max_rows=1)
            assert "23.3" in failure.value.expected
            assert case.session._backend.version in failure.value.received
            assert "point" in failure.value.received and "%d/%m/%Y" in failure.value.received
            assert len(case.session.submissions) == before
            result = case.session.collect_bounded(
                table.select("id"), purpose="parser.unused", max_rows=1
            )
            assert result.id.tolist() == [1]
        else:
            result = case.session.collect_bounded(query, purpose="parser.nonordered", max_rows=1)
            assert pd.Timestamp(result.parsed.iloc[0]).date() == date(2026, 10, 28)


@pytest.mark.runtime
@pytest.mark.parametrize("fmt", ["%Y%m%d", "%Y%m%d%H%M%S"])
def test_public_historical_calendar_preserves_wide_source_carriers(
    fmt: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    data = SourceData(
        "",
        "",
        "id Int64, point Nullable(String), amount Int64",
        [
            {"id": 1, "point": datetime(1960, 2, 29, 12).strftime(fmt), "amount": 7},
            {"id": 2, "point": datetime(2026, 10, 28).strftime(fmt), "amount": 11},
            {"id": 3, "point": None, "amount": 19},
        ],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model("clickhouse", case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + f"point=ms.time_dimension_column(name='point',entity=facts,column='point',parse=ms.strptime({fmt!r},timezone='Asia/Shanghai'))\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        monkeypatch.chdir(tmp_path)
        session = mv.session.get_or_create("historical-encoded", timezone="Asia/Shanghai")
        point = session.catalog.get("time_dimension:sales.facts.point")
        amount = session.catalog.get("measure:sales.facts.amount")
        grid = session.time_scope(start="1960-02-29", end="1960-03-01").by(mv.grain("day"))
        rows = (
            session.observe({"value": amount.sum(time=point)}, by=(grid,))
            .compute()
            .to_pandas(time_details=True)
        )
        assert rows["value"].tolist() == [7]
        assert rows["calendar:day.label"].tolist() == ["1960-02-29"], source_trace.native_sql
