"""Executable disclosure journeys; characters measure context, not model tokens."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import PublicHelpTarget, render_help_text
from marivo._help.render import help as public_help
from marivo.analysis.errors import AnalysisError
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory

BASELINE = Path(__file__).with_name("analysis_context_baseline.json")


class Trace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pages: list[tuple[str, str]] = []
        self.identities: dict[str, str] = {}

    def read(self, label: str, call: Callable[[], None]) -> str:
        output = StringIO()
        with redirect_stdout(output):
            call()
        text = output.getvalue()
        assert text, label
        self.pages.append((label, text))
        return text

    def help(self, target: PublicHelpTarget) -> str:
        assert marivo.help is public_help
        _, surface, canonical_id = render_help_text(target)
        return self.read(
            surface + ("." + canonical_id if canonical_id else ""),
            lambda: public_help(target),
        )

    def normalized_outputs(self) -> list[str]:
        normalized: list[str] = []
        for _, text in self.pages:
            text = text.replace(str(self.root), "<project>")
            text = text.replace(str(Path(marivo.__file__).resolve()), "<package>")
            text = text.replace(sys.executable, "<python>")
            for identity, placeholder in self.identities.items():
                text = text.replace(identity, placeholder)
            normalized.append(text)
        return normalized

    def metrics(self) -> dict[str, int]:
        return {
            "raw_characters": sum(len(text) for _, text in self.pages),
            "characters": sum(map(len, self.normalized_outputs())),
            "pages": len(self.pages),
            "repeated_pages": len(self.pages) - len({label for label, _ in self.pages}),
        }


def test_repeated_reads_are_charged(tmp_path: Path) -> None:
    trace = Trace(tmp_path)
    first = trace.help(mv.mean)
    trace.help("analysis.dsl.mean")
    assert trace.metrics() == {
        "raw_characters": len(first) * 2,
        "characters": len(first) * 2,
        "pages": 2,
        "repeated_pages": 1,
    }


def test_baseline_retains_complete_outputs() -> None:
    baseline = json.loads(BASELINE.read_text())
    for name, metrics in baseline["metrics"].items():
        assert len(baseline["outputs"][name]) == metrics["pages"]
        assert sum(len(text) for _, text in baseline["outputs"][name]) == metrics["raw_characters"]
        assert sum(map(len, baseline["normalized_outputs"][name])) == metrics["characters"]


@pytest.mark.runtime
def test_task_context_budget(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    metric = ms.ref.metric("sales.order_count")
    buyer = ms.ref.relationship("sales.order_buyer")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    values = members.observe(metric, during=august, via=buyer, by=(mv.member(),))
    assert isinstance(values, mv.LogicalNumericRelation)
    traces: dict[str, Trace] = {}

    trace = traces["observation_rollup"] = Trace(case.root)
    trace.help("analysis")
    trace.help("analysis.entry")
    trace.help("analysis.session.members")
    trace.read("members contract", members.contract().show)
    trace.help(members.observe)
    trace.read("observation contract", values.contract().show)
    trace.help(values.rollup)
    logical_total = values.rollup()
    trace.help(logical_total.execute)
    total = logical_total.execute()
    trace.identities[total.state.artifact_ref.ref] = "<artifact>"
    trace.identities[total.state.producing_run_ref] = "<run>"
    trace.read("total result", total.show)
    assert total.to_pandas()["value"].tolist() == [4]

    trace = traces["current_mean"] = Trace(case.root)
    trace.help("analysis")
    methods = trace.help("analysis.methods")
    if "analysis.methods.metric.summary" not in methods:
        trace.help("analysis.methods.metric")
    summary = trace.help("analysis.methods.metric.summary")
    assert "analysis.dsl.LogicalNumericRelation.aggregate" in summary
    trace.help(values.aggregate)
    mean = values.aggregate(mv.mean()).execute()
    trace.identities[mean.state.artifact_ref.ref] = "<artifact>"
    trace.identities[mean.state.producing_run_ref] = "<run>"
    trace.read("mean result", mean.show)
    assert mean.to_pandas()["value"].tolist() == [1.0]

    trace = traces["comparison_ratio"] = Trace(case.root)
    contract = values.contract()
    trace.read("observation contract", contract.show)
    compare_action = next(
        action for action in contract.actions if action.call.startswith("relation.compare(")
    )
    trace.help(compare_action.help_target)
    earlier = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=buyer,
        by=(mv.member(),),
    )
    change = values.compare(earlier)
    trace.help(values.ratio)
    revenue = members.observe(
        ms.ref.metric("sales.revenue"),
        during=august,
        via=buyer,
        by=(mv.member(),),
    )
    quotient = values.ratio(revenue)
    assert isinstance(change, mv.LogicalDifferenceRelation)
    assert isinstance(quotient, mv.LogicalNumericRelation)
    trace.read("ratio contract", quotient.contract().show)

    trace = traces["selected_members"] = Trace(case.root)
    trace.help(values.where)
    selected = values.where(values.value.gt(0))
    trace.read("selection contract", selected.contract().show)
    trace.help(selected.members)
    selected_members = selected.members()
    trace.read("selected members contract", selected_members.contract().show)
    assert isinstance(selected_members, mv.LogicalAnalysisDomain)

    trace = traces["fixed_statistic"] = Trace(case.root)
    fixed = values.execute()
    trace.identities[fixed.state.artifact_ref.ref] = "<artifact>"
    trace.identities[fixed.state.producing_run_ref] = "<run>"
    contract = fixed.contract()
    trace.read("fixed contract", contract.show)
    summary_action = next(
        action for action in contract.actions if action.call.startswith("relation.aggregate(")
    )
    trace.help(summary_action.help_target)
    # Fixed execution must succeed with the source unavailable.
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        fixed_mean = fixed.aggregate(mv.mean()).execute()
        trace.identities[fixed_mean.state.artifact_ref.ref] = "<next-artifact>"
        trace.identities[fixed_mean.state.producing_run_ref] = "<next-run>"
        trace.read("fixed mean result", fixed_mean.show)
        assert fixed_mean.to_pandas()["value"].tolist() == [1.0]
    finally:
        offline.rename(case.database_path)

    trace = traces["spearman"] = Trace(case.root)
    trace.help("analysis")
    trace.help("analysis.methods")
    trace.help("analysis.methods.association")
    trace.help(values.correlate)
    revenue = members.observe(
        ms.ref.metric("sales.revenue"),
        during=august,
        via=buyer,
        by=(mv.member(),),
    )
    association = values.correlate(revenue, method="spearman")
    assert isinstance(association, mv.LogicalAssociationResult)
    trace.read("association contract", association.contract().show)

    trace = traces["missing_subject"] = Trace(case.root)
    share = values.share_of(values.rollup())
    selected_share = share.where(share.value.gt(0.5))
    trace.read("share selection contract", selected_share.contract().show)
    trace.help(selected_share.members)
    with pytest.raises(AnalysisError) as captured:
        selected_share.members()
    trace.read("error", lambda: print(captured.value))
    trace.help(captured.value)
    assert captured.value.repair is not None
    target = captured.value.repair.help_target
    trace.help(target.surface + ("." + target.canonical_id if target.canonical_id else ""))

    forecast_case = analysis_dsl_case_factory("j2")
    prepare_profiles(forecast_case, "KS", "table", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=forecast_case.root)
    orders = forecast_case.session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    daily = (
        orders.observe(ms.ref.metric("sales.total_0"), during=grid, by=(mv.member(),))
        .group_by(grid)
        .rollup()
    )
    trace = traces["drift"] = Trace(forecast_case.root)
    trace.help("analysis")
    trace.help("analysis.methods")
    trace.help("analysis.methods.forecast")
    trace.help(daily.forecast)
    future = daily.forecast(horizon=mv.periods(2), model=mv.drift())
    assert isinstance(future, mv.LogicalForecastResult)
    trace.read("forecast contract", future.contract().show)

    measured = {name: trace.metrics() for name, trace in traces.items()}
    recording = os.environ.get("MARIVO_HELP_BUDGET_RECORD")
    if recording is not None:
        Path(recording).write_text(
            json.dumps(
                {
                    "metrics": measured,
                    "outputs": {name: trace.pages for name, trace in traces.items()},
                    "normalized_outputs": {
                        name: trace.normalized_outputs() for name, trace in traces.items()
                    },
                },
                indent=2,
            )
            + "\n"
        )
        return  # Explicit baseline collection, not optimization acceptance.
    baseline = json.loads(BASELINE.read_text())["metrics"]
    assert set(measured) == set(baseline)
    for name, metrics in measured.items():
        assert metrics["characters"] <= baseline[name]["characters"] * 1.10, (name, metrics)
        assert metrics["pages"] <= baseline[name]["pages"], (name, metrics)
    assert measured["current_mean"]["pages"] < baseline["current_mean"]["pages"]
    assert (
        sum(m["characters"] for m in measured.values())
        <= sum(m["characters"] for m in baseline.values()) * 0.85
    ), measured
