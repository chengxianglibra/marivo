"""Inventory requested Metric slots for shared per-endpoint Attribute source reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.frame_data import SourceFrameBinding
from marivo.analysis.core.frame_derivation import DerivationTarget, FrameDerivation, SourceMetric
from marivo.analysis.core.frame_expression import NamedFrameComparison
from marivo.analysis.core.model import BoundQuantity


@dataclass(frozen=True, slots=True)
class SourceGroupKey:
    graph_identity: int
    axis_positions: tuple[tuple[int, int], ...]
    side: Literal["left", "right"]
    match: CalendarCorrespondence | None = None
    comparison_alias: str | None = None


@dataclass(frozen=True, slots=True)
class SourceMetricGroup:
    """Only requested source Metrics, in their original declaration order."""

    key: SourceGroupKey
    binding: SourceFrameBinding
    quantities: tuple[BoundQuantity, ...]

    def index(self, path: tuple[str, ...]) -> int:
        return tuple(quantity.name for quantity in self.quantities).index(".".join(path))


def endpoint_source_side(
    target: DerivationTarget, side: Literal["left", "right"]
) -> Literal["left", "right"]:
    if target.mode == "level":
        if target.align_side is not None:
            return target.align_side
        if isinstance(target.pairing, NamedFrameComparison):
            return "right"
    return side


def source_group_key(
    metric: SourceMetric, side: Literal["left", "right"], target: DerivationTarget | None = None
) -> SourceGroupKey:
    actual = side if target is None else endpoint_source_side(target, side)
    match = (
        target.pairing.match
        if actual == "right" and target is not None and target.pairing is not None
        else None
    )
    alias = (
        target.pairing.alias
        if actual == "right"
        and target is not None
        and isinstance(target.pairing, NamedFrameComparison)
        else None
    )
    return SourceGroupKey(id(metric.binding.graph), metric.axis_positions, actual, match, alias)


def collect_source_groups(declaration: FrameDerivation) -> dict[SourceGroupKey, SourceMetricGroup]:
    """Group actual requested endpoint recipes without adding unselected obligations."""
    members: dict[SourceGroupKey, tuple[SourceFrameBinding, set[str]]] = {}
    for output in declaration.outputs:
        target = output.target
        endpoints: tuple[tuple[Literal["left", "right"], SourceMetric | None], ...] = (
            (("left", target.current),)
            if target.mode == "level"
            else (("left", target.current), ("right", target.baseline))
        )
        for side, metric in endpoints:
            assert metric is not None and side in ("left", "right")
            key = source_group_key(metric, side, target)
            if key not in members:
                members[key] = (metric.binding, set())
            members[key][1].add(".".join(metric.path))
    result: dict[SourceGroupKey, SourceMetricGroup] = {}
    for key, (binding, names) in members.items():
        quantities = tuple(
            quantity
            for quantity in binding.graph.signature.quantities
            if isinstance(quantity, BoundQuantity) and quantity.name in names
        )
        assert len(quantities) == len(names)
        result[key] = SourceMetricGroup(key, binding, quantities)
    return result
