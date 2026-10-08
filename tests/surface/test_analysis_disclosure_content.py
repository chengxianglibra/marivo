"""Independent semantic, example and family-projection disclosure regressions."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput, TypeInput
from marivo.analysis._capabilities.dataset_registry import assemble, prepare
from marivo.analysis.datasets.errors import DatasetRegistrationError
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory


def test_runtime_aggregate_help_discloses_static_backend_check() -> None:
    text, _, _ = render_help_text("analysis.runtime_metric.aggregate")
    callable_text, _, _ = render_help_text(mv.runtime_metric.aggregate)
    assert text == callable_text
    assert "count_distinct, median and percentile require exact operations" in text
    assert "approx_* explicitly permits approximation" in text
    assert "catalog.readiness() blocks known backend incompatibilities without connecting" in text
    assert "execution never substitutes the definition" in text


def test_parameter_semantics_and_real_producers() -> None:
    text, _, _ = render_help_text(mv.LogicalNumericRelation.correlate)
    assert "Input method: pearson, spearman, or kendall." in text
    assert "mv.sum" not in text
    text, _, _ = render_help_text(mv.LogicalNumericRelation.summarize)
    assert "result = relation.summarize(mv.mean())" in text
    assert "Example inputs: relation" in text
    assert "follow structured repair" not in text
    text, _, _ = render_help_text(mv.LogicalAnalysisDomain.observe)
    assert "business-complete scopes with aware datetime bounds" in text
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for producer in descriptor.producers:
                assert render_help_text("analysis." + producer)[0]
            if descriptor.canonical_id != "LogicalAnalysisDomain":
                assert "session.members" not in descriptor.producers, descriptor.canonical_id
    for target, producer in (
        ("LogicalNumericRelation", "dsl.LogicalAnalysisDomain.observe"),
        ("LogicalStatisticRelation", "dsl.LogicalNumericRelation.summarize"),
        ("LogicalSelectedNumericRelation", "dsl.LogicalNumericRelation.where"),
    ):
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, TypeInput) and producer in descriptor.producers
        callable_descriptor = registry.by_canonical_id(producer)
        assert isinstance(callable_descriptor, CallableInput)
        assert target in str(
            callable_descriptor.bindings[0].implementation.__annotations__["return"]
        )
    for target, producer in (
        ("dsl.NumericField", "LogicalNumericRelation"),
        ("dsl.CategoryField", "LogicalCategoryRelation"),
        ("dsl.BooleanField", "LogicalBooleanRelation"),
        ("dsl.TemporalField", "LogicalTemporalRelation"),
    ):
        descriptor = registry.by_canonical_id(target)
        owner = registry.by_canonical_id(producer)
        assert isinstance(descriptor, TypeInput) and descriptor.producers == (producer,)
        assert isinstance(owner, TypeInput)
        assert any(
            field.name == "value" and target.removeprefix("dsl.") in field.annotation
            for binding in owner.bindings
            for field in binding.fields
        )


def test_family_projection_preserves_every_canonical_member() -> None:
    registry = prepare()
    families: dict[str, list[str]] = {}
    for descriptor in registry.descriptors:
        if isinstance(descriptor, CallableInput) and descriptor.discovery_family:
            families.setdefault(descriptor.discovery_family, []).append(descriptor.canonical_id)
    assert families
    assert {family: set(members) for family, members in families.items()} == {
        "dsl.LogicalNumericRelation.summarize": {
            "dsl.OriginalContinuation.summarize",
            "dsl.LogicalNumericRelation.summarize",
            "dsl.MaterializedNumericRelation.summarize",
            "dsl.LogicalRatioRelation.summarize",
        },
        "dsl.LogicalRatioRelation.rollup": {
            "dsl.LogicalRatioRelation.rollup",
            "dsl.MaterializedRatioRelation.rollup",
        },
    }
    # Correlation receivers already share one native method, not separate variants.
    for receiver in (mv.LogicalNumericRelation, mv.MaterializedNumericRelation):
        assert (
            registry.by_callable(receiver.correlate).canonical_id
            == "dsl.NumericComparison.correlate"
        )
    for family, members in families.items():
        descriptor = registry.by_canonical_id(family)
        assert isinstance(descriptor, CallableInput) and descriptor.discovery_group
        text, _, _ = render_help_text("analysis." + descriptor.discovery_group)
        targets = re.findall(r"marivo.help\('analysis.([^']+)'\)", text)
        assert all(targets.count(member) == 1 for member in members)
        assert text.count(descriptor.summary) == 1


@pytest.mark.parametrize("fault", ("input", "guidance", "family", "missing_family", "syntax"))
def test_registration_rejects_incomplete_example_or_foreign_family(fault: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, provider = next((i, p) for i, p in enumerate(providers) if p.owner == "runtime")
    descriptors = list(provider.descriptors)
    position, descriptor = next(
        (i, d)
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "dsl.LogicalNumericRelation.summarize"
    )
    if fault == "input":
        broken = replace(descriptor, example=replace(descriptor.example, requires=()))
    elif fault == "guidance":
        broken = replace(
            descriptor, parameters=(replace(descriptor.parameters[0], acquisition=""),)
        )
    elif fault == "family":
        broken = replace(descriptor, discovery_family="dsl.NumericComparison.correlate")
    elif fault == "missing_family":
        broken = replace(descriptor, discovery_family="dsl.Missing.summarize")
    else:
        broken = replace(descriptor, example=replace(descriptor.example, code="result = ("))
    descriptors[position] = broken
    providers[index] = replace(provider, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("observe", "where", "summarize", "rank", "correlate", "ratio"))
def test_rendered_focused_examples_execute(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    if method == "correlate":
        prepare_profiles(case, "KS", "table", "us", "UTC", False)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    buyer = ms.ref.relationship("sales.order_buyer")
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
    values = members.observe(ms.ref.metric("sales.order_count"), during=scope, via=buyer)
    revenue = members.observe(ms.ref.metric("sales.revenue"), during=scope, via=buyer)
    if method == "correlate":
        order_members = case.session.members(ms.ref.entity("sales.order"))
        revenue = order_members.read(ms.ref.measure("sales.order.profile_0"))
        values = order_members.read(ms.ref.measure("sales.order.profile_1"))
    receiver = members if method == "observe" else values
    text, _, _ = render_help_text(getattr(receiver, method))
    code = text.split("\nExample:\n", 1)[1].split("\nSee:", 1)[0].split("\nExpected:", 1)[0]
    namespace: dict[str, object] = {
        "mv": mv,
        "relation": receiver,
        "values": values,
        "revenue": revenue,
        "orders": values,
        "other": revenue,
        "metric": ms.ref.metric("sales.order_count"),
        "buyer": buyer,
        "current": values,
        "reference": revenue,
    }
    exec(compile(code, "focused-help-example", "exec"), namespace)
    result = namespace[
        "selected"
        if method == "where"
        else "ranking"
        if method == "rank"
        else "quotient"
        if method == "ratio"
        else "result"
    ]
    assert isinstance(
        result,
        (
            mv.LogicalNumericRelation,
            mv.LogicalSelectedNumericRelation,
            mv.LogicalStatisticRelation,
            mv.LogicalRankingResult,
            mv.LogicalAssociationResult,
        ),
    )
    saved = result.execute()
    assert saved.state.realized_row_count > 0
    assert saved.contract().retained_parts


@pytest.mark.runtime
def test_rendered_forecast_example_executes(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "table", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    daily = (
        members.each(grid)
        .observe(ms.ref.metric("sales.total_0"), during=grid.window)
        .group_by(grid)
        .rollup()
    )
    text, _, _ = render_help_text(daily.forecast)
    code = text.split("\nExample:\n", 1)[1].split("\nSee:", 1)[0]
    namespace: dict[str, object] = {"daily": daily, "mv": mv}
    exec(compile(code, "forecast-help-example", "exec"), namespace)
    future = namespace["future"]
    assert isinstance(future, mv.LogicalForecastResult)
    saved = future.execute()
    assert saved.state.realized_row_count == 4
    assert saved.prediction.to_pandas()["value"].tolist() == [10.0, 13.0, 16.0, 19.0]
