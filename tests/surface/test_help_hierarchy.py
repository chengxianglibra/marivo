"""Task discovery preserves exact owners, count meanings and recovery boundaries."""

from __future__ import annotations

import re
from collections import Counter
from itertools import pairwise
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError, NativeHelpRoute
from marivo._help.render import render_help_text
from marivo._help.route import route_help_target
from marivo.analysis._capabilities.dataset_model import CallableInput, NavigationInput
from marivo.analysis._capabilities.model import ANALYSIS_HELP_RENDER_BUDGETS
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory


def _links(target: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"marivo\.help\(['\"]([a-z_][a-z0-9_.]*)['\"]\)", render_help_text(target)[0])
    )


def test_global_roots_offer_only_the_owning_task_choices() -> None:
    assert _links("root") == ("authoring", "analysis")
    assert _links("authoring") == ("datasource", "semantic", "ontology")
    assert _links("analysis") == (
        "analysis.inputs",
        "analysis.operators",
        "analysis.sessions",
        "analysis.execution",
        "analysis.results",
        "analysis.session.get_or_create",
    )


def test_retired_task_topics_fail_without_aliases() -> None:
    for target in (
        "datasource.authoring",
        "semantic.authoring",
        "semantic.builders",
        "semantic.builders.entity_history",
        "semantic.builders.field_metric_support",
        "semantic.builders.governed_temporal",
        "semantic.builders.relationship",
        "semantic.builders.temporal_parsing",
        "semantic.checks",
        "semantic.objects",
        "analysis.artifacts",
        "analysis.artifacts.reads",
        "analysis.catalog",
        "analysis.catalog.temporal",
        "analysis.entry",
        "analysis.evidence",
        "analysis.methods",
        "analysis.methods.attribute",
        "analysis.methods.compare",
        "analysis.methods.metric",
        "analysis.methods.metric.summary",
        "analysis.methods.rows",
        "analysis.methods.rows.expressions",
        "analysis.population_tasks",
        "analysis.runtime",
        "analysis.runtime.sessions",
        "analysis.runtime.values",
        "analysis.session.namespace",
        "analysis.windows",
        "analysis.source",
        "analysis.frame",
        "analysis.frame.expressions",
        "analysis.frame.rows",
        "analysis.frame.statistics",
        "analysis.frame.compare",
    ):
        with pytest.raises(MarivoHelpTargetError):
            route_help_target(target)


def test_first_discovery_reaches_a_meaningful_exact_choice_within_four_reads() -> None:
    for path in (
        ("analysis", "analysis.operators", "analysis.population.count"),
        ("analysis", "analysis.inputs", "semantic.expressions", "semantic.metric"),
        ("authoring", "semantic", "semantic.expressions", "semantic.rescale"),
        ("analysis", "analysis.operators", "analysis.session.observe", "semantic.predicate"),
        ("analysis", "analysis.operators", "analysis.analysis_frame.attribute"),
        ("analysis", "analysis.operators", "analysis.session.values"),
        ("analysis", "analysis.operators", "analysis.session.frame"),
        (
            "analysis",
            "analysis.inputs",
            "analysis.inputs.population",
            "analysis.population.classify",
        ),
        (
            "analysis",
            "analysis.inputs",
            "analysis.inputs.population",
            "analysis.session.population_from",
        ),
        (
            "analysis",
            "analysis.operators",
            "analysis.analysis_frame.correlate",
        ),
        (
            "analysis",
            "analysis.inputs",
            "analysis.inputs.frame",
            "analysis.analysis_frame.coord",
        ),
        ("analysis", "analysis.sessions", "analysis.session.persisted_frame"),
        (
            "analysis",
            "analysis.results",
            "analysis.results.evidence",
            "analysis.analysis_frame.persist",
        ),
    ):
        assert len(path) <= 4
        for current, following in pairwise(path):
            assert following in _links(current), (current, following)
        route = route_help_target(path[-1])
        assert isinstance(route, NativeHelpRoute)
        assert render_help_text(path[-1])[0].startswith(f"Target: {path[-1]}\n")


def test_required_secondary_routes_keep_their_real_native_owners() -> None:
    for page, required in (
        ("analysis.inputs", ("semantic.expressions",)),
        (
            "analysis.inputs.catalog",
            (
                "semantic.semantic_catalog.search",
                "semantic.semantic_catalog.get",
                "semantic.readiness",
                "authoring",
            ),
        ),
        ("analysis.inputs.population", ("analysis.population.count", "semantic.predicate")),
        (
            "analysis.operators",
            ("semantic.entity.count", "analysis.row_count", "analysis.session.observe"),
        ),
        (
            "analysis.inputs.comparison",
            ("analysis.baseline", "analysis.offset", "analysis.ordinal"),
        ),
        (
            "analysis.inputs.frame",
            ("analysis.column", "analysis.frame_axis", "analysis.value_statistic"),
        ),
        ("analysis.results.evidence", ("analysis.analysis_frame.persist",)),
        ("analysis.sessions", ("analysis.operators",)),
    ):
        links = _links(page)
        for target in required:
            assert links.count(target) == 1
            route_help_target(target)
        if page == "analysis.inputs":
            assert "analysis.expressions" not in links
            assert "semantic.metric.define" not in links
            assert (
                "without publishing" in render_help_text(page)[0]
                or "no new published Metric" in render_help_text(page)[0]
            )


