"""Native equality buckets agree with frozen boundaries without SQL enumeration."""

import os
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
import sqlglot as sg
import sqlglot.expressions as sge

import marivo.analysis as mv
from marivo._temporal import certify_period_calendar_rows, semantic_grain
from marivo.analysis.compiler.calendar import bind_calendar
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource._calendar_relations import CalendarData
from marivo.datasource._temporal_expressions import localize, timestamp
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, receipt, source_case, source_params
from tests.shared_fixtures import catalog_value, frozen_coordinates
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


def _custom() -> mv.CalendarAxis:
    start = date(2025, 1, 1)
    stop = date(2027, 1, 1)
    calendar = ref.period_calendar("test.fiscal")
    snapshot = certify_period_calendar_rows(
        calendar_ref=calendar,
        boundary_timezone="Asia/Shanghai",
        coverage=(start, stop),
        columns=("day", "period"),
        date_column="day",
        levels={"fiscal": "period"},
        retained_values=tuple(
            {
                "day": (start + timedelta(days=i)).isoformat(),
                "period": "first" if i < 17 else "long" if i < 400 else "last",
            }
            for i in range((stop - start).days)
        ),
    )
    return exact_scope("2025-01-18", "2026-02-05", "Asia/Shanghai").by(
        semantic_grain(calendar=calendar, level="fiscal", snapshot=snapshot)
    )


@pytest.mark.parametrize("backend,profile", CASES)
def test_native_calendar_boundary_membership(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    samples: tuple[
        tuple[
            str, str, str, Literal["second", "hour", "day", "week", "month", "quarter", "year"], int
        ],
        ...,
    ] = (
        ("2026-01-01T00:00:00.000001", "2026-01-01T00:06:00.000001", "UTC", "second", 90),
        ("2026-01-01", "2026-01-02", "UTC+08:00", "hour", 6),
        ("2026-01-01", "2026-01-02", "UTC+05:30", "hour", 1),
        ("2026-01-01", "2026-01-03", "Asia/Shanghai", "day", 1),
        ("2025-12-28", "2026-01-06", "Asia/Shanghai", "week", 1),
        ("2025-12-30T06:00:00", "2026-02-02", "UTC", "month", 1),
        ("2025-12-30", "2026-04-02", "UTC", "quarter", 1),
        ("2025-12-30", "2026-01-02", "UTC", "year", 1),
    )
    axes = [
        exact_scope(start, end, zone).by(mv.grain(unit, count=count))
        for start, end, zone, unit, count in samples
    ]
    axes.append(_custom())

    points: list[datetime | None] = [
        *sorted(
            {
                boundary + timedelta(microseconds=delta)
                for axis in axes
                for cell in axis._binding.cells
                for boundary in cell[2:]
                for delta in (-1, 0, 1)
            }
        ),
        None,
    ]
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend, points)) as case:
        table = case.session.bind(case.source, source_identity="calendar-facts").relation
        clock = (
            table.point if table.point.type().timezone else localize("UTC", timestamp(table.point))
        )
        table = table.mutate(point=clock)
        evidence = []
        for axis in axes:
            expression = bind_calendar(table, table.point, axis._binding, "bucket")
            for directory in expression.op().find(CalendarData):
                import json

                rows = json.loads(directory.payload)
                matches = [next(iter(row.values())) for row in rows]
                assert len(matches) == len(set(matches))
            payload = sum(len(node.payload.encode()) for node in expression.op().find(CalendarData))
            expression = lower_temporal(expression.order_by("id"), backend)
            actual = case.session.collect_bounded(
                expression, purpose="calendar.boundary", max_rows=len(points) + 1
            )
            expected = [
                next(
                    (
                        cell[0]
                        for cell in axis._binding.cells
                        if point is not None and cell[2] <= point < cell[3]
                    ),
                    None,
                )
                for point in points
            ]
            assert len(actual) == len(points)  # Equality maps cannot multiply facts.
            normalized = [
                None
                if pd.isna(value)
                else pd.Timestamp(value).tz_convert("UTC")
                if pd.Timestamp(value).tzinfo
                else pd.Timestamp(value).tz_localize("UTC")
                for value in actual.bucket
            ]
            assert normalized == expected, (axis._binding.grain, case.session.submissions[-1].sql)
            sql = case.session.submissions[-1].sql
            assert "UNION ALL" not in sql.upper()
            if backend == "clickhouse":
                assert len(sql.encode()) < 65536
            evidence.append(
                {
                    "grain": axis._binding.grain,
                    "sql_bytes": len(sql.encode()),
                    "payload_bytes": payload,
                    "rows": len(actual),
                }
            )
        receipt("calendar-boundaries-" + backend + "-" + profile, {"cases": evidence})


