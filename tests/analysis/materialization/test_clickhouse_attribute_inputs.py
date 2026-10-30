"""Native Enum, Date identity and single-shard proxy Attribute boundaries."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.environment import clickhouse_analysis as ch
from tests.datasource.source_cases import Case, SourceData, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("shape", ("enum8", "nullable_enum16", "date", "proxy", "multishard_proxy"))
def test_clickhouse_attribute_input_carriers(
    shape: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    enum = (
        "String"
        if shape == "date"
        else (
            "Nullable(Enum16('z_finish'=2, 'a_exception'=4, 'start'=1))"
            if shape == "nullable_enum16"
            else "Enum8('z_finish'=2, 'a_exception'=4, 'start'=1)"
        )
    )
    date_key = shape in ("date", "proxy", "multishard_proxy")
    is_proxy = shape.endswith("proxy")
    distributed = shape == "multishard_proxy"
    profile = "distributed" if distributed else "mergetree"
    ports = ch.CLUSTER_HTTP_PORTS if distributed else (18123,)
    rows: list[dict[str, object]] = [
        {
            "id": date(2026, 10, day) if date_key else str(i),
            "tenant": tenant,
            "category": event,
            "amount": amount,
            "big": 1,
            "weight": 1,
            "day": date(2026, 10, day),
        }
        for i, (day, tenant, event, amount) in enumerate(
            (
                (21, "Insert", "z_finish", 1000),
                (22, "Insert", "z_finish", 10),
                (22, "Select", "a_exception", 20),
                (25, "Insert", "z_finish", 30),
                (26, "Insert", "a_exception", 40),
                (25, "Select", "z_finish", 50),
                (26, "Select", "start", 60),
            )
        )
    ]
    data = SourceData(
        "",
        "",
        f"id {'Date' if date_key else 'String'}, tenant String, category {enum}, amount Int64, big Int64, weight Int64, day Date",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case("clickhouse", profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        database, table = case.source.database, case.source.table
        proxy, view = table + "_proxy", table + "_view"
        try:
            if is_proxy:
                local = table.removesuffix("_distributed") if distributed else table
                cluster = ch.CLUSTER if distributed else "marivo_single_shard"
                for port in ports:
                    with ch.connection(admin=True, port=port) as admin:
                        admin.command(
                            f"CREATE VIEW {database}.{view} AS SELECT * FROM {database}.{local} WHERE day >= toDate('2026-10-22')"
                        )
                        admin.command(
                            f"CREATE TABLE {database}.{proxy} AS {database}.{local} ENGINE=Distributed('{cluster}', '{database}', '{view}', rand())"
                        )
                case = Case(case.session, TableSourceIR(proxy, database), case.environment)
            _project("clickhouse", case, monkeypatch, semantic_project_factory)
            session = mv.session.get_or_create("ck-inputs", timezone="UTC")
            entity = session.catalog.get("entity:sales.facts")
            tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
            event = catalog_value(session.catalog, "sales.facts.category", kind="field")
            metric = entity.count().where(event.isin(("z_finish", "a_exception")))
            current = session.observe(
                {"n": metric}, by=(session.time_scope(start="2026-10-25", end="2026-10-29"),)
            ).compute()
            baseline = session.observe(
                {"n": metric}, by=(session.time_scope(start="2026-10-22", end="2026-10-25"),)
            ).compute()
            trace = capture_source(monkeypatch)
            level = current.attribute(by=(tenant,)).compute().to_pandas()
            assert level["contribution.n"].tolist() == [2, 1]
            compared = current.compare(
                {"baseline": mv.baseline(baseline, match=mv.ordinal())}
            ).compute()
            report = compared.attribute(by=(tenant,)).compute().to_pandas()
            assert report["contribution.baseline.absolute.n"].tolist() == [1, 0]
            assert report["contribution.baseline.relative.n"].tolist() == [0.5, 0]
            assert report["total.baseline.absolute.n"].tolist() == [1, 1]
            repr(report)
            print(report)
            assert len(pd.concat([report, report.copy()])) == 4
            # A native Enum sort is numeric (a_exception > z_finish); exported strings sort oppositely.
            fields = session.read(session.members(entity), {"event": event})
            assert fields.top(2, by=fields["event"]).compute().to_pandas()["event"].tolist() == [
                "z_finish",
                "z_finish",
            ]
            if shape == "enum8":
                amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
                nonlinear = amount.median().where(event.isin(("z_finish", "a_exception")))
                left = session.observe(
                    {"m": nonlinear}, by=(session.time_scope(start="2026-10-25", end="2026-10-29"),)
                )
                right = session.observe(
                    {"m": nonlinear}, by=(session.time_scope(start="2026-10-22", end="2026-10-25"),)
                )
                game = left.compare({"base": mv.baseline(right, match=mv.ordinal())})
                game = game.select({"change": game["base.absolute.m"]})
                game_rows = game.attribute(by=(tenant,), permutations=4).compute().to_pandas()
                assert game_rows["total.change"].tolist() == [25, 25]
                assert game_rows["contribution.change"].sum() == pytest.approx(25)
            if is_proxy:
                assert session.observe({"n": metric}).compute().to_pandas()["n"].tolist() == [5]
                assert all(proxy in sql for sql in trace.native_sql)
                assert not any("distributed_product_mode" in sql for sql in trace.native_sql)
                empty = session.observe(
                    {"n": metric}, by=(session.time_scope(start="2026-10-19", end="2026-10-20"),)
                ).compute()
                zero = current.compare(
                    {"baseline": mv.baseline(empty, match=mv.ordinal())}
                ).compute()
                from marivo.analysis.errors import AnalysisError

                with pytest.raises(AnalysisError, match="baseline=0"):
                    zero.attribute(by=(tenant,)).compute()
        finally:
            if is_proxy:
                for port in ports:
                    with ch.connection(admin=True, port=port) as admin:
                        admin.command(f"DROP TABLE IF EXISTS {database}.{proxy}")
                        admin.command(f"DROP VIEW IF EXISTS {database}.{view}")


def test_multishard_distributed_attribute_uses_global_joins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    from tests.analysis.numeric.test_remote_numeric_query import DATA

    monkeypatch.chdir(tmp_path)
    with source_case("clickhouse", "distributed", tmp_path, monkeypatch, DATA) as case:
        _project("clickhouse", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("multishard", timezone="UTC")
        entity = session.catalog.get("entity:sales.facts")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
        parent = session.observe({"n": entity.count()}).compute()
        trace = capture_source(monkeypatch)
        result = parent.attribute(by=(tenant,)).compute().to_pandas()
        assert result["contribution.n"].tolist() == [2, 2]
        assert any("GLOBAL" in sql for sql in trace.native_sql)
