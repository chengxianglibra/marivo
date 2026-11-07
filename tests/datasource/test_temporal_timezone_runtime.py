"""Native temporal kind, transport and parser checks on ready provider services."""

from __future__ import annotations

import os
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import (
    parse_strptime,
    source_time,
    source_time_predicate,
)
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource.adapters import provider_for
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.timezone import verify_source_timezone
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import HourPrefixParse, StrptimeParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.datasource.source_cases import SourceData, receipt, source_case
from tests.shared_fixtures import declared_test_timezone

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory

pytestmark = pytest.mark.runtime


@pytest.mark.skipif(
    os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
)
def test_trino_dynamic_seconds_preserve_sign_null_and_integer_precision() -> None:
    from tests.datasource.aggregate_fixtures import compiler_backend
    from tests.datasource.environment.trino_analysis import connection

    seconds = [-18000, -14400, 0, 20700, 2**53 - 1, -(2**53 - 1), None]
    table = ibis.table({"n": "int64"}, name="offsets")
    query = table.select("n", duration=table.n.as_interval("s"))
    compiled = compiler_backend("trino").compile(query)
    assert isinstance(compiled, str) and "PARSE_DURATION" not in compiled.upper()
    values = ",".join(f"(CAST({'NULL' if n is None else n} AS BIGINT))" for n in seconds)
    sql = (
        f"WITH offsets(n) AS (VALUES {values}) "
        f"SELECT n, to_milliseconds(duration), typeof(duration) FROM ({compiled}) AS converted"
    )
    with connection() as owner:
        cursor = owner.cursor()
        try:
            rows = cursor.execute(sql).fetchall()
        finally:
            cursor.close()
    assert len(rows) == len(seconds)
    assert {n: milliseconds for n, milliseconds, _ in rows} == {
        n: None if n is None else n * 1000 for n in seconds
    }
    assert all(kind == "interval day to second" for _, _, kind in rows)


