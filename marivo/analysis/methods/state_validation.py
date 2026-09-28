"""Numerical invariants owned by the qualified transient method states."""

from __future__ import annotations

import math
from collections.abc import Mapping


def difference_matches(
    primary: Mapping[str, object],
    current: Mapping[str, object],
    baseline: Mapping[str, object],
) -> bool:
    """Validate an int64 difference against both exact retained endpoints."""
    first = current.get("current_endpoint__value")
    second = baseline.get("baseline_endpoint__value")
    return (
        type(first) is int
        and type(second) is int
        and -(2**63) <= first < 2**63
        and -(2**63) <= second < 2**63
        and current.get("current_endpoint__cell_tag") == "defined"
        and baseline.get("baseline_endpoint__cell_tag") == "defined"
        and current.get("current_endpoint__cell_reason") is None
        and baseline.get("baseline_endpoint__cell_reason") is None
        and -(2**63) <= first - second < 2**63
        and type(primary.get("value")) is int
        and primary.get("value") == first - second
        and primary.get("cell_tag") == "defined"
        and primary.get("cell_reason") is None
    )


def state_matches(kind: str, primary: Mapping[str, object], part: Mapping[str, object]) -> bool:
    """Check one complete-key-associated primary and required state part."""
    value = primary.get("value")
    if kind == "original_ratio":
        numerator = part.get("original_state__numerator_sum")
        support = part.get("original_state__numerator_non_null_count")
        denominator = part.get("original_state__denominator_count")
        if (
            type(numerator) is not int
            or not -(2**63) <= numerator < 2**63
            or type(support) is not int
            or not 0 <= support < 2**63
            or type(denominator) is not int
            or not 0 <= denominator < 2**63
            or (support == 0 and numerator != 0)
        ):
            return False
        if denominator == 0:
            return (
                value is None
                and primary.get("cell_tag") == "undefined"
                and primary.get("cell_reason") == "zero_denominator"
            )
        return (
            type(value) is float
            and math.isfinite(value)
            and value == numerator / denominator
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "original_count":
        count = part.get("original_state__count")
        return (
            type(count) is int
            and 0 <= count < 2**63
            and type(value) is int
            and value == count
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind in ("original_sum", "original_sum_zero"):
        total = part.get("original_state__sum")
        count = part.get("original_state__non_null_count")
        if (
            type(total) not in (int, float)
            or not isinstance(total, (int, float))
            or not math.isfinite(total)
            or type(count) is not int
            or not 0 <= count < 2**63
        ):
            return False
        if type(total) is int and not -(2**63) <= total < 2**63:
            return False
        if count == 0 and kind == "original_sum_zero":
            return (
                total == 0
                and value == 0
                and primary.get("cell_tag") == "defined"
                and primary.get("cell_reason") is None
            )
        if count == 0:
            return (
                total == 0
                and value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason") == "empty_contribution"
            )
        return (
            value == total
            and type(value) is type(total)
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind in ("row_sum", "row_count", "row_count_defined"):
        component = part.get(f"row_state__{kind.removeprefix('row_')}")
        return (
            (
                (type(component) is int and -(2**63) <= component < 2**63)
                or (kind == "row_sum" and type(component) is float and math.isfinite(component))
            )
            and isinstance(component, (int, float))
            and (kind == "row_sum" or component >= 0)
            and primary.get("cell_tag") == "defined"
            and type(value) is type(component)
            and value == component
        )
    if kind == "row_mean":
        total, count = part.get("row_state__sum"), part.get("row_state__count")
        if (
            not (
                (type(total) is int and -(2**63) <= total < 2**63)
                or (type(total) is float and math.isfinite(total))
            )
            or not isinstance(total, (int, float))
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


def coordinate_state_matches(
    components: tuple[str, ...],
    value_type: str,
    groups: object,
    original: Mapping[str, object],
    coordinate_columns: tuple[str, ...] = ("coordinate",),
) -> bool:
    """Verify a complete ordered coordinate partition against its original state."""
    if not isinstance(groups, list):
        return False
    labels: list[tuple[str, ...]] = []
    values: dict[str, list[int | float]] = {name: [] for name in components}
    for item in groups:
        if not isinstance(item, dict) or set(item) != {*coordinate_columns, *components}:
            return False
        label = tuple(item[name] for name in coordinate_columns)
        if any(not isinstance(value, str) for value in label):
            return False
        labels.append(label)
        for name in components:
            value: object = item[name]
            floating = value_type == "float64" and name in ("sum", "numerator_sum")
            if floating:
                if type(value) is not float or not math.isfinite(value):
                    return False
            elif type(value) is not int or not -(2**63) <= value < 2**63:
                return False
            if "count" in name and value < 0:
                return False
            values[name].append(value)
        for total_name, count_name in (
            ("sum", "non_null_count"),
            ("numerator_sum", "numerator_non_null_count"),
        ):
            if count_name in components and item[count_name] == 0 and item[total_name] != 0:
                return False
    if labels != sorted(set(labels)):
        return False
    for name, entries in values.items():
        expected = original.get(f"original_state__{name}")
        floating = value_type == "float64" and name in ("sum", "numerator_sum")
        if floating:
            if type(expected) is not float or not math.isfinite(expected):
                return False
            try:
                total = math.fsum(entries)
            except OverflowError:
                return False
            if not math.isclose(total, expected, rel_tol=1e-12, abs_tol=1e-12):
                return False
        elif type(expected) is not int or sum(entries) != expected:
            return False
    return True
