"""Native parser data failures and membership, with independent value witnesses."""

from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.source_time import parse_strptime, source_time, source_time_predicate
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource.errors import DatasourceError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.ir import StrptimeParse
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.datasource.source_cases import SourceData, source_case, source_params

CASES = source_params(
    [
        ("duckdb", "table"),
        ("postgres", "table"),
        ("mysql", "innodb-table"),
        ("mysql", "mariadb"),
        ("trino", "iceberg"),
        ("clickhouse", "mergetree"),
    ]
)


@pytest.mark.runtime
def test_postgres_parser_preserves_native_relaxed_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PostgreSQL TO_TIMESTAMP accepts alternate separators, suffix text and
    # missing trailing fields.
    rows = [
        {"id": 1, "point": "2026/02/28", "amount": 7},
        {"id": 2, "point": "2026-02-28trailing", "amount": 11},
        {"id": 3, "point": "2026-02", "amount": 13},
        {"id": 4, "point": "2026!02!28", "amount": 17},
        {"id": 5, "point": "2026-2-8", "amount": 19},
        {"id": 7, "point": None, "amount": 29},
    ]
    values = ",".join(
        f"({row['id']},"
        + ("NULL" if row["point"] is None else repr(row["point"]))
        + f",{row['amount']})"
        for row in rows
    )
    data = SourceData("id BIGINT, point VARCHAR(100), amount BIGINT", values, "", rows)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="relaxed-dates").relation
        parsed = parse_strptime("postgres", table.point, StrptimeParse("%Y-%m-%d"))
        query = table.select("id", parsed=parsed).order_by("id")
        result = case.session.collect_bounded(
            lower_temporal(query, "postgres"), purpose="parser.relaxed", max_rows=8
        )
        expected = [
            date(2026, 2, 28),
            date(2026, 2, 28),
            date(2026, 2, 1),
            date(2026, 2, 28),
            date(2026, 2, 8),
        ]
        assert [
            pd.Timestamp(value).date() if pd.notna(value) else None for value in result.parsed
        ] == [*expected, None]

        field = axis("%Y-%m-%d")
        _, authority = source_time(
            table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="postgres"
        )
        predicate = source_time_predicate(
            table.point,
            field,
            authority,
            start=date(2026, 2, 1),
            end=date(2026, 3, 1),
            engine="postgres",
        )
        assert predicate is not None
        total = case.session.collect_bounded(
            lower_temporal(table.filter(predicate).aggregate(total=table.amount.sum()), "postgres"),
            purpose="parser.relaxed.membership",
            max_rows=2,
        )
        assert total.total.tolist() == [67]


@pytest.mark.runtime
def test_postgres_parser_preserves_native_relaxed_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"id": 1, "point": "2026/02/28 12:34:56extra"},
        {"id": 2, "point": "2026-02-28 12:34"},
        {"id": 3, "point": None},
    ]
    values = ",".join(
        f"({row['id']}," + ("NULL" if row["point"] is None else repr(row["point"])) + ")"
        for row in rows
    )
    data = SourceData("id BIGINT, point VARCHAR(100)", values, "", rows)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="relaxed-timestamps").relation
        parsed = parse_strptime("postgres", table.point, StrptimeParse("%Y-%m-%d %H:%M:%S"))
        result = case.session.collect_bounded(
            lower_temporal(table.select("id", parsed=parsed).order_by("id"), "postgres"),
            purpose="parser.relaxed.timestamps",
            max_rows=4,
        )
        assert [
            pd.Timestamp(value).replace(tzinfo=None).to_pydatetime() if pd.notna(value) else None
            for value in result.parsed
        ] == [datetime(2026, 2, 28, 12, 34, 56), datetime(2026, 2, 28, 12, 34), None]


@pytest.mark.runtime
@pytest.mark.parametrize("point", ["2026-02-30", "broken"])
@pytest.mark.parametrize("consumer", ["projection", "membership"])
def test_postgres_parser_propagates_invalid_data(
    point: str, consumer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from psycopg.errors import DatetimeFieldOverflow, InvalidDatetimeFormat

    data = SourceData(
        "id BIGINT, point VARCHAR(100)",
        f"(1,{point!r})",
        "",
        [{"id": 1, "point": point}],
    )
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="invalid-date").relation
        if consumer == "projection":
            parsed = parse_strptime("postgres", table.point, StrptimeParse("%Y-%m-%d"))
            query = table.select(parsed=parsed)
        else:
            field = axis("%Y-%m-%d")
            _, authority = source_time(
                table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="postgres"
            )
            predicate = source_time_predicate(
                table.point,
                field,
                authority,
                start=date(2026, 2, 1),
                end=date(2026, 3, 1),
                engine="postgres",
            )
            assert predicate is not None
            query = table.filter(predicate).aggregate(total=table.id.count())
        with pytest.raises((DatasourceError, DatetimeFieldOverflow, InvalidDatetimeFormat)):
            case.session.collect_bounded(
                lower_temporal(query, "postgres"),
                purpose="parser.invalid",
                max_rows=2,
            )