@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
)
@pytest.mark.parametrize(
    ("column", "parser", "valid"),
    (
        ("point", "ms.timestamp(timezone='UTC')", False),
        ("point", "ms.timestamp()", True),
        ("text", "ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')", True),
    ),
)
def test_clickhouse_catalog_validation_shares_preview_and_analysis_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: _SemanticProjectFactory,
    column: str,
    parser: str,
    valid: bool,
) -> None:
    data = SourceData(
        columns="",
        values="",
        clickhouse_columns="point DateTime, text String, amount Int64",
        rows=[
            {
                "point": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "text": "2026-08-01 00:00:00",
                "amount": 2,
            }
        ],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        monkeypatch.setenv("MARIVO_SOURCE_PROFILE_USER", "analysis_reader")
        project = semantic_project_factory(
            {
                "datasources/warehouse.py": (
                    "import marivo.datasource as md\n"
                    "md.clickhouse(name='warehouse', host='127.0.0.1', port=18123, "
                    "database='qualification', user=md.env('MARIVO_SOURCE_PROFILE_USER'), "
                    "password=md.env('MARIVO_SOURCE_PROFILE_PASSWORD'))\n"
                ),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
                "sales/events.py": (
                    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                    f"events=ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}))\n"
                    f"point=ms.time_dimension_column(name='point', entity=events, column={column!r}, parse={parser})\n"
                    "amount=ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
                    "@ms.metric()\ndef total():\n    return amount.sum(time=point)\n"
                ),
            }
        )
        monkeypatch.chdir(tmp_path)
        catalog = SemanticCatalog(project)
        report = catalog.validate([catalog.get("metric:sales.total")])
        assert all(not check.user_data_queried for check in report.checks)
        session = mv.session.get_or_create("native-physical-validation", timezone="UTC")
        metric = session.catalog.get("metric:sales.total")
        assert metric.kind == "metric"
        period = session.time_scope(start="2026-08-01", end="2026-08-02")
        scope = md.unpruned(max_rows=5, timeout_seconds=10)
        if valid:
            assert report.status == "current", report.to_dict()
            assert catalog.preview(
                catalog.get("time_dimension:sales.events.point"), scope=scope
            ).rows
            assert session.observe({"total": metric}, where=period).compute().to_pandas()[
                "total"
            ].tolist() == [2]
        else:
            assert report.status == "failed", report.to_dict()
            schema = next(check for check in report.checks if check.kind == "schema")
            assert "already-aware physical source" in str(schema.observed["contract_violations"])
            with pytest.raises(
                SemanticRuntimeError, match="an aware source without parser timezone"
            ):
                catalog.preview(catalog.get("time_dimension:sales.events.point"), scope=scope)
            with pytest.raises(
                SemanticRuntimeError, match="an aware source without parser timezone"
            ):
                session.observe({"total": metric}, where=period).compute()


@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
)
def test_clickhouse_implicit_instants_and_encoding_in_non_utc_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    instant = datetime(2026, 4, 30, 16, 30, tzinfo=timezone.utc)
    data = SourceData(
        columns="",
        values="",
        clickhouse_columns="point DateTime, precise DateTime64(6), text String, hour UInt8",
        rows=[
            {
                "point": instant,
                "precise": instant.replace(microsecond=123456),
                "text": "2026-05-01 00:30:00",
                "hour": 0,
            }
        ],
    )
    user = "temporal_" + uuid4().hex
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        with ch.connection(admin=True) as admin:
            admin.command(
                f"CREATE USER {user} IDENTIFIED BY {{password:String}} "
                "SETTINGS readonly=1, session_timezone='America/New_York'",
                parameters={"password": password()},
            )
            try:
                admin.command(f"GRANT SELECT ON qualification.{case.source.table} TO {user}")
                datasource = replace(
                    case.session.datasource,
                    fields={**case.session.datasource.fields, "user": user},
                )
                with provider_for("clickhouse").open(datasource) as owner:
                    session_zone = owner._backend.con.query(
                        "SELECT getSetting('session_timezone')"
                    ).first_row
                    assert session_zone == ("America/New_York",)
                    bound = owner.bind(case.source, source_identity="temporal_native")
                    table = bound.relation
                    assert table.point.type().timezone is None
                    assert table.point.type().scale == 0
                    assert table.precise.type().timezone is None
                    assert table.precise.type().scale == 6
                    native_axis = replace(axis("%Y-%m-%d %H:%M:%S"), parse=TimestampParse())
                    with pytest.raises(DatasetCompilationError) as caught:
                        source_time(
                            table.point,
                            replace(native_axis, parse=TimestampParse(timezone="UTC")),
                            boundary_timezone="UTC",
                            source_timezone=declared_test_timezone(native_axis, "America/New_York"),
                            engine="clickhouse",
                        )
                    error = caught.value
                    assert error.location == (
                        "analysis.source_time[time_dimension:sales.events.point]"
                    )
                    assert error.received is not None
                    assert "clickhouse" in error.received
                    assert "timestamp(0)" in error.received
                    assert "parser_timezone='UTC'" in error.received
                    assert error.repair is not None
                    assert "parse=ms.timestamp()" in error.repair.action
                    point, authority = source_time(
                        table.point,
                        native_axis,
                        boundary_timezone="UTC",
                        source_timezone=declared_test_timezone(native_axis, "America/New_York"),
                        engine="clickhouse",
                    )
                    precise, precise_authority = source_time(
                        table.precise,
                        native_axis,
                        boundary_timezone="UTC",
                        source_timezone=declared_test_timezone(native_axis, "America/New_York"),
                        engine="clickhouse",
                    )
                    assert authority.kind == precise_authority.kind == "instant"
                    assert set(authority.model_dump()) == {"kind", "axis", "physical_type"}
                    assert set(precise_authority.model_dump()) == {"kind", "axis", "physical_type"}
                    assert precise_authority.physical_type == str(table.precise.type())
                    parsed, parsed_authority = source_time(
                        table.text,
                        axis("%Y-%m-%d %H:%M:%S", "Asia/Shanghai"),
                        boundary_timezone="UTC",
                        source_timezone=declared_test_timezone(
                            axis("%Y-%m-%d %H:%M:%S", "Asia/Shanghai")
                        ),
                        engine="clickhouse",
                    )
                    assert parsed_authority.kind == "wall_clock"
                    assert parsed_authority.origin == "field"
                    civil = parse_strptime(
                        "clickhouse", table.text.substr(0, 10), StrptimeParse("%Y-%m-%d")
                    )
                    composite, composite_authority = source_time(
                        table.hour,
                        replace(native_axis, parse=HourPrefixParse(prefix="day")),
                        boundary_timezone="UTC",
                        source_timezone=declared_test_timezone(
                            replace(
                                axis("%Y-%m-%d"),
                                parse=StrptimeParse("%Y-%m-%d", timezone="Asia/Shanghai"),
                            ),
                            inherited_from="day",
                        ),
                        engine="clickhouse",
                        prefix=civil,
                    )
                    assert composite_authority.kind == "wall_clock"
                    assert composite_authority.timezone == "Asia/Shanghai"
                    assert composite_authority.origin == "field"
                    assert composite_authority.inherited_from == "day"
                    native_range = source_time_predicate(
                        table.precise,
                        native_axis,
                        precise_authority,
                        start=instant.replace(microsecond=123456),
                        end=instant.replace(microsecond=123457),
                    )
                    assert native_range is not None
                    expression = lower_temporal(
                        table.filter(native_range).select(
                            point=point,
                            precise=precise,
                            parsed=parsed,
                            civil=civil,
                            composite=composite,
                        ),
                        "clickhouse",
                    )
                    assert isinstance(expression, ir.Table)
                    frame = owner.collect_bounded(expression, purpose="temporal.native", max_rows=2)
                    assert frame.point.tolist() == [pd.Timestamp(instant)]
                    assert frame.precise.tolist() == [
                        pd.Timestamp(instant.replace(microsecond=123456))
                    ]
                    assert pd.Timestamp(frame.parsed.iloc[0]).tz_localize(None) == pd.Timestamp(
                        "2026-04-30 16:30:00"
                    )
                    assert pd.Timestamp(frame.civil.iloc[0]).date() == date(2026, 5, 1)
                    assert pd.Timestamp(frame.composite.iloc[0]).tz_localize(None) == pd.Timestamp(
                        "2026-04-30 16:00:00"
                    )
                    sql = owner.submissions[-1].sql
                    assert "toDateTime64OrNull" in sql and "'UTC'" in sql
                    receipt(
                        "adapters-runtime-clickhouse",
                        {
                            "environment": case.environment,
                            "session_timezone": session_zone[0],
                            "schema": str(table.schema()),
                            "authority": authority.model_dump(),
                            "precise_authority": precise_authority.model_dump(),
                            "parsed_authority": parsed_authority.model_dump(),
                            "composite_authority": composite_authority.model_dump(),
                            "values": {name: str(frame[name].iloc[0]) for name in frame.columns},
                            "sql": sql,
                        },
                    )
            finally:
                admin.command(f"DROP USER IF EXISTS {user}")


