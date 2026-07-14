"""Shared cumulative frame metadata helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

CUMULATIVE_CONTRACT_VERSION = 2

type CumulativeAnchor = (
    Literal["all_history"]
    | tuple[Literal["grain_to_date"], str]
    | tuple[Literal["trailing"], int, str]
)
type CumulativeCompareBlocker = Literal[
    "non_cumulative_component",
    "mixed_component_anchors",
    "unresolved_component_anchor",
]

_DIRECT_REQUIRED_FIELDS = frozenset({"kind", "base", "over", "anchor", "components"})
_DERIVED_REQUIRED_FIELDS = frozenset({"kind", "anchor", "compare_blocker", "components"})
_COMPARE_BLOCKERS = frozenset(
    {
        "non_cumulative_component",
        "mixed_component_anchors",
        "unresolved_component_anchor",
    }
)


def normalize_cumulative_anchor(value: object) -> CumulativeAnchor | None:
    """Return a validated in-memory cumulative anchor from metadata."""
    if value == "all_history":
        return "all_history"
    if not isinstance(value, (tuple, list)):
        return None
    if len(value) == 2 and value[0] == "grain_to_date" and isinstance(value[1], str) and value[1]:
        return ("grain_to_date", value[1])
    if (
        len(value) == 3
        and value[0] == "trailing"
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] > 0
        and isinstance(value[2], str)
        and value[2]
    ):
        return ("trailing", value[1], value[2])
    return None


def _direct_cumulative_anchor(
    cumulative: Mapping[str, object],
) -> CumulativeAnchor | None:
    """Validate the current direct marker and return its anchor."""
    if not _DIRECT_REQUIRED_FIELDS.issubset(cumulative):
        return None
    if cumulative.get("kind") != "cumulative":
        return None
    base = cumulative.get("base")
    over = cumulative.get("over")
    if not isinstance(base, str) or not base or not isinstance(over, str) or not over:
        return None
    if cumulative.get("components") is not None:
        return None
    return normalize_cumulative_anchor(cumulative.get("anchor"))


def _derived_component_anchors(
    cumulative: Mapping[str, object],
) -> tuple[CumulativeAnchor | None, ...] | None:
    """Validate the required wrapper shape and return component anchors."""
    if not _DERIVED_REQUIRED_FIELDS.issubset(cumulative):
        return None
    components = cumulative.get("components")
    if not isinstance(components, Mapping) or not components:
        return None
    anchors: list[CumulativeAnchor | None] = []
    for role, payload in components.items():
        if not isinstance(role, str) or not role or not isinstance(payload, Mapping):
            return None
        anchors.append(_direct_cumulative_anchor(payload))
    return tuple(anchors)


def cumulative_compare_anchor(cumulative: Mapping[str, object] | None) -> CumulativeAnchor | None:
    """Return the compare anchor from the current cumulative metadata contract."""
    if cumulative is None:
        return None
    kind = cumulative.get("kind")
    if kind == "cumulative":
        return _direct_cumulative_anchor(cumulative)
    if kind != "derived_contains_cumulative":
        return None
    component_anchors = _derived_component_anchors(cumulative)
    if component_anchors is None or cumulative.get("compare_blocker") is not None:
        return None
    anchor = normalize_cumulative_anchor(cumulative.get("anchor"))
    if anchor is None or any(component_anchor != anchor for component_anchor in component_anchors):
        return None
    return anchor


def cumulative_compare_blocker(
    cumulative: Mapping[str, object] | None,
) -> CumulativeCompareBlocker | None:
    """Return the persisted blocker for a derived cumulative wrapper."""
    if cumulative is None or cumulative.get("kind") != "derived_contains_cumulative":
        return None
    component_anchors = _derived_component_anchors(cumulative)
    if component_anchors is None:
        return "unresolved_component_anchor"
    blocker = cumulative.get("compare_blocker")
    if blocker in _COMPARE_BLOCKERS:
        if cumulative.get("anchor") is not None:
            return "unresolved_component_anchor"
        if blocker == "mixed_component_anchors":
            valid_anchors = [anchor for anchor in component_anchors if anchor is not None]
            if len(valid_anchors) < 2 or all(
                anchor == valid_anchors[0] for anchor in valid_anchors[1:]
            ):
                return "unresolved_component_anchor"
        if blocker == "unresolved_component_anchor" and all(
            anchor is not None for anchor in component_anchors
        ):
            return "unresolved_component_anchor"
        return cast("CumulativeCompareBlocker", blocker)
    if blocker is None and cumulative_compare_anchor(cumulative) is not None:
        return None
    return "unresolved_component_anchor"
