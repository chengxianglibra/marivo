"""Native equality buckets agree with frozen boundaries without SQL enumeration."""

import os
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
import sqlglot as sg
import sqlglot.expressions as sge

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, receipt, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

CASES = source_params(
    [
        ("duckdb", "table"),
        ("postgres", "table"),
        ("mysql", "innodb-table"),
        ("mysql", "mariadb"),
        ("trino", "iceberg"),
        ("clickhouse", "mergetree"),
        ("clickhouse", "distributed"),
    ]
)


def _maria(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if profile != "mariadb":
        return
    if os.environ.get("MARIVO_MARIADB_ANALYSIS_TEST") != "1":
        pytest.skip("Explicit ready MariaDB service required")
    from tests.datasource.environment import mysql_analysis as mysql

    monkeypatch.setenv("MARIVO_MYSQL_ANALYSIS_TEST", "1")
    monkeypatch.setattr(mysql, "connection", partial(mysql.connection, port=23307))
    monkeypatch.setattr(mysql, "setup", partial(mysql.setup, port=23307))
    monkeypatch.setattr(mysql, "PORT", 23307)


def _data(backend: str, points: Sequence[datetime | None]) -> SourceData:
    physical = "DATETIME(6)" if backend == "mysql" else "TIMESTAMP(6)"
    values = ",".join(
        f"({index},"
        + (
            "NULL"
            if point is None
            else ("TIMESTAMP " if backend == "trino" else "")
            + "'"
            + point.replace(tzinfo=None).isoformat(sep=" ")
            + "'"
        )
        + ",1)"
        for index, point in enumerate(points)
    )
    return SourceData(
        f"id BIGINT, point {physical}, amount BIGINT",
        values,
        "id Int64, point Nullable(DateTime64(6, 'UTC')), amount Int64",
        [{"id": index, "point": point, "amount": 1} for index, point in enumerate(points)],
    )


@pytest.mark.parametrize("backend,profile", CASES)
def test_public_encoded_clocks_retain_wall_bucket_membership(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    clocks = [
        "2026-03-08 01:59:59",
        "2026-03-08 02:30:00",
        "2026-03-08 03:00:00",
        "2026-11-01 01:30:00",
    ]
    numbers = [int(datetime.fromisoformat(clock).strftime("%Y%m%d%H%M%S")) for clock in clocks]
    data = SourceData(
        "id BIGINT, wall VARCHAR(30), encoded BIGINT, amount BIGINT",
        ",".join(
            f"({i},'{clock}',{number},1)"
            for i, (clock, number) in enumerate(zip(clocks, numbers, strict=True))
        ),
        "id Int64, wall String, encoded Int64, amount Int64",
        [
            {"id": i, "wall": clock, "encoded": number, "amount": 1}
            for i, (clock, number) in enumerate(zip(clocks, numbers, strict=True))
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "wall=ms.time_dimension_column(name='wall', entity=facts, column='wall', parse=ms.strptime('%Y-%m-%d %H:%M:%S', timezone='America/New_York'))\n"
                + "encoded=ms.time_dimension_column(name='encoded', entity=facts, column='encoded', parse=ms.strptime('%Y%m%d%H%M%S', timezone='America/New_York'))\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("wall-calendar", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        namespace = case.source.database
        parts = (
            namespace
            if isinstance(namespace, tuple)
            else (namespace,)
            if namespace is not None
            else ()
        )
        table_sql = sge.table_(
            case.source.table,
            db=parts[-1] if parts else None,
            catalog=parts[0] if len(parts) == 2 else None,
        ).sql(dialect=backend)
        native_parse = {
            "duckdb": "timezone('America/New_York', CAST(wall AS TIMESTAMP))",
            "postgres": "CAST(wall AS TIMESTAMP) AT TIME ZONE 'America/New_York'",
            "mysql": "CONVERT_TZ(CAST(wall AS DATETIME), 'America/New_York', '+00:00')",
            "trino": "with_timezone(CAST(wall AS TIMESTAMP), 'America/New_York')",
            "clickhouse": "toTimeZone(toDateTime64(wall, 6, 'America/New_York'), 'UTC')",
        }[backend]
        native_rows = case.session._backend.execute(
            case.session._backend.sql(
                f"SELECT {native_parse} AS point FROM {table_sql}",
                schema={"point": "timestamp('UTC', 6)"},
            )
        )
        instants = [
            pd.Timestamp(value).tz_localize("UTC")
            if pd.Timestamp(value).tzinfo is None
            else pd.Timestamp(value).tz_convert("UTC")
            for value in native_rows.point
        ]
        for clock in ("wall", "encoded"):
            point = catalog_value(session.catalog, "sales.facts." + clock, kind="time_dimension")
            for start, end in (
                ("2026-03-08T06:55Z", "2026-03-08T07:10Z"),
                ("2026-11-01T05:25Z", "2026-11-01T06:40Z"),
            ):
                expected = [
                    sum(left <= instant < left + pd.Timedelta(minutes=5) for instant in instants)
                    for left in pd.date_range(start, end, freq="5min", inclusive="left")
                ]
                axis_scope = session.time_scope(start=start, end=end)
                pending = session.observe(
                    {"total": amount.sum(time=point)},
                    by=(axis_scope.by(mv.grain("minute", count=5)),),
                )
                actual = pending.compute().to_pandas()
                assert actual.total.tolist() == [count for count in expected if count]


@pytest.mark.parametrize("backend,profile", CASES)
@pytest.mark.parametrize(
    "zone,day,clocks,ranges",
    [
        (
            "America/St_Johns",
            "2009-11-01",
            (
                "02:29:59.999999",
                "02:30:00",
                "02:30:59.999999",
                "02:31:00",
                "02:45:00",
                "03:29:59.999999",
                "03:30:00",
                "03:30:00.000001",
            ),
            (("02:30", "03:30", 5), ("02:40", "03:00", 1)),
        ),
        (
            "Antarctica/Casey",
            "2010-03-04",
            (
                "12:59:59.999999",
                "13:00:00",
                "14:59:59.999999",
                "15:00:00",
                "15:30:00",
                "15:59:59.999999",
                "16:00:00",
                "16:00:00.000001",
            ),
            (("13:00", "16:00", 5), ("15:00", "15:45", 2)),
        ),
    ],
)
def test_public_calendar_keeps_facts_when_midnight_fold_returns_to_previous_date(
    zone: str,
    day: str,
    clocks: tuple[str, ...],
    ranges: tuple[tuple[str, str, int], ...],
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    # Both transitions revisit a civil date. Native bucket membership need not
    # form one contiguous UTC interval; compare each executor to its own SQL.
    points = [
        datetime.fromisoformat(day + "T" + clock).replace(tzinfo=timezone.utc) for clock in clocks
    ]
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, _data(backend, [*points, None])
    ) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point', entity=facts, column='point')\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("midnight-fold", timezone=zone)
        point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        trace = capture_source(monkeypatch)
        for unit in ("day", "month"):
            for start, end, expected in ranges:
                axis_scope = session.time_scope(
                    start=day + "T" + start + "Z",
                    end=day + "T" + end + "Z",
                )
                before = len(trace.native_sql)
                result = session.observe(
                    {"total": amount.sum(time=point)}, by=(axis_scope.by(mv.grain(unit)),)
                ).compute()
                rows = result.to_pandas(time_details=True)
                namespace = case.source.database
                parts = (
                    namespace
                    if isinstance(namespace, tuple)
                    else (namespace,)
                    if namespace is not None
                    else ()
                )
                table_sql = sge.table_(
                    case.source.table,
                    db=parts[-1] if parts else None,
                    catalog=parts[0] if len(parts) == 2 else None,
                ).sql(dialect=backend)
                fmt = "%Y-%m-01 00:00:00" if unit == "month" else "%Y-%m-%d 00:00:00"
                native_key = {
                    "duckdb": f"timezone('{zone}', date_trunc('{unit}', timezone('{zone}', timezone('UTC', point))))",
                    "postgres": f"date_trunc('{unit}', (point AT TIME ZONE 'UTC') AT TIME ZONE '{zone}') AT TIME ZONE '{zone}'",
                    "mysql": f"CONVERT_TZ(DATE_FORMAT(CONVERT_TZ(point, '+00:00', '{zone}'), '{fmt}'), '{zone}', '+00:00')",
                    "trino": f"with_timezone(date_trunc('{unit}', CAST(at_timezone(with_timezone(point, 'UTC'), '{zone}') AS TIMESTAMP(6))), '{zone}')",
                    "clickhouse": f"toTimeZone(toDateTime64(toString(toDateTime64(dateTrunc('{unit}', toDateTime64(toString(point, '{zone}'), 6, 'UTC')), 6, 'UTC'), 'UTC'), 6, '{zone}'), 'UTC')",
                }[backend]
                lower, upper = (
                    pd.Timestamp(day + " " + clock).isoformat(sep=" ") for clock in (start, end)
                )

                def bound(value: str) -> str:
                    return (
                        f"toDateTime64('{value}', 6, 'UTC')"
                        if backend == "clickhouse"
                        else f"TIMESTAMP '{value}'"
                    )

                # Read Trino's chosen instant numerically: a client decoding a
                # named-zone midnight may select the other side of the fold.
                bucket = (
                    f"CAST(to_unixtime({native_key}) AS BIGINT)"
                    if backend == "trino"
                    else native_key
                )
                sql = f"SELECT {bucket} AS bucket, count(*) AS total FROM {table_sql} WHERE point >= {bound(lower)} AND point < {bound(upper)} GROUP BY {native_key} ORDER BY bucket"
                oracle = case.session._backend.execute(
                    case.session._backend.sql(
                        sql,
                        schema={
                            "bucket": "int64" if backend == "trino" else "timestamp('UTC', 6)",
                            "total": "int64",
                        },
                    )
                )

                def key(value: object) -> pd.Timestamp:
                    assert isinstance(value, (str, datetime, date, int, float))
                    stamp = pd.Timestamp(value)
                    return (
                        stamp.tz_localize("UTC")
                        if stamp.tzinfo is None
                        else stamp.tz_convert("UTC")
                    )

                actual = {
                    key(bucket): total
                    for bucket, total in zip(rows[f"calendar:{unit}"], rows.total, strict=True)
                    if total != 0
                }
                assert actual == {
                    (
                        pd.to_datetime(bucket, unit="s", utc=True)
                        if backend == "trino"
                        else key(bucket)
                    ): total
                    for bucket, total in zip(oracle.bucket, oracle.total, strict=True)
                }
                assert sum(actual.values()) == expected
                assert len(trace.native_sql) >= before + 2
                if backend == "mysql":
                    # Only constant digit tables may use UNION; calendar keys
                    # and their parameters must not be enumerated as SQL rows.
                    for union in sg.parse_one(trace.native_sql[-1], read="mysql").find_all(
                        sge.Union
                    ):
                        for branch in (union.left, union.right):
                            if isinstance(branch, sge.Union):
                                continue
                            assert isinstance(branch, sge.Select)
                            assert branch.args.get("from_") is None
                            assert len(branch.expressions) == 1
                            digit = branch.expressions[0].unalias()
                            assert isinstance(digit, sge.Literal) and digit.is_int
                            assert 0 <= int(digit.this) <= 9
                if backend == "clickhouse":
                    assert len(trace.native_sql[-1].encode()) < 65536
        receipt(
            "calendar-midnight-fold-" + zone.replace("/", "-") + "-" + backend + "-" + profile,
            {"actual_sql": trace.native_sql},
        )


@pytest.mark.parametrize(
    ("start", "end", "zone", "unit", "clocks", "error_name"),
    [
        (
            "2010-03-03T13:00:00Z",
            "2010-03-04T16:00:00Z",
            "Antarctica/Casey",
            "day",
            ["2010-03-04", "2010-03-05"],
            "AmbiguousTimeError",
        ),
        (
            "2026-03-08T05:00Z",
            "2026-03-08T09:00Z",
            "America/New_York",
            "hour",
            ["2026-03-08 00:00", "2026-03-08 01:00", "2026-03-08 02:00"],
            "NonExistentTimeError",
        ),
        (
            "2026-11-01T05:00Z",
            "2026-11-01T06:00Z",
            "America/New_York",
            "hour",
            ["2026-11-01 01:00"],
            "AmbiguousTimeError",
        ),
    ],
)
def test_source_free_calendar_preserves_pandas_native_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: str,
    end: str,
    zone: str,
    unit: Literal["hour", "day"],
    clocks: list[str],
    error_name: str,
) -> None:
    from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError

    from marivo.analysis.dsl.population import _calendar_population
    from marivo.analysis.materialization.errors import MaterializationError

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("pandas-native-calendar", timezone=zone)
    axis = session.time_scope(start=start, end=end).by(mv.grain(unit))
    error_type = AmbiguousTimeError if error_name == "AmbiguousTimeError" else NonExistentTimeError
    with pytest.raises(error_type):
        pd.DatetimeIndex(clocks).tz_localize(zone)
    with pytest.raises(MaterializationError) as captured:
        session.frame(_calendar_population(axis)).compute()
    assert isinstance(captured.value.__cause__, error_type)


@pytest.mark.parametrize("backend,profile", CASES)
def test_public_hour_index_follows_native_gap_and_fold_interpretation(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    monkeypatch.chdir(tmp_path)
    # The fact is outside every selection, but belongs to each all-history window.
    with source_case(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        _data(backend, [datetime(2026, 1, 1, tzinfo=timezone.utc)]),
    ) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point', entity=facts, column='point')\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("native-hour-index", timezone="America/New_York")
        point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        for day, start, end, clocks in (
            ("2026-03-08", "05:00", "09:00", ("00:00", "01:00", "02:00", "03:00", "04:00")),
            ("2026-11-01", "05:00", "06:00", ("01:00",)),
            ("2026-11-01", "05:15", "05:45", ("01:00",)),
            ("2026-11-01", "06:00", "07:00", ("01:00",)),
        ):
            # Independent SQL delegates each civil candidate to the actual engine.
            native = []
            for clock in clocks:
                wall = f"{day} {clock}:00"
                key = {
                    "duckdb": f"timezone('America/New_York', TIMESTAMP '{wall}')",
                    "postgres": f"TIMESTAMP '{wall}' AT TIME ZONE 'America/New_York'",
                    "mysql": f"CONVERT_TZ(CAST('{wall}' AS DATETIME(6)), 'America/New_York', '+00:00')",
                    "trino": f"with_timezone(TIMESTAMP '{wall}', 'America/New_York')",
                    "clickhouse": f"toTimeZone(toDateTime64('{wall}', 6, 'America/New_York'), 'UTC')",
                }[backend]
                native.append(f"SELECT {key} AS bucket")
            oracle = case.session._backend.execute(
                case.session._backend.sql(
                    " UNION ALL ".join(native), schema={"bucket": "timestamp('UTC', 6)"}
                )
            )
            expected = sorted(
                {
                    pd.Timestamp(value).tz_localize("UTC")
                    if pd.Timestamp(value).tzinfo is None
                    else pd.Timestamp(value).tz_convert("UTC")
                    for value in oracle.bucket
                }
            )
            axis_scope = session.time_scope(start=f"{day}T{start}Z", end=f"{day}T{end}Z")
            total = amount.sum(time=point)
            rows = (
                session.observe(
                    {"total": total, "history": total.window(mv.all_history())},
                    by=(axis_scope.by(mv.grain("hour")),),
                )
                .compute()
                .to_pandas()
            )
            assert rows["calendar:hour"].dt.tz_convert("UTC").tolist() == expected
            assert rows.total.tolist() == [0] * len(expected)
            assert rows.history.tolist() == [1] * len(expected)
