"""Public temporal consumers on ready native services at the repaired seams."""

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path

import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.compiler.source_time import _hour_clock
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource._temporal_expressions import localize
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.capabilities import StatementPurpose, execute_provider_statement
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, receipt, source_case
from tests.shared_fixtures import catalog_value, datasource_arguments_source
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.runtime


def _model(
    case: Case,
    factory: Callable[[dict[str, str]], SemanticProject],
    declarations: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    wall_clock_timezone: str = "UTC",
) -> None:
    assert isinstance(case.source, TableSourceIR)
    arguments = {
        **case.session.datasource.fields,
        **{key: md.env(value) for key, value in case.session.datasource.env_refs.items()},
    }
    if isinstance(arguments.get("user"), str):
        monkeypatch.setenv("MARIVO_WALL_TEST_READER", str(arguments.pop("user")))
        arguments["user"] = md.env("MARIVO_WALL_TEST_READER")
    factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{case.session.provider.name}(wall_clock_timezone={wall_clock_timezone!r}, name='warehouse',"
            + datasource_arguments_source(arguments)
            + ")\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
            + "amount=ms.measure_column(name='amount',entity=events,column='amount',additivity=ms.additive_all())\n"
            + declarations,
        }
    )


def test_mysql_public_native_membership_and_projection_require_selected_zone_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from marivo.datasource.engines import mysql

    data = SourceData(
        "id BIGINT, clock DATETIME(6), text VARCHAR(30), amount BIGINT",
        "(1,'2026-03-08 02:30','08/03/2026 02:30',10),(2,'2026-03-08 03:00','08/03/2026 03:00',20)",
        "",
        [],
    )
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        _model(
            case,
            semantic_project_factory,
            "clock=ms.time_dimension_column(name='clock',entity=events,column='clock',parse=ms.timestamp(timezone='America/New_York'))\n"
            "parsed=ms.time_dimension_column(name='parsed',entity=events,column='text',parse=ms.strptime('%d/%m/%Y %H:%M',timezone='America/New_York'))\n",
            monkeypatch,
        )
        monkeypatch.chdir(tmp_path)
        session = mv.session.get_or_create("missing-selected-zone", timezone="UTC")
        original_statement = execute_provider_statement
        probes: list[str] = []

        def missing_zone(
            backend: BaseBackend,
            profile: EngineProfile,
            statement_id: str,
            *,
            values: Mapping[str, object] = {},
            identifiers: Mapping[str, str | tuple[str, ...]] = {},
            purpose: StatementPurpose,
            parameters: Sequence[object] | Mapping[str, str] | None = None,
        ) -> tuple[dict[str, object], ...]:
            if statement_id == "mysql.timezone.verify":
                probes.append(str(values["timezone"]))
                return ({"converted": None},)
            return original_statement(
                backend,
                profile,
                statement_id,
                values=values,
                identifiers=identifiers,
                purpose=purpose,
                parameters=parameters,
            )

        monkeypatch.setattr(mysql, "execute_provider_statement", missing_zone)
        clock = catalog_value(session.catalog, "sales.events.clock", kind="field")
        amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
        assert isinstance(clock, ms.TimeDimension) and isinstance(amount, ms.Measure)
        scope = session.time_scope(start="2026-03-08T07:00Z", end="2026-03-08T07:15Z")
        executed: list[CompiledRead] = []
        original_execute = SourceSession.execute

        def traced(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            executed.append(read)
            return original_execute(owner, read)

        monkeypatch.setattr(SourceSession, "execute", traced)
        entity = session.catalog.get("entity:sales.events")
        assert isinstance(entity, ms.Entity)
        members = session.members(entity)
        parsed = catalog_value(session.catalog, "sales.events.parsed", kind="field")
        assert isinstance(parsed, ms.TimeDimension)
        operations = {
            "native_membership": session.observe({"value": amount.sum(time=clock)}, by=(scope,)),
            "parsed_membership": session.observe(
                {"value": amount.sum(time=clock)}, where=parsed.within(scope)
            ),
            "output": session.read(members, {"clock": parsed}),
        }
        outcomes: dict[str, object] = {}
        for name, pending in operations.items():
            probes.clear()
            executed.clear()
            # Every live native conversion requires the selected timezone,
            # including predicates; no wall-clock replacement bypasses it.
            with pytest.raises(DatasourceConnectionError) as failure:
                pending.compute()
            assert failure.value.received == "America/New_York"
            assert probes == ["America/New_York"]
            assert all("CONVERT_TZ(" not in read.sql.upper() for read in executed)
            outcomes[name] = {
                "probes": list(probes),
                "executed_purposes": [read.purpose for read in executed],
                "conversion_read_executed": False,
            }
        receipt(
            "consumers-r1-mysql",
            {"unconverted_sql": tuple(source_trace.native_sql), "native_failures": outcomes},
        )


def test_clickhouse_hour_sampling_uses_non_utc_server_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    port = os.environ.get("MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT")
    if port is None:
        pytest.skip(
            "Explicit Asia/Shanghai ClickHouse server required: MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT"
        )
    monkeypatch.setenv("MARIVO_CLICKHOUSE_TEST_PORT", port)
    data = SourceData(
        "",
        "",
        "id UInt8, day Date, hour UInt8, amount Int64",
        [
            {"id": 1, "day": date(2026, 5, 1), "hour": 0, "amount": 4},
            {"id": 2, "day": date(2026, 5, 1), "hour": 0, "amount": 6},
            {"id": 3, "day": date(2026, 5, 1), "hour": 1, "amount": 20},
            {"id": 4, "day": date(2026, 4, 30), "hour": 23, "amount": 100},
            {"id": 5, "day": date(2026, 5, 1), "hour": 2, "amount": 1000},
        ],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        native = case.session._backend.con
        header = native.query("SELECT version(), timezone()").first_row
        assert header is not None
        version, zone = header
        assert zone == "Asia/Shanghai"
        # No session timezone setting or per-user timezone override is applied.
        # The server's own Date + hour conversion supplies the independent oracle.
        oracle_sql = (
            "SELECT point, sum(amount) AS sample FROM (SELECT "
            "toTimeZone(toDateTime(day) + toIntervalHour(hour), 'UTC') AS point, amount "
            f"FROM qualification.{case.source.table}) WHERE "
            "point >= toDateTime('2026-04-30 16:00:00', 'UTC') AND "
            "point < toDateTime('2026-04-30 18:00:00', 'UTC') GROUP BY point ORDER BY point"
        )
        oracle = native.query(oracle_sql).result_rows
        assert [(pd.Timestamp(point).tz_convert("UTC"), value) for point, value in oracle] == [
            (pd.Timestamp("2026-04-30T16:00:00Z"), 10),
            (pd.Timestamp("2026-04-30T17:00:00Z"), 20),
        ]
        table = case.session.bind(case.source, source_identity="wall_carrier").relation
        assert isinstance(table.day, ir.DateValue)
        expression = lower_temporal(
            table.order_by(table.id).select(
                carrier=localize("UTC", _hour_clock(table.hour, table.day, engine="clickhouse"))
            ),
            "clickhouse",
        )
        actual = case.session.collect_bounded(
            expression, purpose="temporal.wall_carrier", max_rows=6
        )
        normalized = pd.to_datetime(actual.carrier, utc=True).tolist()
        assert normalized == [
            pd.Timestamp("2026-05-01T00:00:00Z"),
            pd.Timestamp("2026-05-01T00:00:00Z"),
            pd.Timestamp("2026-05-01T01:00:00Z"),
            pd.Timestamp("2026-04-30T23:00:00Z"),
            pd.Timestamp("2026-05-01T02:00:00Z"),
        ]
        carrier_sql = case.session.submissions[-1].sql
        _model(
            case,
            semantic_project_factory,
            "day=ms.time_dimension_column(name='day',entity=events,column='day')\n"
            "hour=ms.time_dimension_column(name='hour',entity=events,column='hour',parse=ms.hour_prefix(day),sample_interval=(1,'hour'))\n",
            monkeypatch,
            wall_clock_timezone="Asia/Shanghai",
        )
        monkeypatch.chdir(tmp_path)
        session = mv.session.get_or_create("server-hour-samples", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
        hour = catalog_value(session.catalog, "sales.events.hour", kind="field")
        assert isinstance(amount, ms.Measure) and isinstance(hour, ms.TimeDimension)
        scope = session.time_scope(start="2026-04-30T16:00Z", end="2026-04-30T18:00Z")
        total = amount.sum(time=hour)
        result = (
            session.observe(
                {
                    "mean": total.per(hour).mean(),
                    "total": total,
                    "samples": total.per(hour).count(),
                },
                by=(scope,),
            )
            .compute()
            .to_pandas()
        )
        samples = [value for _, value in oracle]
        assert result["mean"].tolist() == [sum(samples) / len(samples)] == [15.0]
        assert result["total"].tolist() == [sum(samples)] == [30]
        assert result["samples"].tolist() == [len(samples)] == [2]
        receipt(
            "consumers-clickhouse-server-shanghai",
            {
                "version": version,
                "server_timezone": zone,
                "oracle_sql": oracle_sql,
                "native_sample_keys": [str(point) for point, _ in oracle],
                "native_sample_values": samples,
                "carrier_sql": carrier_sql,
                "actual_wall_carriers": [str(value) for value in normalized],
                "public_values": result[["mean", "total", "samples"]].to_dict("records"),
            },
        )
