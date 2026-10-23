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


def _links(target: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"marivo\.help\(['\"]([a-z_][a-z0-9_.]*)['\"]\)", render_help_text(target)[0])
    )


def test_global_roots_offer_only_the_owning_task_choices() -> None:
    assert _links("root") == ("authoring", "analysis")
    assert _links("authoring") == ("datasource", "semantic", "ontology")
    assert _links("analysis") == (
        "analysis.inputs",
        "analysis.source",
        "analysis.frame",
        "analysis.execution",
        "analysis.results",
        "analysis.sessions",
    )


@pytest.mark.parametrize(
    "target",
    (
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
    ),
)
def test_retired_task_topics_fail_without_aliases(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        route_help_target(target)


@pytest.mark.parametrize(
    "path",
    (
        ("analysis", "analysis.source", "analysis.population.count"),
        ("analysis", "analysis.inputs", "semantic.expressions", "semantic.metric"),
        ("authoring", "semantic", "semantic.expressions", "semantic.rescale"),
        ("analysis", "analysis.source", "semantic.predicate"),
        ("analysis", "analysis.source", "analysis.analysis_frame.attribute"),
        ("analysis", "analysis.frame", "analysis.session.values"),
        ("analysis", "analysis.frame", "analysis.session.frame"),
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
            "analysis.frame",
            "analysis.frame.statistics",
            "analysis.analysis_frame.correlate",
        ),
        (
            "analysis",
            "analysis.frame",
            "analysis.frame.expressions",
            "analysis.analysis_frame.coord",
        ),
        ("analysis", "analysis.sessions", "analysis.session.persisted_frame"),
        (
            "analysis",
            "analysis.results",
            "analysis.results.evidence",
            "analysis.analysis_frame.persist",
        ),
    ),
)
def test_first_discovery_reaches_a_meaningful_exact_choice_within_four_reads(
    path: tuple[str, ...],
) -> None:
    assert len(path) <= 4
    for current, following in pairwise(path):
        assert following in _links(current), (current, following)
    route = route_help_target(path[-1])
    assert isinstance(route, NativeHelpRoute)
    assert render_help_text(path[-1])[0].startswith(f"Target: {path[-1]}\n")


@pytest.mark.parametrize(
    ("page", "required"),
    (
        ("analysis.inputs", ("semantic.expressions",)),
        (
            "analysis.inputs.catalog",
            ("semantic.catalog_collection.get", "semantic.semantic_catalog.describe", "authoring"),
        ),
        ("analysis.inputs.population", ("analysis.population.count", "semantic.predicate")),
        (
            "analysis.source",
            (
                "semantic.expressions",
                "semantic.predicate",
                "semantic.entity.count",
                "analysis.row_count",
                "analysis.session.population_from",
                "analysis.inputs",
                "analysis.execution",
                "authoring",
            ),
        ),
        ("analysis.frame.compare", ("analysis.inputs.time",)),
        (
            "analysis.frame.statistics",
            ("analysis.population.count", "semantic.entity.count", "analysis.source"),
        ),
        ("analysis.results.evidence", ("analysis.analysis_frame.persist",)),
        ("analysis.sessions", ("analysis.frame", "analysis.source")),
    ),
)
def test_required_secondary_routes_keep_their_real_native_owners(
    page: str,
    required: tuple[str, ...],
) -> None:
    links = _links(page)
    for target in required:
        assert links.count(target) == 1
        route_help_target(target)
    if page in ("analysis.inputs", "analysis.source"):
        assert "analysis.expressions" not in links
        assert "semantic.metric.define" not in links
        assert (
            "without publishing" in render_help_text(page)[0]
            or "no new published Metric" in render_help_text(page)[0]
        )


@pytest.mark.parametrize("page", ("analysis.source", "analysis.frame.statistics"))
def test_count_choices_disclose_distinct_domains_and_completion(page: str) -> None:
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
    assert REGISTRY.by_canonical_id("population.count").discovery_group == "source"
    assert REGISTRY.by_canonical_id("row_count").discovery_group == "frame.statistics"


def test_analysis_pages_are_static_deterministic_and_keep_primary_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Static task Help entered a Session or source")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mv.Session, "catalog", property(forbidden))
    monkeypatch.setattr(mv.session, "create", forbidden)
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
    assert "analysis.column" in _links("analysis.frame.expressions")
    page = render_help_text("analysis.column")[0]
    operators = ("__add__", "__rsub__", "__truediv__", "__and__", "__or__", "__invert__")
    for name in operators:
        target = "analysis.column." + name
        assert target in page
        assert target not in _links("analysis.frame.expressions")
        assert render_help_text(target)[0] == render_help_text(getattr(mv.Column, name))[0]
    assert "independent" in render_help_text("analysis.source")[0].lower()
    assert "source attribute dependencies" in render_help_text("analysis.inputs.population")[0]
