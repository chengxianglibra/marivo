"""Exact shared-basis allocation; no source, Catalog or legacy executor access."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from typing import Literal, TypeAlias

from marivo.analysis.core.model import AttributionPart
from marivo.analysis.methods.comparison import _finish, propagated_error, roundoff
from marivo.analysis.methods.physical import ValueType


def columns(part: AttributionPart) -> tuple[str, ...]:
    """Return the closed sufficient-state fields of an allocation role."""
    endpoint = part.endpoint
    if endpoint is not None:
        return (
            "value",
            "cell_tag",
            "cell_reason",
            *(
                ("state__" + c for c in endpoint.original_state.components)
                if endpoint.original_state
                else ()
            ),
            *(("contribution_present",) if endpoint.coordinate_state else ()),
            *(("complete",) if endpoint.original_state else ()),
        )
    return {
        "basis": ("value",),
        "allocation": (
            "contribution",
            "current",
            "baseline",
            "contribution_error_bound",
            "current_error_bound",
            "baseline_error_bound",
        ),
        "reconciliation": ("target", "total", "complete"),
        "selection_scope": ("selected",),
    }[part.role]


def part_keys(part: AttributionPart) -> tuple[str, ...]:
    """Return exact role keys from the frozen scope declaration."""
    return tuple(f"key_{i}" for i in range(len(part.domain.instance_key)))


Number: TypeAlias = int | float | Decimal
Axis: TypeAlias = tuple[str | None, bool]
Coordinate: TypeAlias = tuple[Axis, ...]
Basis: TypeAlias = dict[tuple[str | None, ...], tuple[Fraction, Fraction]]
Errors: TypeAlias = dict[tuple[str | None, ...], float]


def numeric(value: object) -> Fraction:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        raise ValueError("finite numeric component required")
    try:
        return Fraction(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("finite numeric component required") from error


def components(state: Mapping[str, object], method: str) -> tuple[Fraction, Fraction]:
    if method in ("sum@v1", "sum_zero@v1"):
        return numeric(state["sum"]), Fraction(1)
    if method == "count@v1":
        return numeric(state["count"]), Fraction(1)
    if method == "mean@v1":
        return numeric(state["sum"]), numeric(state["non_null_count"])
    if method == "weighted_mean@v1":
        return numeric(state["weighted_numerator"]), numeric(state["weight_sum"])
    if method == "ratio@v1":
        return numeric(state["numerator_sum"]), numeric(state["denominator_sum"])
    if method == "linear@v1":
        total = sum(
            (
                numeric(v) * (1 if k.startswith("plus_") else -1)
                for k, v in state.items()
                if k.endswith("_sum") and not k.endswith("_absolute_sum")
            ),
            Fraction(),
        )
        return total, Fraction(1)
    raise ValueError("no additive or component allocation method for the original state")


def component_errors(state: Mapping[str, object], method: str) -> tuple[float, float]:
    """Recover R5 numerator/denominator bounds from original magnitudes."""

    def magnitude(name: str) -> float:
        value = state[name]
        if type(value) is not float or not math.isfinite(value) or value < 0:
            raise ValueError("finite nonnegative original error magnitude required")
        return value

    if method in ("sum@v1", "sum_zero@v1", "mean@v1"):
        return (
            (roundoff(magnitude("absolute_sum")), 0.0)
            if type(state["sum"]) is float
            else (0.0, 0.0)
        )
    if method == "linear@v1":
        return math.fsum(roundoff(magnitude(k)) for k in state if k.endswith("_absolute_sum")), 0.0
    if method in ("weighted_mean@v1", "ratio@v1"):
        weighted = method == "weighted_mean@v1"
        numerator = "weighted_numerator" if weighted else "numerator_sum"
        denominator = "weight_sum" if weighted else "denominator_sum"
        error = 0.0
        if type(state[numerator]) is float:
            absolute = magnitude(
                "absolute_weighted_numerator" if weighted else "numerator_absolute_sum"
            )
            error = roundoff(absolute)
            if weighted:
                count = state["non_null_pair_count"]
                if type(count) is not int or count < 0:
                    raise ValueError("nonnegative original pair count required")
                error += 1e-12 * (absolute + count)
        denominator_error = (
            roundoff(magnitude("absolute_weight_sum" if weighted else "denominator_absolute_sum"))
            if type(state[denominator]) is float
            else 0.0
        )
        return error, denominator_error
    return 0.0, 0.0


def key(coordinate: Coordinate) -> tuple[tuple[int, str], ...]:
    return tuple(
        (2, "") if other else (0, "") if value is None else (1, value)
        for value, other in coordinate
    )


def mapping(
    current: Basis, baseline: Basis, size: int, top_k: int | None, component_mix: bool
) -> dict[tuple[str | None, ...], Coordinate]:
    raw = set(current) | set(baseline)
    mapped: dict[tuple[str | None, ...], Coordinate] = dict.fromkeys(raw, ())
    for index in range(size):
        parents = set(mapped.values())
        for parent in sorted(parents, key=key):
            scores: dict[str | None, Fraction] = {}
            for c in raw:
                if mapped[c] != parent:
                    continue
                position = 1 if component_mix else 0
                score = abs(current.get(c, (Fraction(), Fraction()))[position]) + abs(
                    baseline.get(c, (Fraction(), Fraction()))[position]
                )
                scores[c[index]] = scores.get(c[index], Fraction()) + score
            ordered = sorted(scores, key=lambda c: (0, "") if c is None else (1, c))
            ordered.sort(key=lambda c: scores[c], reverse=True)
            selected = set(ordered if top_k is None else ordered[:top_k])
            for c in raw:
                if mapped[c] == parent:
                    mapped[c] += ((c[index], False) if c[index] in selected else (None, True),)
    return mapped


def allocate(
    current: Basis,
    baseline: Basis,
    *,
    size: int,
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
    component_mix: bool,
    physical: ValueType,
    current_total: Fraction,
    baseline_total: Fraction,
) -> list[tuple[int, Coordinate, Number, Number, Number]]:
    if component_mix:
        for side in (current, baseline):
            if any(w == 0 and n != 0 for n, w in side.values()):
                raise ValueError("zero basis with nonzero numerator is contradictory")
        if current_total == 0 or baseline_total == 0:
            raise ValueError("component allocation requires nonzero total denominators")
    mapped = mapping(current, baseline, size, top_k, component_mix)
    output: list[tuple[int, Coordinate, Number, Number, Number]] = []
    for resolution in range(1, size + 1) if mode == "hierarchy" else (size,):
        grouped: dict[Coordinate, tuple[Fraction, Fraction]] = {}
        for raw, coordinate in mapped.items():
            prefix = coordinate[:resolution] + ((None, False),) * (size - resolution)
            c = current.get(raw, (Fraction(), Fraction()))[0]
            b = baseline.get(raw, (Fraction(), Fraction()))[0]
            before = grouped.get(prefix, (Fraction(), Fraction()))
            grouped[prefix] = before[0] + c, before[1] + b
        for coordinate in sorted(grouped, key=key):
            c, b = grouped[coordinate]
            current_side = _finish(c / current_total if component_mix else c, physical)
            baseline_side = _finish(b / baseline_total if component_mix else b, physical)
            contribution = _finish(numeric(current_side) - numeric(baseline_side), physical)
            output.append((resolution, coordinate, current_side, baseline_side, contribution))
    return output


def allocation_errors(
    current: Basis,
    baseline: Basis,
    current_errors: Errors,
    baseline_errors: Errors,
    allocations: list[tuple[int, Coordinate, Number, Number, Number]],
    *,
    size: int,
    top_k: int | None,
    component_mix: bool,
    current_total: Fraction,
    baseline_total: Fraction,
    current_denominator_error: float,
    baseline_denominator_error: float,
) -> dict[tuple[int, Coordinate], tuple[float, float, float]]:
    """Propagate complete component bounds through the same common mapping."""
    mapped = mapping(current, baseline, size, top_k, component_mix)
    grouped: dict[tuple[int, Coordinate], tuple[list[float], list[float]]] = {}
    for resolution in {a[0] for a in allocations}:
        for raw, coordinate in mapped.items():
            prefix = coordinate[:resolution] + ((None, False),) * (size - resolution)
            errors = grouped.setdefault((resolution, prefix), ([], []))
            errors[0].append(current_errors.get(raw, 0.0))
            errors[1].append(baseline_errors.get(raw, 0.0))
    result = {}
    for resolution, coordinate, c, b, contribution in allocations:
        if type(contribution) is not float:
            result[resolution, coordinate] = (0.0, 0.0, 0.0)
            continue
        errors = grouped[resolution, coordinate]
        sides = []
        for value, values, total, denominator_error in (
            (c, errors[0], current_total, current_denominator_error),
            (b, errors[1], baseline_total, baseline_denominator_error),
        ):
            assert isinstance(value, float)
            numerator_error = math.fsum(values)
            sides.append(
                propagated_error(
                    "ratio", None, float(total), value, numerator_error, denominator_error
                )
                if component_mix
                else numerator_error + roundoff(value)
            )
        result[resolution, coordinate] = (
            propagated_error("difference", c, b, contribution, sides[0], sides[1]),
            sides[0],
            sides[1],
        )
    return result


def reconciles(target: Fraction, values: list[Number], exact: bool) -> bool:
    total = sum((numeric(v) for v in values), Fraction())
    if exact:
        return target == total
    return abs(target - total) <= max(
        Fraction(1, 10**12), Fraction(1, 10**9) * max(abs(target), abs(total), Fraction(1))
    )
