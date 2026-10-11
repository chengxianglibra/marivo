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
    assert axis == mv.member()
    assert not inspect.signature(mv.member).parameters
    assert str(inspect.signature(mv.member).return_annotation) == "MemberAxis"
    assert "MemberAxis" in repr(axis) and "analysis.dsl.member" in repr(axis)
    assert "\n" not in repr(axis)
    with pytest.raises((AttributeError, TypeError)):
        axis.entity = "sales.customer"
    assert (
        str(inspect.signature(mv.LogicalAnalysisDomain.observe).parameters["by"].annotation)
        == "tuple[MemberAxis | Ref[DimensionKind] | LogicalCategoryRelation, ...]"
    )
    for name in ("LogicalSelectedCategoryRelation", "MaterializedSelectedCategoryRelation"):
        assert not hasattr(mv, name)
        assert name not in REGISTRY.canonical_ids()
        assert not any(name in target for target in REGISTRY.canonical_ids())


def test_time_binding_has_one_public_entry_per_operation() -> None:
    import marivo.analysis as mv

    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("month")
    )
    assert not hasattr(mv.LogicalAnalysisDomain, "each")
    assert not hasattr(grid, "window")
    for name in ("GridWindow", "LogicalTimeAnalysisDomain", "MaterializedTimeAnalysisDomain"):
        assert not hasattr(mv, name)
        assert name not in REGISTRY.canonical_ids()
    assert "dsl.LogicalAnalysisDomain.each" not in REGISTRY.canonical_ids()
    assert (
        str(inspect.signature(mv.LogicalAnalysisDomain.observe).parameters["during"].annotation)
        == "TimeScope | TimeGrid | None"
    )
    assert "GridEndpoint" in str(inspect.signature(mv.LogicalAnalysisDomain.read))


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

    first_round = {target for target in REGISTRY.canonical_ids() if target.startswith("dsl.")}
    assert first_round <= seen
    assert "session.members" in seen
    for target in (*sorted(first_round), "session.members"):
        route = route_help_target("analysis." + target)
        assert isinstance(route, NativeHelpRoute)
        page = render(REGISTRY, target)
        descriptor = REGISTRY.by_canonical_id(target)
        if isinstance(descriptor, CallableInput):
            assert _free_names(descriptor.example.code) <= set(descriptor.example.requires)


def test_grouping_surface_has_no_explicit_target_domains() -> None:
    import marivo.analysis as mv

    assert "GroupedAnalysisDomain" not in mv.__all__
    assert not hasattr(mv, "GroupedAnalysisDomain")
    members = (
        mv.LogicalAnalysisDomain,
        mv.MaterializedAnalysisDomain,
        mv.LogicalFixedAnalysisDomain,
    )
    scalars = (
        mv.LogicalCategoryRelation,
        mv.MaterializedCategoryRelation,
        mv.LogicalBooleanRelation,
        mv.MaterializedBooleanRelation,
        mv.LogicalSelectedBooleanRelation,
        mv.MaterializedSelectedBooleanRelation,
        mv.LogicalTemporalRelation,
        mv.MaterializedTemporalRelation,
        mv.LogicalSelectedTemporalRelation,
        mv.MaterializedSelectedTemporalRelation,
    )
    for owner in (*members, *scalars):
        assert not hasattr(owner, "group_by"), owner.__name__
        assert f"dsl.{owner.__name__}.group_by" not in REGISTRY.canonical_ids()
    for owner in members:
        assert not hasattr(owner, "count")
    for owner in scalars:
        assert hasattr(owner, "aggregate")
    for name in mv.__all__:
        owner = getattr(mv, name)
        if not inspect.isclass(owner):
            continue
        for method in ("observe", "group_by"):
            if hasattr(owner, method):
                assert "groups" not in inspect.signature(getattr(owner, method)).parameters
    assert not any("GroupedAnalysisDomain" in target for target in REGISTRY.canonical_ids())


def test_current_row_aggregation_has_one_public_entry() -> None:
    owners = (
        mv.LogicalCategoryRelation,
        mv.MaterializedCategoryRelation,
        mv.LogicalBooleanRelation,
        mv.MaterializedBooleanRelation,
        mv.LogicalTemporalRelation,
        mv.MaterializedTemporalRelation,
        mv.LogicalSelectedBooleanRelation,
        mv.MaterializedSelectedBooleanRelation,
        mv.LogicalSelectedTemporalRelation,
        mv.MaterializedSelectedTemporalRelation,
        mv.LogicalSelectedNumericRelation,
        mv.MaterializedSelectedNumericRelation,
        mv.LogicalNumericRelation,
        mv.MaterializedNumericRelation,
        mv.MaterializedGroupedNumericRelation,
        mv.LogicalRolledNumericRelation,
        mv.MaterializedRolledNumericRelation,
        mv.LogicalRolledRatioRelation,
        mv.MaterializedRolledRatioRelation,
        mv.LogicalRatioRelation,
        mv.MaterializedRatioRelation,
        mv.LogicalDifferenceRelation,
        mv.MaterializedDifferenceRelation,
        mv.LogicalSelectedDifferenceRelation,
        mv.MaterializedSelectedDifferenceRelation,
        mv.MaterializedCoefficientRelation,
        mv.LogicalCoefficientSelectionRelation,
        mv.MaterializedCoefficientSelectionRelation,
        mv.GroupedNumericRelation,
        mv.GroupedRatioRelation,
        mv.LogicalCoefficientRelation,
    )
    for owner in owners:
        assert callable(owner.aggregate), owner.__name__
        assert not hasattr(owner, "summarize"), owner.__name__
        assert not hasattr(owner, "agg"), owner.__name__
        route = route_help_target(owner.aggregate)
        assert isinstance(route, NativeHelpRoute)
        assert route.owner == "analysis"
        assert route.resolved.canonical_id is not None
        assert route.resolved.canonical_id.endswith(".aggregate")
    assert not any(target.endswith(".summarize") for target in REGISTRY.canonical_ids())
