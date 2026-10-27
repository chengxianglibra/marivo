"""First-round public Help routes and examples use their native owners."""

from __future__ import annotations

import ast
import builtins
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

    path = ("analysis", "analysis.operators", "analysis.session.observe")
    for current, following in pairwise(path):
        assert following in targets(rendered_help(current))
    assert "analysis.session.observe" in targets(rendered_help("analysis.operators"))
    descriptor = REGISTRY.by_canonical_id("session.observe")
    assert isinstance(descriptor, CallableInput)
    assert descriptor.discovery_group == "operators"
    # Discovery cross-links do not create additional capability owners.
    owners = [
        d.canonical_id
        for d in REGISTRY.descriptors
        if isinstance(d, NavigationInput) and "session.observe" in d.members
    ]
    assert owners == ["operators"]
    assert rendered_help(mv.Session.observe) == rendered_help(path[-1])


def test_execution_help_links_retention_and_exact_recovery_without_new_owners() -> None:
    routes = {
        "analysis.execution": (
            "analysis.analysis_frame.compute",
            "analysis.analysis_frame.persist",
            "analysis.session.persisted_frame",
        ),
        "analysis.analysis_frame.compute": ("analysis.analysis_frame.persist",),
        "analysis.analysis_frame.persist": (
            "analysis.analysis_frame.session_id",
            "analysis.analysis_frame.artifact_id",
            "analysis.session.persisted_frame",
        ),
        "analysis.session.persisted_frame": ("analysis.session.resume",),
    }
    for target, expected in routes.items():
        links = re.findall(r"marivo\.help\(['\"]([^'\"]+)['\"]\)", rendered_help(target))
        assert set(expected) <= set(links), target
        for link in expected:
            assert rendered_help(link).startswith(f"Target: {link}\n")
    descriptor = REGISTRY.by_canonical_id("session.persisted_frame")
    assert isinstance(descriptor, CallableInput)
    assert descriptor.discovery_group == "sessions"
    assert [
        d.canonical_id
        for d in REGISTRY.descriptors
        if isinstance(d, NavigationInput) and "session.persisted_frame" in d.members
    ] == ["sessions"]


def test_attribute_intent_and_complete_fact_navigation_have_exact_owners() -> None:
    path = (
        "analysis",
        "analysis.operators",
        "analysis.analysis_frame.attribute",
    )
    for current, following in pairwise(path):
        assert following in rendered_help(current)
    descriptor = REGISTRY.by_canonical_id("analysis_frame.attribute")
    assert isinstance(descriptor, CallableInput) and descriptor.discovery_group == "operators"
    owners = [
        value.canonical_id
        for value in REGISTRY.descriptors
        if isinstance(value, NavigationInput) and "analysis_frame.attribute" in value.members
    ]
    assert owners == ["operators"]
    assert "analysis.analysis_frame.method_facts" in rendered_help("analysis.analysis_frame")
    assert "analysis.frame_method_facts" in rendered_help("analysis.analysis_frame.method_facts")
    assert "analysis.frame_method_parent" in rendered_help("analysis.frame_method_facts")
    assert "analysis.frame_attribute_metric" in rendered_help("analysis.frame_method_parent")
    assert "analysis.frame_ratio_method" in rendered_help("analysis.frame_attribute_metric")
    assert "analysis.frame_method_value" in rendered_help("analysis.frame_ratio_method")


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
    assert inspect.isabstract(mv.Grouping)


