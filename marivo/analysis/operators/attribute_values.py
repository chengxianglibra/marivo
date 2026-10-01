"""Shared exact reconciliation and R8 driver partition values."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction

import pandas as pd

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    coverage_columns,
    fold_state_names,
)
from marivo.analysis.operators.attribution_contracts import (
    delta_part_authorities,
    delta_state_name,
)
from marivo.analysis.operators.delta_state import validate_delta_parts
from marivo.analysis.operators.errors import attribution_error
from marivo.analysis.operators.rollup import _value
from marivo.analysis.operators.row import PartFrame, aligned_part_positions
from marivo.analysis.operators.row_values import _missing, frame_keys, row_key_names

Number = int | float | Decimal


def _finite(value: object) -> Number:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float, Decimal))
        or not math.isfinite(value)
    ):
        raise attribution_error(
            "defined finite component or endpoint", "null or non-finite attribution value"
        )
    return value


def exact_magnitude(value: Number) -> Number:
    """Preserve full Decimal precision independent of the ambient context."""
    return value.copy_abs() if isinstance(value, Decimal) else abs(value)


def _sum(values: list[Number]) -> Number:
    if any(isinstance(value, Decimal) for value in values):
        with localcontext() as context:
            context.prec = 80
            return _finite(sum((Decimal(str(value)) for value in values), Decimal(0)))
    if all(isinstance(value, int) for value in values):
        return sum(value for value in values if isinstance(value, int))
    try:
        floating = [float(value) for value in values]
        try:
            result = math.fsum(floating)
        except OverflowError:
            # fsum may overflow a partial accumulator although the exact final
            # binary sum is finite. Round that exact represented sum only once.
            result = float(sum((Fraction(value) for value in floating), Fraction(0)))
        return _finite(result)
    except OverflowError:
        raise attribution_error(
            "finite component sums", "floating component sum overflow"
        ) from None


def reconciles(left: Number, right: Number) -> bool:
    if not isinstance(left, float) and not isinstance(right, float):
        return left == right
    with localcontext() as context:
        context.prec = 80
        a, b = Decimal(str(left)), Decimal(str(right))
        return abs(a - b) <= max(
            Decimal("1e-12"), Decimal("1e-9") * max(abs(a), abs(b), Decimal(1))
        )


def _state_fold(
    authority: MetricFoldAuthorityV1, states: list[dict[str, object]]
) -> dict[str, object]:
    result: dict[str, object] = {}
    for component in authority.components:
        for kind, name in component.state_columns:
            values = [_finite(state[name]) for state in states if not _missing(state[name])]
            result[name] = _sum(values) if values else 0 if kind.endswith("count") else None
    if authority.cumulative:
        names = coverage_columns(authority)
        present = [
            tuple(state[name] for name in names)
            for state in states
            if not all(_missing(state[name]) for name in names[:3])
        ]
        if present and any(values != present[0] for values in present[1:]):
            raise attribution_error(
                "aligned semi-additive evaluation-end authority", "incompatible component endpoints"
            )
        if present:
            endpoint, start, end, seconds, complete = present[0]
            if (
                not isinstance(endpoint, (pd.Timestamp, datetime))
                or endpoint != end
                or not isinstance(start, (pd.Timestamp, datetime))
                or not isinstance(end, (pd.Timestamp, datetime))
                or start > end
                or not isinstance(seconds, (int, float))
                or seconds != (end - start).total_seconds()
                or type(complete) is not bool
            ):
                raise attribution_error(
                    "exact contiguous evaluation-end state", "invalid semi-additive coverage"
                )
            result.update(zip(names, present[0], strict=True))
        else:
            result.update(zip(names, (None, None, None, 0.0, False), strict=True))
    return result


def _difference(current: Number, baseline: Number) -> Number:
    with localcontext() as context:
        context.prec = 80
        if isinstance(current, Decimal) or isinstance(baseline, Decimal):
            return _finite(Decimal(str(current)) - Decimal(str(baseline)))
        return _finite(current - baseline)


def prepare_partition_states(
    frame: pd.DataFrame,
    row: DatasetRowContract,
    parts: tuple[PartFrame, ...],
    check: Callable[[], None] | None = None,
) -> tuple[tuple[MetricFoldAuthorityV1, ...], tuple[list[dict[str, object]], ...]]:
    """Validate complete Delta state once and align each side by its exact input key."""
    if check is not None:
        check()
    validate_delta_parts(frame, parts, row)
    keys = row_key_names(row)
    input_keys = frame_keys(frame, keys)
    authorities = delta_part_authorities(row)
    states_by_side: list[list[dict[str, object]]] = []
    for side, (role, authority) in zip(("current", "baseline"), authorities, strict=True):
        part = next(part for part in parts if part.role == role)
        positions = aligned_part_positions(part, keys, input_keys)
        states: list[dict[str, object]] = []
        for index, key in enumerate(input_keys):
            if check is not None and index % 1024 == 0:
                check()
            states.append(
                {
                    name: part.frame[delta_state_name(side, name)].iloc[positions[key]]
                    for name in fold_state_names(authority)
                }
            )
        states_by_side.append(states)
    return tuple(authority for _, authority in authorities), tuple(states_by_side)


def partition_endpoints(
    authorities: tuple[MetricFoldAuthorityV1, ...],
    states_by_side: tuple[list[dict[str, object]], ...],
    indices: list[int],
) -> tuple[Number, ...]:
    """Evaluate independent exact endpoints, without any Attribution share arithmetic."""
    return tuple(
        _finite(_value(authority, _state_fold(authority, [states[index] for index in indices])))
        for authority, states in zip(authorities, states_by_side, strict=True)
    )