@pytest.mark.parametrize("backend,profile", CASES)
@pytest.mark.parametrize(
    "fmt,good,bad,expected",
    [
        ("%Y%m%d", "20260228", "broken", date(2026, 2, 28)),
        (
            "%Y-%m-%d %H:%M:%S",
            "2026-02-28 12:34:56",
            "2026-13-28 12:34:56",
            datetime(2026, 2, 28, 12, 34, 56),
        ),
        ("%b %d %Y", "Feb 28 2026", "invalid", date(2026, 2, 28)),
        ("%B %d %Y", "February 28 2026", "invalid", date(2026, 2, 28)),
        ("%Y %j", "2026 059", "invalid", date(2026, 2, 28)),
        ("%y-%m-%d", "26-02-28", "invalid", date(2026, 2, 28)),
        ("%a %Y-%m-%d", "Sat 2026-02-28", "invalid", date(2026, 2, 28)),
        ("%A %Y-%m-%d", "Saturday 2026-02-28", "invalid", date(2026, 2, 28)),
        (
            "%Y-%m-%d %H:%M:%S.%f",
            "2026-02-28 12:34:56.123456",
            "invalid",
            datetime(2026, 2, 28, 12, 34, 56, 123456),
        ),
        (
            "%Y-%m-%d %I:%M:%S %p",
            "2026-02-28 12:34:56 PM",
            "invalid",
            datetime(2026, 2, 28, 12, 34, 56),
        ),
    ],
)
def test_native_parser_rejects_bad_data_without_losing_legal_values(
    backend: str,
    profile: str,
    fmt: str,
    good: str,
    bad: str,
    expected: date | datetime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if backend == "clickhouse" and any(token in fmt for token in ("%a", "%A", "%f")):
        from marivo.analysis.compiler.errors import DatasetCompilationError

        with pytest.raises(DatasetCompilationError):
            parse_strptime(backend, ibis.literal(good), StrptimeParse(fmt))
        return
    _maria(profile, monkeypatch)
    points = (good, None) if backend == "postgres" else (good, bad, "", None)
    rows = [{"id": index, "point": point} for index, point in enumerate(points)]
    values = ",".join(
        f"({row['id']}," + ("NULL" if row["point"] is None else repr(row["point"])) + ")"
        for row in rows
    )
    data = SourceData(
        "id BIGINT, point VARCHAR(100)", values, "id Int64, point Nullable(String)", rows
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="parser-values").relation
        expression = table.select(
            "id", parsed=parse_strptime(backend, table.point.nullif(""), StrptimeParse(fmt))
        ).order_by("id")
        result = case.session.collect_bounded(
            lower_temporal(expression, backend), purpose="parser.values", max_rows=5
        )
        actual = result.parsed.iloc[0]
        if isinstance(expected, datetime):
            if backend == "trino":
                expected = expected.replace(microsecond=expected.microsecond // 1000 * 1000)
            assert pd.Timestamp(actual).replace(tzinfo=None) == expected
        else:
            assert pd.Timestamp(actual).date() == expected
        assert result.parsed.iloc[1:].isna().all()


@pytest.mark.parametrize("backend,profile", CASES)
@pytest.mark.parametrize("fmt", ("%Y%m%d", "%Y-%m-%d"))
def test_safe_membership_excludes_invalid_calendar_and_keeps_native_noncanonical_values(
    backend: str,
    profile: str,
    fmt: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    points = (
        ("20260228", "20260230", "broken")
        if fmt == "%Y%m%d"
        else ("2026-02-28", "2026-02-30", "2026-2-8")
    )
    rows = [
        {"id": 1, "point": points[0], "amount": 7},
        {"id": 2, "point": points[1], "amount": 11},
        {"id": 3, "point": points[2], "amount": 13},
    ]
    if backend == "postgres":
        rows = [row for row in rows if row["id"] == 1 or (fmt != "%Y%m%d" and row["id"] == 3)]
    values = ",".join(f"({row['id']},{row['point']!r},{row['amount']})" for row in rows)
    data = SourceData(
        "id BIGINT, point VARCHAR(100), amount BIGINT",
        values,
        "id Int64, point Nullable(String), amount Int64",
        rows,
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="parser-membership").relation
        field = axis(fmt)
        _, authority = source_time(
            table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine=backend
        )
        predicate = source_time_predicate(
            table.point,
            field,
            authority,
            start=date(2026, 2, 1),
            end=date(2026, 3, 1),
            engine=backend,
        )
        assert predicate is not None
        query = table.filter(predicate).aggregate(total=table.amount.sum())
        result = case.session.collect_bounded(
            lower_temporal(query, backend), purpose="parser.membership", max_rows=2
        )
        assert result.total.tolist() == [7 if fmt == "%Y%m%d" or backend == "clickhouse" else 20]


def test_parsed_gap_membership_keeps_wall_order() -> None:
    table = ibis.memtable(
        {"point": ["2026-03-08 02:15", "2026-03-08 03:00", "bad"], "amount": [7, 11, 19]}
    )
    field = axis("%Y-%m-%d %H:%M", "America/New_York")
    _, authority = source_time(
        table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
    )
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
        end=datetime(2026, 3, 8, 7, 10, tzinfo=timezone.utc),
    )
    assert predicate is not None
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(
            table.filter(predicate).aggregate(total=table.amount.sum())
        ).total.tolist() == [18]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("backend,profile", CASES)
def test_public_parsed_time_predicate_and_calendar_keep_wall_order(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    bad = None if backend == "postgres" else "bad"
    data = SourceData(
        "id BIGINT, point VARCHAR(30), amount BIGINT",
        "(1,'2026-03-08 02:15',7),(2,'2026-03-08 03:00',11),"
        + ("(3,NULL,19)" if bad is None else "(3,'bad',19)"),
        "id Int64, point Nullable(String), amount Int64",
        [
            {"id": 1, "point": "2026-03-08 02:15", "amount": 7},
            {"id": 2, "point": "2026-03-08 03:00", "amount": 11},
            {"id": 3, "point": bad, "amount": 19},
        ],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point',entity=facts,column='point',parse=ms.strptime('%Y-%m-%d %H:%M',timezone='America/New_York'))\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        monkeypatch.chdir(tmp_path)
        session = mv.session.get_or_create("parsed-wall-order", timezone="UTC")
        point = session.catalog.get("time_dimension:sales.facts.point")
        amount = session.catalog.get("measure:sales.facts.amount")
        scope = session.time_scope(
            start="2026-03-08T07:00:00+00:00", end="2026-03-08T07:10:00+00:00"
        )
        selected = session.observe(
            {"value": amount.sum()}, by=(), where=point.within(scope)
        ).compute()
        assert selected.to_pandas()["value"].tolist() == [18]
        daily = session.observe(
            {"value": amount.sum(time=point)}, by=(scope.by(mv.grain("minute")),)
        ).compute()
        assert daily.to_pandas()["value"].tolist() == [18] + [0] * 9
        grouped = session.observe({"value": amount.sum()}, by=(point,)).compute()
        rows = grouped.to_pandas(axis_names={grouped.axes[0].token: "parsed"})
        assert rows.loc[rows.parsed.isna(), "value"].tolist() == [19]


@pytest.mark.parametrize("backend,profile", CASES)
def test_native_input_expression_error_boundary(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    data = SourceData(
        "id BIGINT, point VARCHAR(100)",
        "(1,'bad')",
        "id Int64, point Nullable(String)",
        [{"id": 1, "point": "bad"}],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="parser-input-boundary").relation
        text = table.point.cast("int64").cast("string")
        expression = table.select(parsed=parse_strptime(backend, text, StrptimeParse("%Y%m%d")))
        if backend in ("trino", "mysql", "clickhouse"):
            result = case.session.collect_bounded(expression, purpose="parser.input", max_rows=2)
            assert result.parsed.isna().all()
        else:
            if backend == "duckdb":
                from duckdb import ConversionException

                native_error = ConversionException
            elif backend == "postgres":
                from psycopg.errors import InvalidTextRepresentation

                native_error = InvalidTextRepresentation
            else:
                from clickhouse_connect.driver.exceptions import DatabaseError

                native_error = DatabaseError
            with pytest.raises((DatasourceError, native_error)):
                case.session.collect_bounded(expression, purpose="parser.input", max_rows=2)
