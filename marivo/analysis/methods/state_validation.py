"""Numerical invariants owned by the qualified transient method states."""

from __future__ import annotations

import math
from collections.abc import Mapping


def state_matches(kind: str, primary: Mapping[str, object], part: Mapping[str, object]) -> bool:
    """Check one complete-key-associated primary and required state part."""
    value = primary.get("value")
    if kind in ("row_sum", "row_count", "row_count_defined"):
        component = part.get(f"row_state__{kind.removeprefix('row_')}")
        return (
            type(component) is int
            and -(2**63) <= component < 2**63
            and (kind == "row_sum" or component >= 0)
            and primary.get("cell_tag") == "defined"
            and type(value) is int
            and value == component
        )
    if kind == "row_mean":
        total, count = part.get("row_state__sum"), part.get("row_state__count")
        if (
            type(total) is not int
            or not -(2**63) <= total < 2**63
            or type(count) is not int
            or not 0 <= count < 2**63
        ):
            return False
        if count == 0:
            return (
                total == 0
                and value is None
                and primary.get("cell_tag") == "undefined"
                and primary.get("cell_reason") == "empty_mean"
            )
        return (
            primary.get("cell_tag") == "defined"
            and type(value) is float
            and math.isfinite(value)
            and value == float(total) / count
        )
    if kind == "spearman":
        counts = tuple(
            part.get(f"pair_counts__{name}_count")
            for name in ("input_observation", "matched_observation", "null_pair", "complete_pair")
        )
        if any(type(count) is not int or not 0 <= count < 2**63 for count in counts):
            return False
        # Narrow independently of the physical producer or Arrow scalar conversion.
        incoming, matched, nulls, complete = counts
        assert isinstance(incoming, int) and isinstance(matched, int)
        assert isinstance(nulls, int) and isinstance(complete, int)
        if incoming != matched or matched != nulls + complete:
            return False
        status = primary.get("status")
        if complete < 2:
            return status == "insufficient_pairs" and value is None
        if status == "valid":
            return type(value) is float and math.isfinite(value) and -1 <= value <= 1
        return status in ("constant_a", "constant_b", "constant_both") and value is None
    return False
