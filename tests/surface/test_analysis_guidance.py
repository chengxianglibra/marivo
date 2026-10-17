"""First-round public Help routes and examples use their native owners."""

from __future__ import annotations

import ast
import inspect
import re
from collections import deque

import pytest

import marivo.analysis as mv
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis._capabilities.registry import REGISTRY


def test_member_axis_is_a_pure_immutable_observation_argument() -> None:
    axis = mv.member()
    assert type(axis) is mv.MemberAxis
    assert axis == mv.member() and not inspect.signature(mv.member).parameters
    assert str(inspect.signature(mv.member).return_annotation) == "MemberAxis"
    assert "MemberAxis" in repr(axis) and "analysis.member" in repr(axis)
    assert "\n" not in repr(axis)
    with pytest.raises((AttributeError, TypeError)):
        attribute = "entity"
        setattr(axis, attribute, "sales.customer")
    annotation = str(inspect.signature(mv.Session.observe).parameters["by"].annotation)
    assert "MemberAxis" in annotation and "Population" in annotation
    for name in ("LogicalSelectedCategoryRelation", "MaterializedSelectedCategoryRelation"):
        assert not hasattr(mv, name)
        assert not any(name in target for target in REGISTRY.canonical_ids())


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
        if target.startswith(("Query.", "Population.", "Column."))
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
    for owner in (mv.Population, mv.Query, mv.Artifact):
        assert not hasattr(owner, "group_by")
        assert not hasattr(owner, "aggregate")
    for query_owner, method in (
        (mv.Query, "rollup"),
        (mv.Query, "reduce"),
        (mv.Session, "observe"),
    ):
        assert "groups" not in inspect.signature(getattr(query_owner, method)).parameters
    assert not any("GroupedAnalysisDomain" in target for target in REGISTRY.canonical_ids())


def test_current_row_aggregation_has_one_public_entry() -> None:
    for function, target in (
        (mv.Query.reduce, "Query.reduce"),
        (mv.Column.sum, "Column.sum"),
        (mv.Column.mean, "Column.mean"),
        (mv.row_count, "row_count"),
    ):
        route = route_help_target(function)
        assert isinstance(route, NativeHelpRoute) and route.owner == "analysis"
        assert route.resolved.canonical_id == target
    for alias in ("aggregate", "summarize", "agg"):
        assert not hasattr(mv.Query, alias) and not hasattr(mv.Artifact, alias)
    assert not any(target.endswith(".aggregate") for target in REGISTRY.canonical_ids())
