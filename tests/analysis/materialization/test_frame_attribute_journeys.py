"""Public contribution targets and explicit investigation continuation journeys."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.release
def test_fixed_multibaseline_targets_keep_their_own_source_bindings(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = case.catalog.metrics.get("sales.revenue")
    region = case.catalog.fields.get("sales.customer.region")
    channel = case.catalog.fields.get("sales.order.channel")
    july = session.observe(
        {"revenue": revenue},
        by=(session.time_scope(start="2026-07-01", end="2026-08-01"), region),
    )
    august = session.observe(
        {"revenue": revenue},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), region),
    )
    september = session.observe(
        {"revenue": revenue},
        by=(session.time_scope(start="2026-09-01", end="2026-10-01"), region),
    )
    compared = september.compare(
        {"july": july, "august": august},
        on={
            "july": tuple(zip(september.axes, july.axes, strict=True)),
            "august": tuple(zip(september.axes, august.axes, strict=True)),
        },
        match={"july": mv.ordinal(), "august": mv.ordinal()},
    ).compute()
    selected = compared.where(
        compared.coord(compared.axis("sales.customer.region")) == "east"
    ).compute()
    targets = selected.select(
        {
            "july_change": selected["july.absolute.revenue"],
            "august_change": selected["august.absolute.revenue"],
        }
    ).compute()
    report = targets.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    for name, expected in (("july_change", 30), ("august_change", 50)):
        assert rows[f"contribution.{name}"].tolist() == [expected]
        assert rows[f"total.{name}"].tolist() == [expected]
        assert rows[f"observed_total.{name}"].tolist() == [expected]
        assert rows[f"contribution_share.{name}"].tolist() == [1]
    assert len(report.axes) == 3


def test_unsupported_quantile_continues_through_explicit_parent_key_bridge(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    amount = case.catalog.fields.get("sales.order.amount")
    region = case.catalog.fields.get("sales.customer.region")
    channel = case.catalog.fields.get("sales.order.channel")
    metric = amount.quantile(0.5)
    observed = session.observe({"median": metric}, by=(region,)).compute()
    parents = observed.where(observed.coord(observed.axes[0]) == "east").compute()
    with pytest.raises(AnalysisError):
        parents.attribute(by=(channel,))
    keys = session.population_from(parents, on=((parents.axes[0], region),))
    detail = session.observe(
        {"median": metric}, by=(region, channel), where=mv.in_population(keys)
    ).compute()
    assert detail.to_pandas()["median"].tolist() == [100]
    assert detail.output_names == ("median",)


@pytest.mark.release
def test_wide_report_preview_and_cold_diagnostics_use_exact_target_paths(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    revenue = case.catalog.metrics.get("sales.revenue")
    region = case.catalog.fields.get("sales.customer.region")
    source = case.session.observe({f"metric_{i}": revenue for i in range(8)})
    report = source.attribute(by=(region,)).compute()
    report.show(n=2)
    text = capsys.readouterr().out
    assert "Target metric_0 (level)" in text
    assert "diagnostic" in text and "output_names" in text
    assert len(text.encode("utf-8")) <= 8192
    artifact = report.persist().artifact
    reopened = case.session.persisted_frame(artifact.id)
    reopened.show(n=2)
    cold_text = capsys.readouterr().out
    assert "Target metric_0 (level)" in cold_text
    assert len(cold_text.encode("utf-8")) <= 8192
    detail_path = "detail.value.metric_7"
    assert detail_path in reopened.output_names
    selected = reopened.select({"original": reopened[detail_path]}).compute()
    assert selected.to_pandas()["original"].tolist() == [610, 50, 0]
    assert selected.method_facts[0].targets == report.method_facts[0].targets
