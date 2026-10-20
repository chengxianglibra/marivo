"""Executable disclosure journeys; characters measure context, not model tokens."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd
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
    first = trace.help(mv.Column.mean)
    trace.help("analysis.column.mean")
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


@pytest.mark.slow
def test_task_context_budget(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    metric = session.catalog.metrics.get("sales.order_count")
    august = session.time_scope(start="2026-08-01", end="2026-09-01")
    values = session.observe({"value": metric}, by=mv.product(august, members))
    traces: dict[str, Trace] = {}

    trace = traces["source_overall"] = Trace(case.root)
    trace.help("analysis")
    trace.help("analysis.entry")
    trace.help(session.members)
    trace.read("members metadata", members.show)
    trace.help(session.observe)
    trace.read("observation metadata", values.show)
    overall = session.observe({"value": metric}, by=(august,))
    trace.help(overall.compute)
    total = overall.compute()
    trace.read("total result", total.show)
    assert total.to_pandas()[("value", "value")].tolist() == [4]

    trace = traces["current_mean"] = Trace(case.root)
    trace.help("analysis.methods.metric.summary")
    trace.help(values.reduce)
    trace.help(mv.Column.mean)
    trace.help(mv.AnalysisFrame.compute)
    mean = values.reduce({"value": values["value"].mean()}).compute()
    trace.read("mean result", mean.show)
    assert mean.to_pandas()[("value", "value")].tolist() == [1.0]

    trace = traces["comparison_ratio"] = Trace(case.root)
    trace.read("observation metadata", values.show)
    trace.help(values.compare)
    earlier = session.observe(
        {"value": metric},
        by=mv.product(session.time_scope(start="2026-07-01", end="2026-08-01"), members),
    )
    change = values.compare(
        earlier, on=tuple(zip(values.axes, earlier.axes, strict=True)), match=mv.ordinal()
    )
    assert isinstance(change, mv.AnalysisFrame)
    assert change.output_names == ("left.value", "right.value", "absolute.value", "relative.value")
    amounts = session.observe(
        {"count": metric, "revenue": session.catalog.metrics.get("sales.revenue")},
        by=mv.product(august, members),
    )
    trace.help(mv.Column.divide)
    quotient = amounts.select({"value": amounts["count"] / amounts["revenue"]})
    trace.read("ratio metadata", quotient.show)

    trace = traces["selected_members"] = Trace(case.root)
    trace.help(values.where)
    selected = values.where(values["value"] > 0)
    trace.read("selection metadata", selected.show)
    trace.help(session.population_from)
    entity = session.catalog.entities.get("sales.customer")
    selected_members = session.population_from(selected, on=((selected.axes[1], entity),))
    trace.read("selected members metadata", selected_members.show)
    assert isinstance(selected_members, mv.Population)

    trace = traces["spearman"] = Trace(case.root)
    trace.help("analysis.analysis_frame.to_pandas")
    trace.help("analysis.analysis_frame.select")
    trace.help(mv.AnalysisFrame.compute)
    exported = amounts.compute().to_pandas()
    association = exported[("value", "count")].corr(
        exported[("value", "revenue")], method="spearman"
    )
    trace.read("pandas association", lambda: print(association))
    # All four August counts equal1; represented Spearman is undefined.
    assert pd.isna(association)

    trace = traces["missing_subject"] = Trace(case.root)
    scalar = values.reduce({"value": values["value"].sum()})
    trace.read("scalar metadata", scalar.show)
    trace.help(session.population_from)
    with pytest.raises(AnalysisError) as captured:
        session.population_from(scalar, on=((values.axes[0], entity),))
    trace.read("error", lambda: print(captured.value))
    trace.help(type(captured.value))
    assert captured.value.repair is not None
    target = captured.value.repair.help_target
    trace.help(target.surface + ("." + target.canonical_id if target.canonical_id else ""))

    prepare_profiles(case, "KS", "table", "us", "UTC", False, followup=True, profile_indices=(0,))
    ms.load(workspace_dir=case.root)
    forecast_session = mv.session.create("context-budget-forecast", timezone="UTC")
    daily = forecast_session.observe(
        {"value": forecast_session.catalog.metrics.get("sales.total_0")},
        by=(forecast_session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day")),),
    )
    trace = traces["drift"] = Trace(case.root)
    trace.help("analysis.session.observe")
    trace.help("analysis.analysis_frame.to_pandas")
    from tests.analysis.statistics.pandas_workflows import forecast

    trace.help(mv.AnalysisFrame.compute)
    future = forecast(daily.compute().to_pandas()[("value", "value")], model="drift")
    trace.read("pandas forecast", lambda: print(future.to_string(index=False)))
    assert future["prediction"].tolist() == [10.0, 13.0]

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
    reference = json.loads(BASELINE.read_text())["budget_reference"]
    # Compare the explicit overall source construction to the historical overall
    # journey; this test mapping is not a retired public API alias.
    assert set(measured) == (set(reference) - {"observation_rollup"}) | {"source_overall"}
    for name, metrics in measured.items():
        previous = "observation_rollup" if name == "source_overall" else name
        limits = reference[previous]
        assert metrics["characters"] <= limits["characters"] * 1.10, (name, metrics)
        assert metrics["pages"] <= limits["pages"], (name, metrics)
        assert metrics["repeated_pages"] <= limits["repeated_pages"], (name, metrics)
    assert measured["current_mean"]["pages"] < reference["current_mean"]["pages"]
    assert (
        sum(m["characters"] for m in measured.values())
        <= sum(m["characters"] for m in reference.values()) * 0.85
    ), measured


@pytest.mark.slow
def test_attribute_context_budget(analysis_dsl_case_factory: DslCaseFactory) -> None:
    """Charge the new source-to-facts journey separately from historical savings."""
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    august = session.time_scope(start="2026-08-01", end="2026-09-01")
    july = session.time_scope(start="2026-07-01", end="2026-08-01")
    trace = Trace(case.root)
    for target in (
        "analysis",
        "analysis.methods",
        "analysis.methods.attribute",
        "analysis.session.attribute",
        "analysis.attribute_endpoint",
    ):
        trace.help(target)
    request = session.attribute(
        mv.AttributeEndpoint({"revenue": revenue}, during=august),
        mv.AttributeEndpoint({"revenue": revenue}, during=july),
        by=(region,),
        contribution=True,
    )
    trace.read("attribute captured request", lambda: request.show(n=0))
    fixed = request.compute()
    trace.read("attribute fixed values", fixed.show)
    trace.help("analysis.analysis_frame.method_facts")
    trace.help("analysis.frame_method_facts")
    facts = fixed.method_facts
    trace.identities[facts[0].origin_result_id] = "<attribute-origin>"
    trace.help("analysis.frame_method_facts.show")
    trace.read("original explanation facts", lambda: facts[0].show(n=3))
    assert fixed.to_pandas()[("value", "contribution.revenue")].tolist() == [-20, -50, 0]
    assert facts[0].parents[0].metrics[0].method.change.value == -70
    measured = trace.metrics()
    recording = os.environ.get("MARIVO_ATTRIBUTE_HELP_BUDGET_RECORD")
    if recording is not None:
        Path(recording).write_text(
            json.dumps(
                {
                    "metrics": measured,
                    "outputs": trace.pages,
                    "normalized_outputs": trace.normalized_outputs(),
                },
                indent=2,
            )
            + "\n"
        )
        return  # Raw collection is not budget acceptance.
    limits = json.loads(BASELINE.read_text())["attribute_budget_reference"]
    assert measured["characters"] <= limits["characters"], measured
    assert measured["pages"] <= limits["pages"], measured
    assert measured["repeated_pages"] <= limits["repeated_pages"], measured