@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_utc_timestamp_transport_and_named_source_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.engines.mysql import verify_source_timezone as verify_mysql

    data = SourceData(
        columns="point TIMESTAMP(6), clock DATETIME(6)",
        values="('2026-04-30 16:30:00.123456','2026-05-01 00:30:00.123456')",
        clickhouse_columns="",
        rows=[],
    )
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        owner = case.session
        with owner._backend.raw_sql("SELECT @@session.time_zone") as result:
            assert result.fetchone() == ("+00:00",)
        bound = owner.bind(case.source, source_identity="temporal_native")
        table = bound.relation
        assert table.point.type().timezone == "UTC"
        assert table.clock.type().timezone is None
        assert table.point.type().scale == table.clock.type().scale == 6
        verify_source_timezone(owner._backend, "Asia/Shanghai")
        with pytest.raises(DatasourceConnectionError) as failure:
            verify_mysql(owner._backend, "Marivo/Unavailable")
        assert failure.value.received == "Marivo/Unavailable"
        native_axis = replace(axis("%Y-%m-%d %H:%M:%S"), parse=TimestampParse())
        point, authority = source_time(
            table.point,
            native_axis,
            boundary_timezone="UTC",
            source_timezone=declared_test_timezone(native_axis, "Asia/Shanghai"),
            engine="mysql",
        )
        clock, clock_authority = source_time(
            table.clock,
            replace(native_axis, parse=TimestampParse(timezone="Asia/Shanghai")),
            boundary_timezone="UTC",
            source_timezone=declared_test_timezone(
                replace(native_axis, parse=TimestampParse(timezone="Asia/Shanghai"))
            ),
            engine="mysql",
        )
        assert authority.kind == "instant"
        assert clock_authority.kind == "wall_clock" and clock_authority.origin == "field"
        expression = lower_temporal(table.select(point=point, clock=clock), "mysql")
        assert isinstance(expression, ir.Table)
        frame = owner.collect_bounded(expression, purpose="temporal.native", max_rows=2)
        expected = pd.Timestamp("2026-04-30 16:30:00.123456")
        assert frame.point.tolist() == [expected.tz_localize("UTC")]
        assert frame.clock.tolist() == [expected.tz_localize("UTC")]
        receipt(
            "adapters-runtime-mysql",
            {
                "environment": case.environment,
                "schema": str(table.schema()),
                "authority": authority.model_dump(),
                "clock_authority": clock_authority.model_dump(),
                "values": {name: str(frame[name].iloc[0]) for name in frame.columns},
                "sql": owner.submissions[-1].sql,
                "unavailable_zone": failure.value.received,
                "timezone_statements": [
                    asdict(item) for item in provider_statement_log(owner._backend)
                ],
            },
        )