@pytest.mark.parametrize(
    ("target", "required"),
    (
        (
            "coordinates",
            ("field or identity Grouping", "complete primary-key tuple", "Rejects membership"),
        ),
        ("membership", ("resolved fields", "one member Entity")),
        ("population.project", ("existing coordinates", "population.axes", "observe.by")),
        ("population.classify", ("same-Entity", "existing identity", "calendar", "routed fields")),
        ("session.values", ("field or identity Grouping", "calendar and membership")),
        (
            "session.observe",
            (
                "fields/Grouping/TimeScope/memberships",
                "population.axes",
                "P fixes joint keys",
            ),
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
    annotation = str(inspect.signature(mv.Session.observe).parameters["by"].annotation)
    assert "TimeScope" in annotation and "Grouping" in annotation
    assert "during" not in inspect.signature(mv.Session.observe).parameters
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
    return loaded - assigned - imported - {"mv", "ms"} - set(vars(builtins))


def test_first_round_help_targets_are_reachable_and_bounded() -> None:
    seen = {""}
    pending = deque([""])
    while pending:
        current = pending.popleft()
        page = (
            render(REGISTRY, current)
            if current in REGISTRY.canonical_ids()
            else rendered_help("analysis." + current)
        )
        for match in re.findall(
            r"marivo\.help\(['\"]analysis\.([^'\"]+)['\"]\)|^  analysis\.([a-z_][a-z0-9_.]*)",
            page,
            re.MULTILINE,
        ):
            target = match[0] or match[1]
            text = rendered_help("analysis." + target)
            if target not in REGISTRY.canonical_ids():
                assert text.startswith(f"Target: analysis.{target}\nKind: attribute")
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
    assert "analysis_frame.attribute" in seen and "frame_method_facts" in seen
    assert "frame_method_facts.show" in seen
    for target in (*sorted(first_round), "session.members"):
        route = route_help_target("analysis." + target)
        assert isinstance(route, NativeHelpRoute)
        page = render(REGISTRY, target)
        descriptor = REGISTRY.by_canonical_id(target)
        if isinstance(descriptor, CallableInput):
            assert _free_names(descriptor.example.code) <= set(descriptor.example.requires)


def test_grouping_surface_stays_with_finite_query_operations() -> None:
    assert not hasattr(mv, "GroupedAnalysisDomain")
    for owner in (mv.Population, mv.AnalysisFrame):
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
        (mv.AnalysisFrame.over, "analysis_frame.over"),
        (mv.AnalysisFrame.correlate, "analysis_frame.correlate"),
        (mv.Column.sum, "column.sum"),
        (mv.Column.mean, "column.mean"),
        (mv.Column.corr, "column.corr"),
        (mv.Column.pair_count, "column.pair_count"),
        (mv.Column.share, "column.share"),
        (mv.row_count, "row_count"),
    ):
        route = route_help_target(function)
        assert isinstance(route, NativeHelpRoute) and route.owner == "analysis"
        assert route.resolved.canonical_id == target
    for alias in ("aggregate", "summarize", "agg"):
        assert not hasattr(mv.AnalysisFrame, alias)
    assert not any(target.endswith(".aggregate") for target in REGISTRY.canonical_ids())

    page = rendered_help("analysis.analysis_frame.reduce")
    assert page == rendered_help(mv.AnalysisFrame.reduce)
    assert "Sum/mean retain expression units" in page
    assert "not original Metric additivity or rollup" in page
    for target in ("analysis.column.sum", "analysis.column.mean"):
        assert "Retain the expression's unit without certifying" in rendered_help(target)


def test_partition_and_share_help_disclose_binding_denominators_and_discovery() -> None:
    over = rendered_help("analysis.analysis_frame.over")
    share = rendered_help("analysis.column.share")
    assert over == rendered_help(mv.AnalysisFrame.over)
    assert share == rendered_help(mv.Column.share)
    for fact in ("same-input Column", "empty input stays empty", "before selection", "Nested over"):
        assert fact in over
    for fact in (
        "complementary axes",
        "zero-axis",
        "filtering afterward preserves",
        "unknown units",
    ):
        assert fact in share
    assert "analysis.analysis_frame.over" in rendered_help("analysis.value_statistic")
    assert "analysis.analysis_frame.over" in rendered_help("analysis.analysis_frame")
    assert "analysis.column.share" in rendered_help("analysis.column")
    axis = rendered_help("analysis.frame_axis")
    assert "analysis.analysis_frame.over" in axis and "analysis.column.share" in axis


@pytest.mark.parametrize("method", ("align", "compare"))
def test_frame_correspondence_help_separates_caller_intent_from_identity(method: str) -> None:
    page = rendered_help("analysis.analysis_frame." + method)
    assert page == rendered_help(getattr(mv.AnalysisFrame, method))
    if method == "compare":
        assert "Caller owns business correspondence" in page
        assert "Entity/Field identity is not checked" in page
        assert "Names certify no Metric identity/business comparability" in page
    else:
        assert "declaring caller business correspondence" in page
        assert "without checking original Entity/Field equality" in page
    axis = rendered_help("analysis.frame_axis")
    assert axis == rendered_help(mv.FrameAxis)
    assert "without original Entity/Field nominal bindings" in axis
    assert "validation does not prove semantic identity" in axis


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
    assert "construction reads no rows" in page.lower()
    assert "new semantic bindings after joint key projection" in page
    assert "not original Entity/Field equality" in page
    assert "Compatible Customer keys may bind Product" in page
    assert "shipping-city keys may bind billing-city" in page
    assert "Ordinary fields cannot become identities" in page
    assert "analysis.session.population_from" in rendered_help("analysis.inputs.population")
    assert "analysis.session.population_from" in rendered_help("analysis.population")