@pytest.mark.parametrize("backend,profile", CASES)
def test_calendar_capacity_and_sum_before_p95(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    points = [
        start + timedelta(minutes=5 * i, seconds=offset) for i in range(4032) for offset in (0, 0)
    ]
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend, points)) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point', entity=facts, column='point', sample_interval=(5,'minute'))\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("calendar-capacity", timezone="UTC")
        point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        trace = capture_source(monkeypatch, measure_cost=True)
        evidence = []
        for days in (1, 14):
            before = len(trace.native_sql)
            axis = session.time_scope(start=start, end=start + timedelta(days=days)).by(
                mv.grain("minute", count=5)
            )
            result = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute()
            assert result.to_pandas().total.tolist() == [2] * (days * 288)
            sqls = trace.native_sql[before:]
            assert len(sqls) == 2
            assert max(len(sql.encode()) for sql in sqls) < 65536
            evidence.append(
                {
                    "days": days,
                    "sql_bytes": len(sqls[0].encode()),
                    "source_seconds": trace.source_seconds[-1],
                    "payload_bytes": trace.calendar_payload_bytes[-1],
                    "returned_rows": trace.returned_rows[-1],
                }
            )
        custom = _custom()
        before = len(trace.native_sql)
        totals = (
            session.observe({"total": amount.sum(time=point)}, by=(custom,))
            .compute()
            .to_pandas()
            .total.tolist()
        )
        assert totals == [8064]
        assert all(len(sql.encode()) < 65536 for sql in trace.native_sql[before:])
        assert all(
            "JSONExtract" not in sql and "JSON_TABLE" not in sql
            for sql in trace.native_sql[before:]
        )
        with pytest.raises(ValueError, match="one complete custom"):
            session.time_scope(
                start=start, end=start + timedelta(days=14), timezone="Asia/Shanghai"
            ).by(custom._binding.grain_spec)
        # Missing time buckets do not enter the internal reduction.
        before = len(trace.native_sql)
        scope = session.time_scope(start=start, end=start + timedelta(days=15))
        mean = amount.sum(time=point).per(mv.grain("minute", count=5)).mean()
        assert session.observe({"mean": mean}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == pytest.approx([2])
        submitted = trace.native_sql[before:]
        assert len(submitted) == 1
        if backend == "clickhouse":
            assert len(submitted[0].encode()) < 65536
        evidence.append(
            {
                "internal_grain_days": 15,
                "sql_bytes": len(submitted[0].encode()),
                "payload_bytes": trace.calendar_payload_bytes[-1],
                "source_seconds": trace.source_seconds[-1],
                "returned_rows": trace.returned_rows[-1],
            }
        )
        if backend in ("duckdb", "postgres", "clickhouse"):
            for days in (1, 14):
                axis = session.time_scope(start=start, end=start + timedelta(days=days)).by(
                    mv.grain("minute", count=5)
                )
                p95 = amount.sum(time=point).per(point).quantile(0.95)
                before = len(trace.native_sql)
                values = (
                    session.observe({"p95": p95}, by=(axis,)).compute().to_pandas().p95.tolist()
                )
                assert values == [2] * (days * 288)
                submitted = trace.native_sql[before:]
                assert len(submitted) == 2 and all(len(sql.encode()) < 65536 for sql in submitted)
                evidence.append(
                    {
                        "p95_days": days,
                        "sql_bytes": len(submitted[0].encode()),
                        "payload_bytes": trace.calendar_payload_bytes[-1],
                        "source_seconds": trace.source_seconds[-1],
                    }
                )
        # Exact P95 is not a native source capability on MySQL/Trino; retain that
        # boundary and exercise it on the qualified engines only.
        if backend in ("duckdb", "postgres", "clickhouse"):
            scope = session.time_scope(start=start, end=start + timedelta(days=14))
            p95 = amount.sum(time=point).per(point).quantile(0.95)
            assert session.observe(
                {"p95": p95}, by=(scope,)
            ).compute().to_pandas().p95.tolist() == [2]
        if backend == "clickhouse":
            assert case.session._backend.con.query(
                "SELECT getSetting('readonly'), getSetting('max_query_size')"
            ).first_row == (1, 262144)
        receipt(
            "calendar-capacity-" + backend + "-" + profile,
            {"cases": evidence, "actual_sql": trace.native_sql},
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
                axis = session.time_scope(start=start, end=end).by(mv.grain("minute", count=5))
                pending = session.observe({"total": amount.sum(time=point)}, by=(axis,))
                actual = pending.compute().to_pandas()
                assert actual.total.tolist() == expected


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
        session = mv.session.get_or_create("midnight-fold", timezone="UTC")
        point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        trace = capture_source(monkeypatch)
        for unit in ("day", "month"):
            for start, end, expected in ranges:
                axis = session.time_scope(
                    start=day + "T" + start + "Z",
                    end=day + "T" + end + "Z",
                    timezone=zone,
                ).by(mv.grain(unit))
                before = len(trace.native_sql)
                result = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute()
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
                columns = list(frozen_coordinates(result)[0].rows.columns)
                assert not set(columns) & {
                    "nominal_start",
                    "nominal_end",
                    "actual_start",
                    "actual_end",
                }
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
                else:
                    assert "UNION ALL" not in trace.native_sql[-1].upper()
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
    session = mv.session.get_or_create("pandas-native-calendar", timezone="UTC")
    axis = session.time_scope(start=start, end=end, timezone=zone).by(mv.grain(unit))
    error_type = AmbiguousTimeError if error_name == "AmbiguousTimeError" else NonExistentTimeError
    with pytest.raises(error_type):
        pd.DatetimeIndex(clocks).tz_localize(zone)
    with pytest.raises(MaterializationError, match="pandas native calendar evaluation") as captured:
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
        session = mv.session.get_or_create("native-hour-index", timezone="UTC")
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
            axis = session.time_scope(
                start=f"{day}T{start}Z", end=f"{day}T{end}Z", timezone="America/New_York"
            ).by(mv.grain("hour"))
            total = amount.sum(time=point)
            rows = (
                session.observe(
                    {"total": total, "history": total.window(mv.all_history())}, by=(axis,)
                )
                .compute()
                .to_pandas()
            )
            assert rows["calendar:hour"].dt.tz_convert("UTC").tolist() == expected
            assert rows.total.tolist() == [0] * len(expected)
            assert rows.history.tolist() == [1] * len(expected)