@pytest.mark.parametrize("backend", ("mysql", "clickhouse"))
def test_gap_fold_ranges_match_native_source_localization(
    backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clocks = [
        "2026-03-08 01:59:59",
        "2026-03-08 02:00:00",
        "2026-03-08 02:30:00",
        "2026-03-08 03:00:00",
        "2026-03-08 03:15:00",
        "2026-11-01 01:30:00",
        None,
    ]
    values = ",".join(
        f"({index}," + ("NULL" if point is None else f"'{point}'") + ")"
        for index, point in enumerate(clocks, 1)
    )
    data = SourceData(
        columns="id BIGINT, point DATETIME(6)",
        values=values,
        clickhouse_columns="id Int64, point Nullable(String)",
        rows=[{"id": index, "point": point} for index, point in enumerate(clocks, 1)],
    )
    profile = "innodb-table" if backend == "mysql" else "mergetree"
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        owner = case.session
        table = owner.bind(case.source, source_identity="temporal_raw").relation
        field = axis("%Y-%m-%d %H:%M:%S", "America/New_York")
        if backend == "mysql":
            field = replace(field, parse=TimestampParse(timezone="America/New_York"))
        _, authority = source_time(
            table.point,
            field,
            boundary_timezone="UTC",
            source_timezone=declared_test_timezone(field),
            engine=backend,
        )
        ranges = {
            "gap_cell": (
                datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
                datetime(2026, 3, 8, 7, 15, tzinfo=timezone.utc),
            ),
            "first_fold": (
                datetime(2026, 11, 1, 5, tzinfo=timezone.utc),
                datetime(2026, 11, 1, 6, tzinfo=timezone.utc),
            ),
            "second_fold": (
                datetime(2026, 11, 1, 6, tzinfo=timezone.utc),
                datetime(2026, 11, 1, 7, tzinfo=timezone.utc),
            ),
        }
        predicates = {
            name: source_time_predicate(
                table.point, field, authority, start=start, end=end, engine=backend
            )
            for name, (start, end) in ranges.items()
        }
        assert all(value is not None for value in predicates.values())
        expression = lower_temporal(table.select(table.id, **predicates).order_by("id"), backend)
        assert isinstance(expression, ir.Table)
        frame = owner.collect_bounded(expression, purpose="temporal.native_dst", max_rows=8)
        assert isinstance(case.source, TableSourceIR)
        if backend == "mysql":
            localized = "CONVERT_TZ(point, 'America/New_York', '+00:00')"
            table_name = f"`{case.source.table}`"
        else:
            localized = "toTimeZone(toDateTime64(point, 6, 'America/New_York'), 'UTC')"
            table_name = f"qualification.`{case.source.table}`"
        expected: dict[str, list[int]] = {}
        for name, (start, end) in ranges.items():
            lower = start.strftime("%Y-%m-%d %H:%M:%S")
            upper = end.strftime("%Y-%m-%d %H:%M:%S")
            if backend == "clickhouse":
                lower_sql = f"toDateTime('{lower}', 'UTC')"
                upper_sql = f"toDateTime('{upper}', 'UTC')"
            else:
                lower_sql, upper_sql = repr(lower), repr(upper)
            native_sql = (
                f"SELECT id FROM {table_name} WHERE {localized} >= {lower_sql} "
                f"AND {localized} < {upper_sql} ORDER BY id"
            )
            if backend == "clickhouse":
                rows = owner._backend.con.query(native_sql).result_rows
            else:
                with owner._backend.raw_sql(native_sql) as cursor:
                    rows = cursor.fetchall()
            expected[name] = [int(row[0]) for row in rows]
            assert frame.loc[frame[name] == True, "id"].tolist() == expected[name]  # noqa: E712
            assert pd.isna(frame.loc[frame.id == 7, name].iloc[0])
        receipt(
            f"adapters-runtime-native-dst-{backend}",
            {
                "environment": case.environment,
                "expected_ids": expected,
                "sql": owner.submissions[-1].sql,
            },
        )


@pytest.mark.parametrize(
    "zone, hours", (("UTC", 0), ("Asia/Tokyo", 9), ("UTC+08:00", 8), ("UTC-05:00", -5))
)
@pytest.mark.parametrize("analysis", (False, True))
def test_clickhouse_date_offsets_ignore_server_timezone(
    zone: str, hours: int, analysis: bool
) -> None:
    from marivo.datasource._temporal_expressions import localize, timestamp
    from marivo.datasource._temporal_lowering import lower_temporal as lower_native
    from tests.datasource.environment.clickhouse_analysis import connection

    port = os.environ.get("MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT")
    if port is None:
        pytest.skip("Explicit non-UTC ClickHouse server required")
    expression = localize(zone, timestamp(ibis.literal(date(2026, 7, 1))))
    lowered = (lower_temporal if analysis else lower_native)(expression, "clickhouse")
    with connection(port=int(port)) as client:
        assert client.query("SELECT timezone()").result_rows == [("Asia/Shanghai",)]
        expected = client.query(
            f"SELECT toDateTime64('2026-07-01 00:00:00', 6, 'UTC') - INTERVAL {hours} HOUR"
        ).result_rows
        assert client.query(str(ibis.to_sql(lowered, dialect="clickhouse"))).result_rows == expected
