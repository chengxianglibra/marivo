"""Hour partition filters preserve native row selection without weakening Boolean values."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.source_time import source_time, source_time_predicate
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.semantic._source_timezone import DeclaredSourceTimezone
from marivo.semantic.ir import DateParse, HourPrefixParse, StrptimeParse, TargetDimensionContract
from marivo.semantic.reader import SemanticProject
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import SourceTrace


def _dimension(catalog: ms.SemanticCatalog, path: str) -> ms.Dimension:
    result = catalog.get(path)
    assert result.kind == "dimension"
    return result


def _entity(catalog: ms.SemanticCatalog, path: str) -> ms.Entity:
    result = catalog.get(path)
    assert result.kind == "entity"
    return result


def _metric(catalog: ms.SemanticCatalog, path: str) -> ms.Metric:
    result = catalog.get(path)
    assert result.kind == "metric"
    return result


def _time_dimension(catalog: ms.SemanticCatalog, path: str) -> ms.TimeDimension:
    result = catalog.get(path)
    assert result.kind == "time_dimension"
    return result


def _inputs(
    table: ir.Table, *, zone: str = "Asia/Shanghai", engine: str = "duckdb"
) -> tuple[TargetDimensionContract, SourceTimeAuthority, TargetDimensionContract]:
    day = replace(
        axis("%Y%m%d"),
        parse=DateParse(timezone=zone)
        if table.day.type().is_date()
        else StrptimeParse("%Y%m%d", timezone=zone),
    )
    prefix, _ = source_time(
        table.day,
        day,
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone(zone, "field"),
        engine=engine,
    )
    hour = replace(axis("%Y%m%d%H"), parse=HourPrefixParse(day.ref.path))
    _, authority = source_time(
        table.hour,
        hour,
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone(zone, "field", day.ref.path),
        prefix=prefix,
        engine=engine,
    )
    return hour, authority, day


@pytest.mark.parametrize("prefix_type", ["date", "string", "int64"])
@pytest.mark.parametrize("hour_type", ["string", "int8"])
@pytest.mark.parametrize(
    "bounds", [("00:00", "01:00"), ("03:00", "04:00"), ("03:01", "04:30"), ("03:01", "03:59")]
)
def test_positive_hour_filter_matches_native_selection_and_keeps_boolean_nulls(
    prefix_type: str, hour_type: str, bounds: tuple[str, str]
) -> None:
    days = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29), None]
    pairs = [(day, hour) for day in days for hour in (None, 0, 2, 3, 4, 5, 23)]
    data = {
        "id": list(range(len(pairs))),
        "day": [
            day
            if prefix_type == "date" or day is None
            else int(day.strftime("%Y%m%d"))
            if prefix_type == "int64"
            else day.strftime("%Y%m%d")
            for day, _ in pairs
        ],
        "hour": [
            f"{hour:02d}" if hour_type == "string" and hour is not None else hour
            for _, hour in pairs
        ],
    }
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "events",
            ibis.memtable(data, schema={"id": "int64", "day": prefix_type, "hour": hour_type}),
        )
        hour, authority, day = _inputs(table)
        start, end = [
            datetime.fromisoformat(f"2026-10-28T{bound}").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            for bound in bounds
        ]
        exact = source_time_predicate(
            table.hour, hour, authority, start=start, end=end, prefix=table.day, prefix_axis=day
        )
        filtered = source_time_predicate(
            table.hour,
            hour,
            authority,
            start=start,
            end=end,
            prefix=table.day,
            prefix_axis=day,
            use="filter",
        )
        assert exact is not None and filtered is not None
        native_day = (
            "CAST(day AS TIMESTAMP)"
            if prefix_type == "date"
            else "try_strptime(CAST(day AS VARCHAR), '%Y%m%d')"
        )
        native = f"({native_day} + CAST(hour AS BIGINT) * INTERVAL '1 hour')"
        native_range = f"{native} >= TIMESTAMP '2026-10-28 {bounds[0]}' AND {native} < TIMESTAMP '2026-10-28 {bounds[1]}'"
        expected = backend.con.execute(
            f"SELECT id FROM events WHERE {native_range} ORDER BY id"
        ).fetchall()
        assert backend.execute(table.filter(filtered).order_by("id")).id.tolist() == [
            row[0] for row in expected
        ]
        actual_values = backend.execute(
            table.select("id", p=exact, complement=~exact).order_by("id")
        )
        expected_values = backend.con.execute(
            f"SELECT id, ({native_range}) AS p, NOT ({native_range}) AS complement FROM events ORDER BY id"
        ).fetchdf()
        import pandas as pd

        nullable = {"p": "boolean", "complement": "boolean"}
        pd.testing.assert_frame_equal(
            actual_values.astype(nullable), expected_values.astype(nullable), check_dtype=False
        )
        assert not tuple(filtered.op().find(ops.IfElse))
    finally:
        backend.disconnect()


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("start_hour", [0, 3])
@pytest.mark.parametrize(
    "prefix_type,hour_type", [("date", "int8"), ("string", "string"), ("int64", "int64")]
)
def test_hour_filter_exposes_typed_date_and_hour_constraints(
    engine: str, prefix_type: str, hour_type: str, start_hour: int
) -> None:
    table = ibis.table({"day": prefix_type, "hour": hour_type}, name="events")
    hour, authority, day = _inputs(table, engine=engine)
    start = datetime(2026, 10, 28, start_hour, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start.replace(hour=start_hour + 1)
    predicate = source_time_predicate(
        table.hour,
        hour,
        authority,
        start=start,
        end=end,
        prefix=table.day,
        prefix_axis=day,
        engine=engine,
        use="filter",
    )
    assert predicate is not None
    sql = str(ibis.to_sql(lower_temporal(table.filter(predicate), engine), dialect=engine))
    where = sqlglot.parse_one(sql, read=engine).find(sge.Where)
    assert where is not None
    assert not list(where.find_all(sge.Case, sge.If, sge.Not, sge.Or)), sql
    comparisons = list(where.find_all(sge.EQ, sge.GTE, sge.LT))
    expected_comparisons = 4 if engine == "mysql" and prefix_type == "string" else 3
    assert len(comparisons) == expected_comparisons and all(
        isinstance(item.this, sge.Column) for item in comparisons
    ), sql
    hour_bounds = [item.expression for item in comparisons if item.this.name == "hour"]
    assert (
        {item.this for item in hour_bounds} == {f"{start_hour:02d}", f"{start_hour + 1:02d}"}
        if hour_type == "string"
        else {item.this for item in hour_bounds} == {str(start_hour), str(start_hour + 1)}
    )
    assert all(item.is_string == (hour_type == "string") for item in hour_bounds)


@pytest.mark.parametrize(
    "case",
    [
        "dst",
        "derived_hour",
        "derived_prefix",
    ],
)
def test_unqualified_hour_filters_keep_the_exact_predicate(case: str) -> None:
    table = ibis.table({"day": "string", "hour": "string"}, name="events")
    zone = "America/New_York" if case == "dst" else "Asia/Shanghai"
    hour, authority, day = _inputs(table, zone=zone)
    start = datetime(2026, 3, 8, 3, tzinfo=ZoneInfo(zone))
    end = start.replace(hour=4)
    raw = table.hour.concat("") if case == "derived_hour" else table.hour
    prefix = table.day.concat("") if case == "derived_prefix" else table.day
    exact = source_time_predicate(
        raw,
        hour,
        authority,
        start=start,
        end=end,
        prefix=prefix,
        prefix_axis=day,
    )
    filtered = source_time_predicate(
        raw,
        hour,
        authority,
        start=start,
        end=end,
        prefix=prefix,
        prefix_axis=day,
        use="filter",
    )
    assert exact is not None and filtered is not None and filtered.equals(exact)


@pytest.mark.parametrize("prefix_type", ["date", "string", "int64"])
@pytest.mark.parametrize("hour_type", ["string", "int8"])
@pytest.mark.parametrize(
    "start_text,end_text",
    [
        ("2024-02-28T00:00:00+08:00", "2024-02-29T00:00:00+08:00"),
        ("2024-02-28T23:30:00+08:00", "2024-03-01T01:30:00+08:00"),
        ("2024-12-31T23:00:00+08:00", "2025-01-01T00:00:00+08:00"),
        (None, "2024-03-01T00:00:00+08:00"),
        ("2024-02-28T15:30:00+00:00", None),
    ],
)
def test_multiday_hour_filter_matches_independent_clock_and_preserves_values(
    prefix_type: str, hour_type: str, start_text: str | None, end_text: str | None
) -> None:
    days = [
        date.fromisoformat(value)
        for value in (
            "2024-02-27",
            "2024-02-28",
            "2024-02-29",
            "2024-03-01",
            "2024-12-31",
            "2025-01-01",
        )
    ] + [None]
    pairs = [(day, hour) for day in days for hour in (None, 0, 1, 10, 23)]
    data = {
        "id": list(range(len(pairs))),
        "day": [
            day
            if prefix_type == "date" or day is None
            else int(day.strftime("%Y%m%d"))
            if prefix_type == "int64"
            else day.strftime("%Y%m%d")
            for day, _ in pairs
        ],
        "hour": [f"{h:02d}" if hour_type == "string" and h is not None else h for _, h in pairs],
    }
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "events",
            ibis.memtable(data, schema={"id": "int64", "day": prefix_type, "hour": hour_type}),
        )
        hour, authority, day = _inputs(table)
        start = datetime.fromisoformat(start_text) if start_text else None
        end = datetime.fromisoformat(end_text) if end_text else None
        exact = source_time_predicate(
            table.hour, hour, authority, start=start, end=end, prefix=table.day, prefix_axis=day
        )
        filtered = source_time_predicate(
            table.hour,
            hour,
            authority,
            start=start,
            end=end,
            prefix=table.day,
            prefix_axis=day,
            use="filter",
        )
        assert exact is not None and filtered is not None
        # A raw date predicate must be a required top-level conjunct, not hidden in P.
        pending = [filtered.op()]
        conjuncts = []
        while pending:
            atom = pending.pop()
            if isinstance(atom, ops.And):
                pending.extend((atom.left, atom.right))
            else:
                conjuncts.append(atom)
        date_guards = [
            atom
            for atom in conjuncts
            if isinstance(atom, (ops.GreaterEqual, ops.Less))
            and isinstance(atom.left, ops.Field)
            and atom.left.name == "day"
        ]
        assert len(date_guards) == int(start is not None) + int(end is not None)
        native_day = (
            "CAST(day AS TIMESTAMP)"
            if prefix_type == "date"
            else "try_strptime(CAST(day AS VARCHAR), '%Y%m%d')"
        )
        clock = f"({native_day} + CAST(hour AS BIGINT) * INTERVAL '1 hour')"
        boundaries = []
        params = []
        for op, bound in ((">=", start), ("<", end)):
            if bound is not None:
                boundaries.append(f"{clock} {op} ?")
                params.append(bound.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
        predicate = " AND ".join(boundaries)
        expected = backend.con.execute(
            f"SELECT id FROM events WHERE {predicate} ORDER BY id", params
        ).fetchall()
        assert backend.execute(table.filter(filtered).order_by("id")).id.tolist() == [
            r[0] for r in expected
        ]
        # Exact values and Boolean composition remain ordinary SQL Booleans.
        actual = backend.execute(
            table.select(
                "id",
                p=exact,
                neg=~exact,
                disjunction=exact | (table.id == 0),
            ).order_by("id")
        )
        expected_values = backend.con.execute(
            f"SELECT id, ({predicate}) p, NOT ({predicate}) neg, "
            f"({predicate}) OR id=0 disjunction FROM events ORDER BY id",
            params * 3,
        ).fetchdf()
        import pandas as pd

        nullable = {
            "p": "boolean",
            "neg": "boolean",
            "disjunction": "boolean",
        }
        pd.testing.assert_frame_equal(
            actual.astype(nullable), expected_values.astype(nullable), check_dtype=False
        )
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "first,last,guard_count",
    [
        ("2026-03-01T00:00:00-05:00", "2026-03-15T00:00:00-04:00", 2),
        ("2026-03-08T03:00:00-04:00", "2026-03-15T00:00:00-04:00", 1),
        ("2026-03-01T00:00:00-05:00", "2026-03-08T03:00:00-04:00", 1),
    ],
)
def test_date_guards_across_dst_use_only_safe_endpoints(
    first: str, last: str, guard_count: int
) -> None:
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "events",
            ibis.memtable(
                {
                    "id": list(range(11)),
                    "day": [
                        "20260228",
                        "20260301",
                        "20260307",
                        "20260308",
                        "20260308",
                        "20260308",
                        "20260309",
                        "20260314",
                        "20260315",
                        None,
                        "20260308",
                    ],
                    "hour": ["23", "00", "23", "01", "02", "03", "00", "23", "00", "03", None],
                },
                schema={"id": "int64", "day": "string", "hour": "string"},
            ),
        )
        hour, authority, day = _inputs(table, zone="America/New_York")
        start, end = datetime.fromisoformat(first), datetime.fromisoformat(last)
        filtered = source_time_predicate(
            table.hour,
            hour,
            authority,
            start=start,
            end=end,
            prefix=table.day,
            prefix_axis=day,
            use="filter",
        )
        assert filtered is not None
        pending = [filtered.op()]
        guards = []
        while pending:
            atom = pending.pop()
            if isinstance(atom, ops.And):
                pending.extend((atom.left, atom.right))
            elif (
                isinstance(atom, (ops.GreaterEqual, ops.Less))
                and isinstance(atom.left, ops.Field)
                and atom.left.name == "day"
            ):
                guards.append(atom)
        assert len(guards) == guard_count
        native = "timezone('America/New_York', try_strptime(day, '%Y%m%d') + CAST(hour AS BIGINT) * INTERVAL '1 hour')"
        expected = backend.con.execute(
            f"SELECT id FROM events WHERE {native} >= ? AND {native} < ? ORDER BY id", [start, end]
        ).fetchall()
        assert backend.execute(table.filter(filtered).order_by("id")).id.tolist() == [
            r[0] for r in expected
        ]
    finally:
        backend.disconnect()


@pytest.fixture
def hour_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> mv.Session:
    database = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE events(id BIGINT, day VARCHAR, hour VARCHAR, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO events VALUES (1,'20261028','03',11),(2,'20261028','03',13),(3,'20261028','04',17),(4,'20261028','00',19),(5,'20261029',NULL,23),(6,NULL,'03',29),(7,NULL,'08',31),(8,'20261028',NULL,37),(9,'20261028','02',41)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r},wall_clock_timezone='Asia/Shanghai')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/events.py": "import marivo.semantic as ms\nimport marivo.datasource as md\n"
            "events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table('events'),primary_key=['id'])\n"
            "identity=ms.dimension_column(name='id',entity=events,column='id')\n"
            "day=ms.time_dimension_column(name='day',entity=events,column='day',parse=ms.strptime('%Y%m%d'))\n"
            "hour=ms.time_dimension_column(name='hour',entity=events,column='hour',parse=ms.hour_prefix(day))\n"
            "@ms.metric()\ndef hourly_count():\n    return events.count(time=hour)\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    return mv.session.get_or_create("hour-filter", timezone="Asia/Shanghai")


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("scope", 2),
        ("calendar", 2),
        ("midnight", 1),
        ("within", 2),
        ("and", 1),
        ("not", 3),
        ("or", 3),
        ("isnull", 4),
    ],
)
def test_public_observe_only_pushes_required_hour_membership(
    hour_session: mv.Session, source_trace: SourceTrace, declared: bool, mode: str, expected: int
) -> None:
    session = hour_session
    events = _entity(session.catalog, "entity:sales.events")
    hour = _time_dimension(session.catalog, "time_dimension:sales.events.hour")
    identity = _dimension(session.catalog, "dimension:sales.events.id")
    metric = (
        _metric(session.catalog, "metric:sales.hourly_count")
        if declared
        else events.count(time=hour)
    )
    scope = session.time_scope(start="2026-10-28T03:00", end="2026-10-28T04:00")
    if mode == "midnight":
        scope = session.time_scope(start="2026-10-28T00:00", end="2026-10-28T01:00")
    if mode in ("scope", "calendar", "midnight"):
        frame = session.observe(
            {"n": metric}, by=(scope.by(mv.grain("hour")) if mode != "scope" else scope,)
        )
    else:
        predicate = hour.within(scope)
        if mode == "and":
            predicate = predicate & (identity == 1)
        elif mode == "not":
            predicate = ~predicate
        elif mode == "or":
            predicate = predicate | (identity == 6)
        elif mode == "isnull":
            predicate = predicate.isnull()
        frame = session.observe({"n": metric}, where=predicate)
    assert frame.compute().to_pandas().n.tolist() == [expected]
    statements = [sqlglot.parse_one(sql, read="duckdb") for sql in source_trace.native_sql]
    has_day_equality = any(
        isinstance(item.this, sge.Column)
        and item.this.name == "day"
        and isinstance(item.expression, sge.Literal)
        and item.expression.this == "20261028"
        for statement in statements
        for where in statement.find_all(sge.Where)
        for item in where.find_all(sge.EQ)
    )
    assert has_day_equality == (mode in ("scope", "calendar", "midnight", "within", "and")), (
        source_trace.native_sql
    )


@pytest.mark.parametrize(
    "mode,expected",
    [("within", 3), ("not", 2), ("or", 4), ("isnull", 4), ("not_isnull", 4), ("not_notnull", 5)],
)
def test_public_multiday_boolean_conditions(
    hour_session: mv.Session, mode: str, expected: int
) -> None:
    session = hour_session
    events = _entity(session.catalog, "entity:sales.events")
    hour = _time_dimension(session.catalog, "time_dimension:sales.events.hour")
    identity = _dimension(session.catalog, "dimension:sales.events.id")
    scope = session.time_scope(start="2026-10-28T03:00", end="2026-10-29T04:00")
    predicate = hour.within(scope)
    if mode == "not":
        predicate = ~predicate
    elif mode == "or":
        predicate = predicate | (identity == 6)
    elif mode == "isnull":
        predicate = predicate.isnull()
    elif mode == "not_isnull":
        predicate = (~predicate).isnull()
    elif mode == "not_notnull":
        predicate = (~predicate).notnull()
    actual = session.observe({"n": events.count(time=hour)}, where=predicate).compute()
    assert actual.to_pandas().n.tolist() == [expected]


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("mysql", "mariadb"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("integer", [False, True])
@pytest.mark.parametrize("start_hour", [0, 3])
def test_hour_filter_executes_on_native_backends(
    backend: str,
    profile: str,
    integer: bool,
    start_hour: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    pairs = [
        ("20261028", 3),
        ("20261028", 3),
        ("20261028", 4),
        ("20261027", 23),
        ("20261029", None),
        (None, 3),
        ("20261028", None),
        ("20261028", 0),
        ("20261028", 1),
        ("20261029", 0),
    ]
    rows: list[dict[str, object]] = [
        {"id": index, "day": day, "hour": hour if integer or hour is None else f"{hour:02d}"}
        for index, (day, hour) in enumerate(pairs, start=1)
    ]
    values = ",".join(
        "(" + ",".join("NULL" if value is None else repr(value) for value in row.values()) + ")"
        for row in rows
    )
    data = SourceData(
        f"id BIGINT, day VARCHAR(8), hour {'BIGINT' if integer else 'VARCHAR(2)'}",
        values,
        f"id Int64, day Nullable(String), hour Nullable({'Int64' if integer else 'String'})",
        rows,
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="hour-filter").relation
        hour, authority, day = _inputs(table, engine=backend)
        start = datetime(2026, 10, 28, start_hour, tzinfo=ZoneInfo("Asia/Shanghai"))
        predicate = source_time_predicate(
            table.hour,
            hour,
            authority,
            start=start,
            end=start.replace(hour=start_hour + 1),
            prefix=table.day,
            prefix_axis=day,
            engine=backend,
            use="filter",
        )
        assert predicate is not None
        query = lower_temporal(table.filter(predicate).select("id").order_by("id"), backend)
        actual = case.session.collect_bounded(query, purpose="time.hour_filter", max_rows=8)
        assert actual.id.tolist() == ([8] if start_hour == 0 else [1, 2])
        if not integer and start_hour == 0:
            for first, last, expected in (
                ("2026-10-28T00:00:00+08:00", "2026-10-29T00:00:00+08:00", [1, 2, 3, 8, 9]),
                ("2026-10-27T23:30:00+08:00", "2026-10-29T00:30:00+08:00", [1, 2, 3, 8, 9, 10]),
            ):
                predicate = source_time_predicate(
                    table.hour,
                    hour,
                    authority,
                    start=datetime.fromisoformat(first),
                    end=datetime.fromisoformat(last),
                    prefix=table.day,
                    prefix_axis=day,
                    engine=backend,
                    use="filter",
                )
                assert predicate is not None
                query = lower_temporal(table.filter(predicate).select("id").order_by("id"), backend)
                actual = case.session.collect_bounded(
                    query, purpose="time.hour_filter", max_rows=12
                )
                assert actual.id.tolist() == expected
