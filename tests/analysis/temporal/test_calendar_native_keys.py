"""Native equality buckets agree with frozen boundaries without SQL enumeration."""

import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._temporal import certify_period_calendar_rows, semantic_grain
from marivo.analysis.compiler.calendar import bind_calendar, bind_wall_clock
from marivo.analysis.compiler.source_time import localize, timestamp
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource._calendar_relations import CalendarData
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
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


def _data(backend: str, points: list[datetime | None]) -> SourceData:
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
    return exact_scope("2025-01-03T12:00:00", "2026-12-30T06:00:00", "Asia/Shanghai").by(
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
    axes = [
        exact_scope(start, end, zone).by(mv.grain(unit, count=count))
        for start, end, zone, unit, count in (
            ("2026-01-01T00:00:00.000001", "2026-01-01T00:06:00.000001", "UTC", "second", 90),
            ("2026-03-08", "2026-03-09", "America/New_York", "minute", 90),
            ("2026-11-01", "2026-11-02", "America/New_York", "minute", 90),
            ("2026-04-05", "2026-04-06", "Australia/Lord_Howe", "hour", 1),
            ("2026-10-04", "2026-10-05", "Australia/Lord_Howe", "minute", 90),
            ("2025-12-28", "2026-01-06", "Asia/Shanghai", "week", 1),
            ("2018-11-03", "2018-11-06", "America/Sao_Paulo", "day", 1),
            ("2018-11-03", "2018-11-06", "America/Havana", "day", 1),
            ("2009-10-31", "2009-11-02", "America/St_Johns", "day", 1),
            ("2009-10-31", "2009-11-02", "America/St_Johns", "month", 1),
            ("2020-10-31", "2020-11-03", "America/Havana", "month", 1),
            ("2025-12-30T06:00:00", "2026-02-02", "UTC", "month", 1),
            ("2025-12-30", "2026-04-02", "UTC", "quarter", 1),
            ("2025-12-30", "2026-01-02", "UTC", "year", 1),
        )
    ]
    axes += [
        exact_scope("2021-12-27", "2022-01-10", "UTC")
        .by(mv.grain("week"))
        .shift(mv.offset(years=-1)),
        _custom(),
    ]
    points: list[datetime | None] = sorted(
        {
            boundary + timedelta(microseconds=delta)
            for axis in axes
            for cell in axis._binding.cells
            for boundary in cell[2:]
            for delta in (-1, 0, 1)
        }
    )
    points.append(None)
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
            assert normalized == expected
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
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
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
            assert len(sqls) == 1
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
        small = session.time_scope(
            start=start, end=start + timedelta(days=14), timezone="Asia/Shanghai"
        ).by(custom._binding.grain_spec)
        plans = []
        for custom_axis in (small, custom):
            totals = (
                session.observe({"total": amount.sum(time=point)}, by=(custom_axis,))
                .compute()
                .to_pandas()
                .total.tolist()
            )
            assert sum(totals) == len(points)
            assert totals == ([8064] if custom_axis is small else [0, 8064, 0])
            if backend == "mysql":
                with case.session._backend.raw_sql(
                    "EXPLAIN FORMAT=JSON " + trace.native_sql[-1]
                ) as cursor:
                    plan = cursor.fetchone()[0]
                # The constant date map must be built once and probed by key,
                # rather than rescanned for every fact (including on MariaDB).
                receipt(
                    "calendar-plan-" + backend + "-" + profile + "-" + str(custom_axis is small),
                    {"plan": plan},
                )
                assert "materialized" in plan
                assert re.search(r'"access_type"\s*:\s*"ref"', plan) or "hash join" in plan.lower()
                plans.append(plan)

            evidence.append(
                {
                    "custom_days": 14 if custom_axis is small else 726,
                    "sql_bytes": len(trace.native_sql[-1].encode()),
                    "payload_bytes": trace.calendar_payload_bytes[-1],
                    "source_seconds": trace.source_seconds[-1],
                    "returned_rows": trace.returned_rows[-1],
                }
            )
        # A complete internal grain includes one empty day, independently of
        # source sample instants. This also exercises large completion payloads.
        before = len(trace.native_sql)
        scope = session.time_scope(start=start, end=start + timedelta(days=15))
        mean = amount.sum(time=point).per(mv.grain("minute", count=5)).mean()
        assert session.observe({"mean": mean}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == pytest.approx([28 / 15])
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
                assert len(submitted) == 1 and len(submitted[0].encode()) < 65536
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
            {"cases": evidence, "actual_sql": trace.native_sql, "query_plans": plans},
        )


@pytest.mark.parametrize("backend,profile", CASES)
def test_calendar_wall_order_keeps_later_folds_and_transition_gaps(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    points = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in (
            "2026-03-08T01:59:59.999999",
            "2026-03-08T02:00:00",
            "2026-03-08T02:30:00",
            "2026-03-08T02:59:59.999999",
            "2026-03-08T03:00:00",
            "2026-11-01T01:30:00",
        )
    ]
    expected = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in (
            "2026-03-08T06:59:59.999999",
            "2026-03-08T07:00:00",
            "2026-03-08T07:00:00",
            "2026-03-08T07:00:00",
            "2026-03-08T07:00:00",
            "2026-11-01T06:30:00",
        )
    ]
    axis = exact_scope("2026-03-08", "2026-11-02", "America/New_York").by(mv.grain("day"))
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend, points)) as case:
        table = case.session.bind(case.source, source_identity="calendar-wall").relation
        clock = (
            table.point if table.point.type().timezone else localize("UTC", timestamp(table.point))
        )
        result = bind_wall_clock(table, clock, "America/New_York", axis._binding, "instant")
        actual = case.session.collect_bounded(
            lower_temporal(result.order_by("id"), backend), purpose="calendar.wall", max_rows=7
        )
        assert [
            pd.Timestamp(value).tz_localize("UTC")
            if pd.Timestamp(value).tzinfo is None
            else pd.Timestamp(value).tz_convert("UTC")
            for value in actual.instant
        ] == expected


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
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        for clock in ("wall", "encoded"):
            point = catalog_value(session.catalog, "sales.facts." + clock, kind="field")
            for start, end, expected in (
                ("2026-03-08T06:55Z", "2026-03-08T07:10Z", [1, 2, 0]),
                ("2026-11-01T05:25Z", "2026-11-01T06:40Z", [0] * 13 + [1, 0]),
            ):
                axis = session.time_scope(start=start, end=end).by(mv.grain("minute", count=5))
                actual = (
                    session.observe({"total": amount.sum(time=point)}, by=(axis,))
                    .compute()
                    .to_pandas()
                )
                assert actual.total.tolist() == expected