def test_count_choices_disclose_distinct_domains_and_completion() -> None:
    page = "analysis.operators"
    links = _links(page)
    for target in ("analysis.population.count", "semantic.entity.count", "analysis.row_count"):
        assert target in links
    text = render_help_text(page)[0]
    for fact in (
        "complete Population tuples",
        "Entity source contributions",
        "current Frame rows",
        "including value NA",
        "Metrics for observe",
    ):
        assert fact in text
    assert "reduce/over" in text
    population_count = REGISTRY.by_canonical_id("population.count")
    row_count = REGISTRY.by_canonical_id("row_count")
    assert isinstance(population_count, CallableInput)
    assert isinstance(row_count, CallableInput)
    assert population_count.discovery_group == "operators"
    assert row_count.discovery_group == "operators"


def test_analysis_pages_are_static_deterministic_and_keep_primary_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Static task Help entered a Session or source")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mv.Session, "catalog", property(forbidden))
    monkeypatch.setattr(mv.session, "get_or_create", forbidden)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    primary: Counter[str] = Counter()
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, NavigationInput):
            continue
        target = "analysis" + ("." + descriptor.canonical_id if descriptor.canonical_id else "")
        text = render_help_text(target)[0]
        assert text == render_help_text(target)[0]
        budget = ANALYSIS_HELP_RENDER_BUDGETS[descriptor.render_class]
        assert len(_links(target)) <= budget.max_outgoing_routes
        # The coordinator adds three header facts and a separating blank line.
        assert len(text.splitlines()) <= budget.max_lines + 4
        assert len(text) <= budget.max_codepoints + 100
        for linked in _links(target):
            route_help_target(linked)
        primary.update(descriptor.members)
    assert all(count == 1 for count in primary.values())
    for descriptor in REGISTRY.descriptors:
        if isinstance(descriptor, CallableInput) and descriptor.discovery_group:
            assert primary[descriptor.canonical_id] == 1
    assert not (tmp_path / ".marivo").exists()


def test_column_operator_reference_remains_discoverable_without_flattening_task_page() -> None:
    assert "analysis.column" in _links("analysis.inputs.frame")
    page = render_help_text("analysis.column")[0]
    operators = ("__add__", "__rsub__", "__truediv__", "__and__", "__or__", "__invert__")
    for name in operators:
        target = "analysis.column." + name
        assert target in page
        assert target not in _links("analysis.inputs.frame")
        assert render_help_text(target)[0] == render_help_text(getattr(mv.Column, name))[0]
    assert "independent" in render_help_text("analysis.operators")[0].lower()
    assert "source attribute dependencies" in render_help_text("analysis.inputs.population")[0]


@pytest.mark.parametrize(
    ("target", "parameter", "producers"),
    (
        ("session.observe", "metrics", ("analysis.inputs.catalog", "semantic.expressions")),
        (
            "session.observe",
            "by",
            ("analysis.inputs.catalog", "analysis.session.time_scope", "analysis.time_scope.by"),
        ),
        ("session.observe", "where", ("semantic.predicate",)),
        ("time_scope.by", "grain", ("analysis.grain.define",)),
        ("analysis_frame.reduce", "values", ("analysis.value_statistic", "analysis.column.sum")),
        ("analysis_frame.reduce", "by", ("analysis.analysis_frame.axis",)),
        (
            "analysis_frame.compare",
            "baselines",
            ("analysis.baseline", "analysis.session.persisted_frame"),
        ),
        ("analysis_frame.compare", "on", ("analysis.analysis_frame.axes",)),
        ("analysis_frame.compare", "match", ("analysis.inputs.comparison",)),
        ("baseline", "match", ("analysis.inputs.comparison",)),
        ("analysis_frame.where", "predicate", ("analysis.column",)),
    ),
)
def test_parameter_acquisition_is_adjacent_and_independently_resolvable(
    target: str, parameter: str, producers: tuple[str, ...]
) -> None:
    page = render_help_text("analysis." + target)[0]
    section = page.split("Input " + parameter + ": ", 1)[1]
    acquisition = section.split("\nInput ", 1)[0].split("\nConstraint:", 1)[0]
    for producer in producers:
        link = "marivo.help('" + producer + "')"
        assert "Acquire " + parameter + ": " + link in acquisition
        assert "See: " + link not in page
        route_help_target(producer)


def test_discovery_and_parameter_links_support_a_fresh_question(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    analysis_dsl_case_factory("j2")
    for current, following in (
        ("analysis", "analysis.session.get_or_create"),
        ("analysis.inputs", "analysis.inputs.catalog"),
        ("analysis.inputs.catalog", "semantic.semantic_catalog.search"),
        ("analysis.inputs.catalog", "semantic.semantic_catalog.get"),
        ("analysis.operators", "analysis.session.observe"),
        ("analysis.session.observe", "analysis.session.time_scope"),
        ("analysis.session.observe", "analysis.analysis_frame.compute"),
        ("analysis.analysis_frame.reduce", "analysis.column.sum"),
        ("analysis.analysis_frame.compare", "analysis.baseline"),
    ):
        assert following in _links(current)

    # No preselected Session, closed Metric or time input is handed to this question.
    session = mv.session.get_or_create("help-fresh-question", timezone="UTC")
    candidates = session.catalog.search("revenue", kind="metric", limit=5)
    candidate = next(item for item in candidates.items if item.path == "sales.revenue")
    entry = session.catalog.get(candidate.id)
    assert entry.kind == "metric"
    august = session.time_scope(start="2026-08-01", end="2026-09-01")
    fixed = session.observe({"value": entry.value}, by=(august,)).compute()
    assert fixed.to_pandas()["value"].tolist() == [180]

    summary = fixed.reduce({"total": fixed["value"].sum()}).compute()
    assert summary.to_pandas()["total"].tolist() == [180]
    previous = fixed.select({"value": fixed["value"] / 2})
    comparison = fixed.compare({"reference": mv.baseline(previous)}).compute()
    data = comparison.to_pandas()
    assert data["reference.absolute.value"].tolist() == [90]
    assert data["reference.relative.value"].tolist() == [1]
