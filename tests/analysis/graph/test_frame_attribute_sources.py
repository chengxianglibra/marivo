"""Shared source inventories contain requested recipes in their captured order."""

import pytest

from marivo.analysis.binding.frame_attribute_sources import collect_source_groups
from tests.shared_fixtures import DslCaseFactory


def test_grouped_source_metrics_keep_original_order_and_exclude_unrequested_recipes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    source = case.session.observe(
        {"sum": amount.sum(), "mean": amount.mean(), "unused": amount.max()}
    )
    selected = source.select({"average": source["mean"], "revenue": source["sum"]})
    assert selected._derivation is not None
    groups = collect_source_groups(selected._derivation)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert tuple(quantity.name for quantity in group.quantities) == ("sum", "mean")
    assert group.index(("mean",)) == 1
    assert group.index(("sum",)) == 0


def test_comparison_and_level_references_share_each_endpoint_requested_inventory(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    current = case.session.observe({"sum": amount.sum(), "mean": amount.mean()})
    baseline = case.session.observe({"sum": amount.sum(), "mean": amount.mean()})
    compared = current.compare({"reference": baseline})
    targets = compared.select(
        {"level": compared["sum"], "change": compared["reference.absolute.mean"]}
    )
    assert targets._derivation is not None
    groups = collect_source_groups(targets._derivation)
    assert len(groups) == 2
    assert {
        group.key.side: tuple(q.name for q in group.quantities) for group in groups.values()
    } == {
        "left": ("sum", "mean"),
        "right": ("mean",),
    }


def test_distinct_metric_formulas_share_one_actual_read_and_exclude_unused_rules(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb
    import pandas as pd

    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    channel = case.session.catalog.fields.get("sales.order.channel")
    captured = case.session.observe(
        {"sum": amount.sum(), "count": amount.count(), "unused": amount.max()}
    ).compute()
    selected = captured.select({"n": captured["count"], "s": captured["sum"]}).compute()
    with duckdb.connect(str(case.database_path)) as connection:
        oracle = connection.execute(
            f'SELECT channel,SUM(amount),COUNT(amount) FROM "{case.names.order}" GROUP BY channel ORDER BY channel'
        ).fetchall()
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" in rows:
            reads.append(issued)
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    report = selected.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert len(reads) == 1
    assert rows.iloc[:, 0].tolist() == [row[0] for row in oracle]
    assert rows[("value", "contribution.s")].tolist() == [row[1] for row in oracle]
    assert rows[("value", "contribution.n")].tolist() == [row[2] for row in oracle]


def test_reordered_comparison_metrics_and_level_target_share_endpoint_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb
    import pandas as pd

    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    channel = case.session.catalog.fields.get("sales.order.channel")
    current = case.session.observe({"sum": amount.sum(), "count": amount.count()})
    baseline = case.session.observe({"count": amount.count(), "sum": amount.sum()})
    compared = current.compare({"reference": baseline}).compute()
    selected = compared.select(
        {
            "level": compared["sum"],
            "baseline_level": compared["reference.baseline.sum"],
            "sumchange": compared["reference.absolute.sum"],
            "countchange": compared["reference.absolute.count"],
        }
    ).compute()
    with duckdb.connect(str(case.database_path)) as connection:
        oracle = connection.execute(
            f'SELECT channel,SUM(amount),COUNT(amount) FROM "{case.names.order}" GROUP BY channel ORDER BY channel'
        ).fetchall()
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" in rows:
            reads.append(issued)
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert len(reads) == 2
    assert rows[("value", "contribution.level")].tolist() == [row[1] for row in oracle]
    assert rows[("value", "contribution.baseline_level")].tolist() == [row[1] for row in oracle]
    for name, index in (("sumchange", 1), ("countchange", 2)):
        assert rows[("value", f"contribution.{name}")].tolist() == [0] * len(oracle)
        for side in ("current", "baseline"):
            assert rows[("value", f"detail.{side}.{name}")].tolist() == [
                row[index] for row in oracle
            ]