@pytest.mark.parametrize("backend,profile", CASES)
def test_public_calendar_keeps_facts_when_midnight_fold_returns_to_previous_date(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _maria(profile, monkeypatch)
    # At 02:31Z St Johns moves from Nov 1 00:01 back to Oct 31 23:01.
    # The first Nov 1 midnight bucket is [02:30Z, 03:30Z), covering both dates.
    points = [
        datetime.fromisoformat("2009-11-01T" + clock).replace(tzinfo=timezone.utc)
        for clock in (
            "02:29:59.999999",
            "02:30:00",
            "02:30:59.999999",
            "02:31:00",
            "02:45:00",
            "03:29:59.999999",
            "03:30:00",
            "03:30:00.000001",
        )
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
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        trace = capture_source(monkeypatch)
        for unit in ("day", "month"):
            for start, end, expected in (
                ("02:30", "03:30", 5),
                ("02:40", "03:00", 1),
            ):
                axis = session.time_scope(
                    start="2009-11-01T" + start + "Z",
                    end="2009-11-01T" + end + "Z",
                    timezone="America/St_Johns",
                ).by(mv.grain(unit))
                before = len(trace.native_sql)
                result = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute()
                rows = result.to_pandas(time_details=True)
                assert rows.total.tolist() == [expected]
                assert rows[f"calendar:{unit}.nominal_start"].dt.tz_convert("UTC").tolist() == [
                    datetime(2009, 11, 1, 2, 30, tzinfo=timezone.utc)
                ]
                assert rows[f"calendar:{unit}.start"].dt.tz_convert("UTC").tolist() == [
                    datetime.fromisoformat("2009-11-01T" + start + "+00:00")
                ]
                assert rows[f"calendar:{unit}.end"].dt.tz_convert("UTC").tolist() == [
                    datetime.fromisoformat("2009-11-01T" + end + "+00:00")
                ]
                assert len(trace.native_sql) == before + 1
                assert "UNION ALL" not in trace.native_sql[-1].upper()
                if backend == "clickhouse":
                    assert len(trace.native_sql[-1].encode()) < 65536
        receipt(
            "calendar-midnight-fold-" + backend + "-" + profile, {"actual_sql": trace.native_sql}
        )
