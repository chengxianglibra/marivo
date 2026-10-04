"""First-round public Help routes and examples use their native owners."""

from __future__ import annotations

import ast
import re
from collections import deque

from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis._capabilities.registry import REGISTRY


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
            assert "Signature:" in page
            assert "Example:" in page
            assert _free_names(descriptor.example.code) <= set(descriptor.example.requires)


def test_first_round_help_has_receiver_specific_constraints() -> None:
    entry = render(REGISTRY, "entry")
    assert "Entity-member questions" in entry
    assert "start with session.members(Entity Ref)" in entry
    assert (
        "Use session.members(...) for typed analysis graphs and numeric statistical methods."
        in entry
    )
    assert entry.index("analysis.session.members") < entry.index("analysis.observe")
    assert "receiver's contract() actions" in render(REGISTRY, "methods")
    time_scope = render(REGISTRY, "time_scope")
    assert "start is included and end is excluded" in time_scope
    assert "Date-only bounds use the Session's report timezone" in time_scope
    assert "time_scope(start='2026-08-01', end='2026-09-01')" in time_scope

    assert "dsl.GroupedRatioRelation.rollup" in render(REGISTRY, "methods.metric")
    assert "dsl.MaterializedRatioRelation.rollup" in render(REGISTRY, "methods.metric")
    assert "dsl.NumericComparison.compare" in render(REGISTRY, "methods.compare")
    assert "dsl.MaterializedSelectedDifferenceRelation.members" in render(REGISTRY, "methods.rows")
    assert "dsl.MaterializedCoefficientRelation.where" in render(REGISTRY, "methods.association")

    assert "dsl.NumericComparison.rank" in render(REGISTRY, "methods.rows")
    assert "dsl.table" in render(REGISTRY, "methods.rows")
    ranking_help = render(REGISTRY, "LogicalRankingResult")
    assert "values" in ranking_help and "ranks" in ranking_help
    for target in ("LogicalAttributionResult", "MaterializedAttributionResult"):
        text = render(REGISTRY, target)
        assert "contribution" in text and "current" in text and "baseline" in text
    assert "dsl.LogicalDifferenceRelation.attribute" in render(REGISTRY, "methods.compare")
    assert "axes" in render(REGISTRY, "dsl.MaterializedDifferenceRelation.attribute")
    terminal_help = render(REGISTRY, "MaterializedTable")
    assert "contract" not in next(
        line for line in terminal_help.splitlines() if line.startswith("Methods:")
    )

    observed = render(REGISTRY, "dsl.LogicalAnalysisDomain.observe")
    assert "governed Metric" in observed
    assert "absolute bounds" in observed
    assert "dsl.route" in observed and "dsl.routes" in observed
    assert "coordinates=coordinates" not in observed

    rollup = render(REGISTRY, "dsl.MaterializedRatioRelation.rollup")
    assert "retained" in rollup
    assert "subgroup values are not averaged" in rollup
