"""First-round public Help routes and examples use their native owners."""

from __future__ import annotations

import ast
import inspect
import re
from collections import deque
from itertools import pairwise

import pytest

import marivo.analysis as mv
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis._capabilities.dataset_model import CallableInput, NavigationInput
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis._capabilities.registry import REGISTRY
from tests.shared_fixtures import rendered_help


def test_metric_intent_reaches_observation_without_restarting_discovery() -> None:
    def targets(page: str) -> tuple[str, ...]:
        return tuple(re.findall(r"marivo\.help\(['\"]([^'\"]+)['\"]\)", page))

    path = ("analysis", "analysis.methods", "analysis.methods.metric", "analysis.session.observe")
    for current, following in pairwise(path):
        assert following in targets(rendered_help(current))
    assert "analysis.session.observe" in targets(rendered_help("analysis.entry"))
    descriptor = REGISTRY.by_canonical_id("session.observe")
    assert isinstance(descriptor, CallableInput)
    assert descriptor.discovery_group == "methods.metric"
    # Discovery cross-links do not create additional capability owners.
    owners = [
        d.canonical_id
        for d in REGISTRY.descriptors
        if isinstance(d, NavigationInput) and "session.observe" in d.members
    ]
    assert owners == ["methods.metric"]
    assert rendered_help(mv.Session.observe) == rendered_help(path[-1])


def test_rendered_callable_examples_have_no_duplicate_imports() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, CallableInput):
            continue
        page = rendered_help("analysis." + descriptor.canonical_id)
        code = page.split("Example:\n", 1)[1].split("\nExpected:", 1)[0].split("\nSee:", 1)[0]
        tree = ast.parse(code)
        imports = [
            ast.dump(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert len(imports) == len(set(imports)), descriptor.canonical_id


def test_grouping_is_the_public_observation_family() -> None:
    annotation = str(inspect.signature(mv.Session.observe).parameters["by"].annotation)
    assert "Grouping" in annotation and "Population" in annotation
    for name in ("member", "MemberAxis", "MembershipAxis"):
        assert not hasattr(mv, name)
        assert name not in mv.__all__
    with pytest.raises(TypeError):
        mv.Grouping()


@pytest.mark.parametrize(
    ("target", "required"),
    (
        ("coordinates", ("field or membership Grouping", "identity and calendar", "mv.identities")),
        ("membership", ("resolved fields", "one member Entity")),
        ("population.project", ("existing coordinates", "receiver", "membership")),
        ("population.classify", ("same-Entity", "existing identity", "calendar", "routed fields")),
        ("session.values", ("field or identity Grouping", "calendar and membership")),
        (
            "session.observe",
            ("field, identity, calendar or membership", "population.axes", "by=population"),
        ),
    ),
)
def test_grouping_help_discloses_each_consumers_admitted_inputs(
    target: str, required: tuple[str, ...]
) -> None:
    page = rendered_help("analysis." + target)
    by = next(line for line in page.splitlines() if line.startswith("Input by:"))
    for text in required:
        assert text in by


def test_time_binding_has_one_public_entry_per_operation() -> None:
    assert not hasattr(mv, "time_grid")
    assert not hasattr(mv.Population, "observe")
    assert not hasattr(mv, "time_scope")
    assert not hasattr(mv, "instant")
    assert callable(mv.Session.time_scope) and callable(mv.Session.instant)
    for name in (
        "GridWindow",
        "TimeGrid",
        "LogicalTimeAnalysisDomain",
        "MaterializedTimeAnalysisDomain",
    ):
        assert not hasattr(mv, name) and name not in REGISTRY.canonical_ids()
    annotation = str(inspect.signature(mv.Session.observe).parameters["during"].annotation)
    assert "TimeScope" in annotation and "CalendarAxis" in annotation
    assert tuple(inspect.signature(mv.Session.read).parameters) == ("self", "population", "fields")


def _free_names(code: str) -> set[str]:
    tree = ast.parse(code)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assigned = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    return loaded - assigned - imported - {"mv", "ms"}


def test_first_round_help_targets_are_reachable_and_bounded() -> None:
    seen = {""}
    pending = deque([""])
    while pending:
        page = render(REGISTRY, pending.popleft())
        for target in re.findall(r"marivo\.help\(['\"]analysis\.([^'\"]+)['\"]\)", page):
            assert target in REGISTRY.canonical_ids()
            if target not in seen:
                seen.add(target)
                pending.append(target)

    first_round = {
        target
        for target in REGISTRY.canonical_ids()
        if target.startswith(("analysis_frame.", "frame_axis.", "population.", "column."))
    }
    assert first_round <= seen
    assert "session.members" in seen
    for target in (*sorted(first_round), "session.members"):
        route = route_help_target("analysis." + target)
        assert isinstance(route, NativeHelpRoute)
        page = render(REGISTRY, target)
        descriptor = REGISTRY.by_canonical_id(target)
        if isinstance(descriptor, CallableInput):
            assert _free_names(descriptor.example.code) <= set(descriptor.example.requires)


def test_grouping_surface_stays_with_finite_query_operations() -> None:
    assert not hasattr(mv, "GroupedAnalysisDomain")
    for owner in (mv.Population, mv.AnalysisFrame, mv.Artifact):
        assert not hasattr(owner, "group_by")
        assert not hasattr(owner, "aggregate")
    for query_owner, method in (
        (mv.AnalysisFrame, "reduce"),
        (mv.Session, "observe"),
    ):
        assert "groups" not in inspect.signature(getattr(query_owner, method)).parameters
    assert not any("GroupedAnalysisDomain" in target for target in REGISTRY.canonical_ids())


def test_current_row_aggregation_has_one_public_entry() -> None:
    for function, target in (
        (mv.AnalysisFrame.reduce, "analysis_frame.reduce"),
        (mv.Column.sum, "column.sum"),
        (mv.Column.mean, "column.mean"),
        (mv.row_count, "row_count"),
    ):
        route = route_help_target(function)
        assert isinstance(route, NativeHelpRoute) and route.owner == "analysis"
        assert route.resolved.canonical_id == target
    for alias in ("aggregate", "summarize", "agg"):
        assert not hasattr(mv.AnalysisFrame, alias) and not hasattr(mv.Artifact, alias)
    assert not any(target.endswith(".aggregate") for target in REGISTRY.canonical_ids())


def test_population_bridge_help_uses_actual_static_method_without_catalog_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_session: object) -> object:
        raise AssertionError("Static Help read a Catalog")

    monkeypatch.setattr(mv.Session, "catalog", property(forbidden))
    page = rendered_help("analysis.session.population_from")
    assert page == rendered_help(mv.Session.population_from)
    assert "Target: analysis.session.population_from\nKind: callable" in page
    assert "on:" in page and "FrameAxis" in page and "Entity" in page
    assert "Required immutable tuple" in page
    assert "construction reads no rows" in page
    assert "analysis.session.population_from" in rendered_help("analysis.population_tasks")
    assert "analysis.session.population_from" in rendered_help("analysis.population")
