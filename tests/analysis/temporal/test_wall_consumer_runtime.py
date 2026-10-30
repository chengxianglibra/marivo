"""Public temporal consumers on ready native services at the repaired seams."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
from marivo.analysis.compiler.source_time import _hour_clock, localize
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.capabilities import StatementPurpose
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, receipt, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.runtime


def _model(
    case: Case,
    factory: Callable[[dict[str, str]], SemanticProject],
    declarations: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: str | None = None,
) -> None:
    assert isinstance(case.source, TableSourceIR)
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    if user is not None:
        arguments["user"] = user
    monkeypatch.setenv("MARIVO_WALL_TEST_READER", str(arguments.pop("user")))
    arguments["user_env"] = "MARIVO_WALL_TEST_READER"
    factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{case.session.provider.name}(name='warehouse',"
            + ",".join(f"{key}={value!r}" for key, value in arguments.items())
            + ")\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
            + "amount=ms.measure_column(name='amount',entity=events,column='amount',additivity=ms.additive_all())\n"
            + declarations,
        }
    )


def test_mysql_public_native_projection_requires_selected_zone_before_read(
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
        original_statement = mysql.execute_provider_statement
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
        scope = session.time_scope(start="2026-03-08T07:00Z", end="2026-03-08T07:15Z")
        # Raw membership and raw bucket assignment need no native conversion.
        assert session.observe(
            {"value": amount.sum(time=clock)}, by=(scope,)
        ).compute().to_pandas()["value"].tolist() == [30]
        assert probes == []
        raw_sql = tuple(source_trace.native_sql)

        executed: list[CompiledRead] = []
        original_execute = SourceSession.execute

        def traced(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            executed.append(read)
            return original_execute(owner, read)

        monkeypatch.setattr(SourceSession, "execute", traced)
        members = session.members(session.catalog.get("entity:sales.events"))
        parsed = catalog_value(session.catalog, "sales.events.parsed", kind="field")
        # Parsed wall membership compares the safe neutral clock and needs
        # no native named-zone conversion. Output still requires that capability.
        probes.clear()
        executed.clear()
        filtered = session.observe(
            {"value": amount.sum(time=clock)}, where=parsed.within(scope)
        ).compute()
        assert filtered.to_pandas()["value"].tolist() == [30]
        assert probes == []
        assert len(executed) == 1 and "STR_TO_DATE" in executed[0].sql.upper()
        # Unused native projections may remain in submitted subqueries; only
        # the final projection closure owns timezone-table qualification.
        outcomes: dict[str, object] = {
            "filter": {"probes": [], "conversion_read_executed": False, "value": 30}
        }
        probes.clear()
        executed.clear()
        with pytest.raises(DatasourceConnectionError) as failure:
            session.read(members, {"clock": parsed}).compute()
        assert failure.value.received == "America/New_York"
        assert probes == ["America/New_York"]
        assert all("CONVERT_TZ(" not in read.sql.upper() for read in executed)
        assert len(executed) == 1  # Independent identity-domain read.
        outcomes["output"] = {
            "probes": list(probes),
            "executed_purposes": [read.purpose for read in executed],
            "conversion_read_executed": False,
        }
        receipt(
            "consumers-r1-mysql",
            {
                "raw_count": 30,
                "raw_count_zone_probes": [],
                "raw_count_sql": raw_sql,
                "native_failures": outcomes,
            },
        )


def test_clickhouse_hour_sampling_keeps_neutral_wall_carrier_in_non_utc_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from dataclasses import replace

    from marivo.datasource.adapters import provider_for
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    data = SourceData(
        "",
        "",
        "id UInt8, day Date, hour UInt8, amount Int64",
        [
            {"id": 1, "day": date(2026, 5, 1), "hour": 0, "amount": 10},
            {"id": 2, "day": date(2026, 5, 1), "hour": 1, "amount": 20},
        ],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        user = "wall_sample_" + uuid4().hex
        with ch.connection(admin=True) as admin:
            admin.command(
                f"CREATE USER {user} IDENTIFIED BY {{password:String}} SETTINGS readonly=1, session_timezone='America/New_York'",
                parameters={"password": password()},
            )
            try:
                admin.command(f"GRANT SELECT ON qualification.{case.source.table} TO {user}")
                datasource = replace(
                    case.session.datasource, fields={**case.session.datasource.fields, "user": user}
                )
                with provider_for("clickhouse").open(datasource) as owner:
                    assert owner._backend.con.query(
                        "SELECT getSetting('session_timezone')"
                    ).first_row == ("America/New_York",)
                    bound = owner.bind(case.source, source_identity="wall_carrier")
                    table = bound.relation
                    assert isinstance(table.day, ir.DateValue)
                    expression = lower_temporal(
                        table.select(
                            carrier=localize(
                                "UTC", _hour_clock(table.hour, table.day, engine="clickhouse")
                            )
                        ),
                        "clickhouse",
                    )
                    actual = owner.collect_bounded(
                        expression, purpose="temporal.wall_carrier", max_rows=3
                    )
                    normalized = [
                        pd.Timestamp(value).tz_localize("UTC")
                        if pd.Timestamp(value).tzinfo is None
                        else pd.Timestamp(value).tz_convert("UTC")
                        for value in actual.carrier
                    ]
                    assert normalized == [
                        pd.Timestamp("2026-05-01T00:00:00Z"),
                        pd.Timestamp("2026-05-01T01:00:00Z"),
                    ]
                    carrier_sql = owner.submissions[-1].sql
                _model(
                    case,
                    semantic_project_factory,
                    "day=ms.time_dimension_column(name='day',entity=events,column='day')\n"
                    "hour=ms.time_dimension_column(name='hour',entity=events,column='hour',parse=ms.hour_prefix(day),sample_interval=(1,'hour'))\n",
                    monkeypatch,
                    user=user,
                )
                monkeypatch.chdir(tmp_path)
                session = mv.session.get_or_create("neutral-hour-samples", timezone="UTC")
                amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
                hour = catalog_value(session.catalog, "sales.events.hour", kind="field")
                scope = session.time_scope(start="2026-05-01T04:00Z", end="2026-05-01T06:00Z")
                mean = (
                    session.observe({"mean": amount.sum(time=hour).per(hour).mean()}, by=(scope,))
                    .compute()
                    .to_pandas()["mean"]
                    .tolist()
                )
                assert mean == [15.0]
                receipt(
                    "consumers-r1-clickhouse",
                    {
                        "session_timezone": "America/New_York",
                        "carrier_sql": carrier_sql,
                        "actual_wall_carriers": [str(value) for value in normalized],
                        "public_mean": mean,
                    },
                )
            finally:
                admin.command(f"DROP USER IF EXISTS {user}")
