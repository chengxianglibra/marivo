"""Native reducer compilation and actual per-parent query cost ownership."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.attribute_native_queries import lower_native_coalition
from marivo.analysis.compiler.graph_plan import PeriodFilter, ReductionRead, read_reduction
from marivo.analysis.session.core import Session
from tests.analysis.materialization.test_attribute_frequencies import _stage
from tests.analysis.materialization.test_frame_attribute_shapley import game_session as game_session
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
def test_native_mean_sql_retains_union_recipes_null_keys_and_periods(backend: str) -> None:
    table = ibis.table(
        {"category": "string", "amount": "float64", "day": "timestamp"}, name="facts"
    )
    stage = _stage(table, backend)
    reduction = read_reduction(stage)
    assert reduction is not None
    stage = replace(
        stage,
        program=ReductionRead(
            replace(reduction, node=replace(reduction.node, reducer="mean", q=None))
        ),
        period=PeriodFilter("day", datetime(2026, 7, 1), datetime(2026, 7, 2)),
    )
    _, request = lower_native_coalition(
        stage,
        stage,
        table.mutate(amount=table.amount + 10),
        table.mutate(amount=table.amount * 2),
        ((None,),),
        (("B",),),
        stage_id="native:mean",
    )
    sql = ibis.to_sql(request.expression, dialect=backend).upper()
    assert "AVG(" in sql and "UNION ALL" in sql
    assert (
        "2026-07-01" in sql
        or "MAKE_TIMESTAMP(2026, 7, 1," in sql
        or "MAKE_TIMESTAMPTZ(2026, 7, 1," in sql
    )
    assert (
        "2026-07-02" in sql
        or "MAKE_TIMESTAMP(2026, 7, 2," in sql
        or "MAKE_TIMESTAMPTZ(2026, 7, 2," in sql
    )
    assert "10" in sql and "* 2" in sql
    assert "IS NULL" in sql or "IS NOT DISTINCT FROM" in sql or "<=>" in sql
    assert "__ATTRIBUTE_FREQUENCY" not in sql


def test_native_parent_methods_keep_only_their_coalition_receipts(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts VALUES "
            "(11,'A',10,1,DATE '2026-07-01','west'),"
            "(12,'B',20,1,DATE '2026-07-01','west'),"
            "(13,'A',11,1,DATE '2026-08-01','west'),"
            "(14,'B',22,1,DATE '2026-08-01','west')"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    day = catalog_value(game_session.catalog, "sales.facts.day", kind="field")
    region = catalog_value(game_session.catalog, "sales.facts.region", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.quantile(0.5) * x.count()
    current = game_session.observe({"q": metric}, by=(region,), where=day >= "2026-08-01")
    baseline = game_session.observe({"q": metric}, by=(region,), where=day < "2026-08-01")
    compared = current.compare(
        {"b": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    ).compute()
    target = compared.select({"q": compared["b.absolute.q"]})
    trace = capture_source(monkeypatch, measure_cost=True)
    report = target.attribute(by=(player,), permutations=16, seed=7).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [2, 2, 3, 3]
    methods = [
        metric.method
        for fact in report.method_facts
        for parent in fact.parents
        for metric in parent.metrics
    ]
    assert len(methods) == 2
    coalition_ids = [
        {c.capture_id for c in method.captures if c.purpose == "coalition"} for method in methods
    ]
    assert all(coalition_ids) and coalition_ids[0].isdisjoint(coalition_ids[1])
    captures = {c.capture_id: c for method in methods for c in method.captures}
    assert len(trace.native_sql) == sum(c.source_reads for c in captures.values())
    assert sum(trace.returned_rows) == sum(c.returned_rows for c in captures.values())
    evidence = report.persist().evidence(limit=100).items
    checks = [
        dict(f.values)
        for f in evidence
        if f.kind == "check" and dict(f.values).get("check") == "attribute_shapley"
    ]
    assert checks and all(c["evaluation"] == "native_queries" for c in checks)
    reads = [
        dict(f.values)
        for f in evidence
        if f.kind == "read" and ":native:" in str(dict(f.values).get("physical_stage", ""))
    ]
    assert reads and all(f["input_id"] and f["physical_stage"] in captures for f in reads)
