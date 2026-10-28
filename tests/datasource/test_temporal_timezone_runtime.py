"""Native temporal kind, transport and parser checks on ready provider services."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pandas as pd
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import (
    parse_strptime,
    source_time,
    source_time_comparison,
    source_time_predicate,
)
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource.adapters import provider_for
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.timezone import probe_engine_timezone, verify_source_timezone
from marivo.semantic.ir import HourPrefixParse, StrptimeParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.datasource.source_cases import SourceData, receipt, source_case

pytestmark = pytest.mark.runtime


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
                            read_timezone="America/New_York",
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
                    assert "parse=ms.timestamp()" in error.repair.action
                    point, authority = source_time(
                        table.point,
                        native_axis,
                        boundary_timezone="UTC",
                        read_timezone="America/New_York",
                        engine="clickhouse",
                    )
                    precise, precise_authority = source_time(
                        table.precise,
                        native_axis,
                        boundary_timezone="UTC",
                        read_timezone="America/New_York",
                        engine="clickhouse",
                    )
                    assert authority.kind == precise_authority.kind == "instant"
                    assert authority.source == precise_authority.source == "physical"
                    assert authority.read_timezone == precise_authority.read_timezone == "UTC"
                    assert precise_authority.physical_type == str(table.precise.type())
                    parsed, parsed_authority = source_time(
                        table.text,
                        axis("%Y-%m-%d %H:%M:%S", "Asia/Shanghai"),
                        boundary_timezone="UTC",
                        read_timezone="America/New_York",
                        engine="clickhouse",
                    )
                    assert parsed_authority.kind == "localizable"
                    assert parsed_authority.source == "declared"
                    civil = parse_strptime(
                        "clickhouse", table.text.substr(0, 10), StrptimeParse("%Y-%m-%d")
                    )
                    composite, composite_authority = source_time(
                        table.hour,
                        replace(native_axis, parse=HourPrefixParse(prefix="day")),
                        boundary_timezone="UTC",
                        read_timezone="America/New_York",
                        engine="clickhouse",
                        prefix=civil,
                        prefix_timezone="Asia/Shanghai",
                    )
                    assert composite_authority.read_timezone == "Asia/Shanghai"
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
                    assert "parseDateTimeOrNull" in sql and "'UTC'" in sql
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
        assert probe_engine_timezone(owner._backend).engine_timezone_name == "UTC"
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
            read_timezone="Asia/Shanghai",
            engine="mysql",
        )
        clock, clock_authority = source_time(
            table.clock,
            replace(native_axis, parse=TimestampParse(timezone="Asia/Shanghai")),
            boundary_timezone="UTC",
            read_timezone="UTC",
            engine="mysql",
        )
        assert authority.kind == "instant" and authority.source == "physical"
        assert clock_authority.kind == "localizable" and clock_authority.source == "declared"
        expression = lower_temporal(table.select(point=point, clock=clock), "mysql")
        assert isinstance(expression, ir.Table)
        frame = owner.collect_bounded(expression, purpose="temporal.native", max_rows=2)
        expected = pd.Timestamp("2026-04-30 16:30:00.123456")
        assert frame.point.tolist() == frame.clock.tolist() == [expected]
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
def test_raw_wall_clock_gap_fold_comparisons_keep_native_columns_bare(
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
            table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine=backend
        )
        gap = datetime(2026, 3, 8, 7, tzinfo=timezone.utc)
        comparisons = {
            "equal": source_time_comparison(
                table.point, field, authority, bound=gap, comparison="=="
            ),
            "strict": source_time_comparison(
                table.point, field, authority, bound=gap, comparison=">"
            ),
            "before": source_time_comparison(
                table.point, field, authority, bound=gap, comparison="<"
            ),
            "at_most": source_time_comparison(
                table.point, field, authority, bound=gap, comparison="<="
            ),
            "at_least": source_time_comparison(
                table.point, field, authority, bound=gap, comparison=">="
            ),
            "unequal": source_time_comparison(
                table.point, field, authority, bound=gap, comparison="!="
            ),
            "gap_cell": source_time_predicate(
                table.point, field, authority, start=gap, end=gap + timedelta(minutes=15)
            ),
            "first_fold": source_time_predicate(
                table.point,
                field,
                authority,
                start=datetime(2026, 11, 1, 5, tzinfo=timezone.utc),
                end=datetime(2026, 11, 1, 6, tzinfo=timezone.utc),
            ),
        }
        assert all(value is not None for value in comparisons.values())
        expression = table.select(table.id, **comparisons).order_by("id")
        frame = owner.collect_bounded(expression, purpose="temporal.raw", max_rows=8)
        expected = {
            "equal": [2, 3, 4],
            "strict": [5, 6],
            "before": [1],
            "at_most": [1, 2, 3, 4],
            "at_least": [2, 3, 4, 5, 6],
            "unequal": [1, 5, 6],
            "gap_cell": [2, 3, 4],
            "first_fold": [],
        }
        for name, ids in expected.items():
            assert frame.loc[frame[name] == True, "id"].tolist() == ids  # noqa: E712
            assert pd.isna(frame.loc[frame.id == 7, name].iloc[0])
        sql = owner.submissions[-1].sql
        assert "CONVERT_TZ" not in sql.upper() and "parseDateTime" not in sql
        assert re.search(r'CAST\([^)]*["`]point["`]', sql) is None
        receipt(
            f"adapters-runtime-wall-order-{backend}",
            {"environment": case.environment, "expected_ids": expected, "sql": sql},
        )
