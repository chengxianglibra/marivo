"""Sample ordered player permutations and finish the conditional moments."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable
from decimal import Decimal
from typing import Literal, NoReturn, TypeAlias

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute_game import (
    AttributeGameProgram,
    CoalitionValue,
    GameContext,
    GameSample,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.methods.attribute_numeric import _numeric_binary, _Value
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.attribute_game_errors import AttributeGameError
from marivo.analysis.model.data_types import DataColumn, DecimalDataType
from marivo.analysis.model.frozen_keys import FrozenKey
from marivo.semantic.spi import (
    numeric_failure,
)

_Number: TypeAlias = int | float | Decimal


def _fail(
    message: str,
    *,
    metric: str = "replacement Metric",
    parent: FrozenKey | None = None,
    occurrence: tuple[int, ...] = (),
    reason: Literal["incomplete_domain", "invalid_value"] = "incomplete_domain",
) -> NoReturn:
    raise AttributeGameError(
        reason=reason,
        metric=metric,
        received=message,
        parent=parent,
        occurrence=occurrence,
    )


def _series(value: _Number | None, column: DataColumn) -> _Value:
    try:
        return _Value(
            pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([value], type=stage_arrow_type(column.dtype))
                )
            ),
            column,
        )
    except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
        numeric_failure(
            "a scalar representable in the owning coalition carrier", f"{column.dtype!r}: {error}"
        )


def _null(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def _input_number(value: object) -> _Number:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return value
    _fail(f"a captured numeric state has an invalid physical scalar: {value!r}")


def _number(
    value: object, *, reason: Literal["incomplete_domain", "invalid_value"] = "incomplete_domain"
) -> _Number:
    if type(value) not in (int, float, Decimal):
        _fail(f"a required coalition scalar is undefined: {value!r}", reason=reason)
    assert isinstance(value, (int, float, Decimal))
    if (isinstance(value, float) and not math.isfinite(value)) or (
        isinstance(value, Decimal) and not value.is_finite()
    ):
        numeric_failure("finite demanded original Metric values", repr(value))
    return value


def _scaled_mean(values: list[float]) -> tuple[float, float]:
    """Return a finite mean and its Float64 normalization/summation roundoff bound."""
    scale = max((abs(value) for value in values), default=0.0)
    if not math.isfinite(scale):
        numeric_failure("finite Float64 sampled statistics", repr(values))
    if scale == 0:
        return 0.0, 0.0
    exponent = math.frexp(scale)[1]
    normalized = [math.ldexp(value, -exponent) for value in values]
    summed = math.fsum(normalized)
    average = summed / len(values)
    mean = math.ldexp(average, exponent)
    # Normalization uses a power of two; only subnormal loss can round it.
    budget = math.ldexp(
        math.fsum(math.ulp(value) for value in normalized) / len(values)
        + math.ulp(summed) / len(values)
        + math.ulp(average),
        exponent,
    ) + math.ulp(mean)
    if not math.isfinite(mean) or not math.isfinite(budget):
        numeric_failure("finite Float64 sampled mean and roundoff", f"mean={mean}; bound={budget}")
    return mean, budget


def _sample_moments(values: list[float]) -> tuple[float, float, float]:
    """Compute the sample mean and standard error without unscaled squares or sums."""
    mean, budget = _scaled_mean(values)
    scale = max((abs(value) for value in values), default=0.0)
    if scale == 0:
        return mean, 0.0, budget
    exponent = math.frexp(scale)[1]
    normalized = [math.ldexp(value, -exponent) for value in values]
    # Shift before centering so adjacent large floats retain their difference,
    # even when their midpoint has no Float64 representation.
    shifts = [value - normalized[0] for value in normalized]
    center = math.fsum(shifts) / len(values)
    squared = math.fsum((value - center) ** 2 for value in shifts)
    error = math.ldexp(math.sqrt(squared / (len(values) * (len(values) - 1))), exponent)
    if not math.isfinite(error):
        numeric_failure("finite Float64 permutation standard errors", repr(error))
    return mean, error, budget


def _scaled_sum(values: tuple[float, ...] | list[float]) -> tuple[float, float]:
    """Sum finite values while permitting cancellation before rescaling."""
    scale = max((abs(value) for value in values), default=0.0)
    if not math.isfinite(scale):
        numeric_failure("finite Float64 sampled sums", repr(values))
    if scale == 0:
        return 0.0, 0.0
    exponent = math.frexp(scale)[1]
    normalized = [math.ldexp(value, -exponent) for value in values]
    normalized_sum = math.fsum(normalized)
    try:
        summed = math.ldexp(normalized_sum, exponent)
        budget = math.ldexp(
            math.fsum(math.ulp(value) for value in normalized) + math.ulp(normalized_sum), exponent
        ) + math.ulp(summed)
    except OverflowError as error:
        numeric_failure("a finite Float64 sampled sum and roundoff", str(error))
    if not math.isfinite(summed) or not math.isfinite(budget):
        numeric_failure(
            "a finite Float64 sampled sum and roundoff", f"sum={summed}; bound={budget}"
        )
    return summed, budget


def sample_game(
    parent: FrozenKey,
    players: tuple[FrozenKey, ...],
    permutations: int,
    seed: int,
    evaluate: Callable[[int], CoalitionValue],
) -> GameSample:
    """Sample complete paths over cached typed values supplied by the execution owner."""
    cache: dict[int, _Value] = {}

    def value(mask: int) -> _Value:
        if mask not in cache:
            actual = evaluate(mask)
            cache[mask] = _series(_number(actual.value, reason="invalid_value"), actual.column)
        return cache[mask]

    n = len(players)
    baseline, current = value(0), value((1 << n) - 1)
    stream = hashlib.sha256(repr(("permutation/v1", seed, parent, players)).encode()).digest()
    rng = random.Random(int.from_bytes(stream, "big"))
    marginals: list[list[float]] = [[] for _ in players]
    roundoff: list[list[float]] = [[] for _ in players]
    edge_values: dict[tuple[int, int], tuple[float, float]] = {}
    for _ in range(permutations):
        order = list(range(n))
        rng.shuffle(order)
        mask = 0
        previous = baseline
        for player in order:
            before = mask
            mask |= 1 << player
            following = value(mask)
            edge = (before, mask)
            if edge not in edge_values:
                delta = _numeric_binary("sub", following, previous)
                typed = _number(delta.values.iloc[0])
                try:
                    converted = float(typed)
                except (OverflowError, ValueError) as conversion_error:
                    numeric_failure("finite Float64 sampled marginals", str(conversion_error))
                if not math.isfinite(converted):
                    numeric_failure("finite Float64 sampled marginals", repr(typed))
                rounding = math.ulp(converted)
                if isinstance(typed, float):
                    rounding = math.fsum(
                        (
                            rounding,
                            math.ulp(float(_number(following.values.iloc[0]))),
                            math.ulp(float(_number(previous.values.iloc[0]))),
                        )
                    )
                elif isinstance(delta.column.dtype, DecimalDataType):
                    rounding += float(Decimal(1).scaleb(-delta.column.dtype.scale)) / 2
                edge_values[edge] = (converted, rounding)
            converted, rounding = edge_values[edge]
            marginals[player].append(converted)
            roundoff[player].append(rounding)
            previous = following
    moments = tuple(_sample_moments(values) for values in marginals)
    means = tuple(item[0] for item in moments)
    errors = tuple(item[1] for item in moments)
    bounds = []
    for values, moment in zip(roundoff, moments, strict=True):
        bound, normalization = _scaled_mean(values)
        bounds.append(bound + normalization + moment[2])
    budget, budget_roundoff = _scaled_sum(bounds)
    budget += budget_roundoff
    if not math.isfinite(budget):
        numeric_failure("a finite Float64 sampling roundoff budget", repr(budget))
    return GameSample(
        means,
        errors,
        CoalitionValue(current.column, _number(current.values.iloc[0])),
        CoalitionValue(baseline.column, _number(baseline.values.iloc[0])),
        len(cache),
        budget,
    )


def game_contexts(
    program: AttributeGameProgram, inputs: tuple[StageOutput, ...]
) -> tuple[GameContext, ...]:
    """Freeze parent correspondence and the joint typed player directory."""
    parent, left, right, left_projection, right_projection = inputs
    operation = program.operation
    parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
    parent_keys = frozen_key_rows(parent.table, parent_columns)
    endpoint_maps: list[dict[FrozenKey, dict[FrozenKey, int]]] = []
    for schema, source, projection, positions in (
        (program.left_schema, left, left_projection, operation.mapping.left_parents),
        (program.right_schema, right, right_projection, operation.mapping.right_parents),
    ):
        original_parent_columns = tuple(c for i in positions for c in schema.axes[i].columns)
        original_fine_columns = tuple(
            c for i, axis in enumerate(schema.axes) if i not in positions for c in axis.columns
        )
        projected = (
            frozen_key_rows(
                projection.table,
                tuple(c for axis in projection.frame_schema.axes for c in axis.columns),
            )
            if projection.frame_schema is not None
            else ()
        )
        restored = frozen_key_rows(
            projection.table, projection.columns[len(original_parent_columns) :]
        )
        correspondence = dict(zip(projected, restored, strict=True))
        groups: dict[FrozenKey, dict[FrozenKey, int]] = {}
        for index, (key, player) in enumerate(
            zip(
                frozen_key_rows(source.table, original_parent_columns),
                frozen_key_rows(source.table, original_fine_columns),
                strict=True,
            )
        ):
            if key in correspondence:
                groups.setdefault(correspondence[key], {})[player] = index
        endpoint_maps.append(groups)
    contexts = []
    for parent_index, key in enumerate(parent_keys):
        current_rows, baseline_rows = endpoint_maps[0].get(key, {}), endpoint_maps[1].get(key, {})
        players = tuple(
            sorted(
                set(current_rows) | set(baseline_rows),
                key=lambda item: tuple((value is not None, value) for _, value in item),
            )
        )
        contexts.append(
            GameContext(
                key,
                players,
                tuple(current_rows.items()),
                tuple(baseline_rows.items()),
                parent_index,
            )
        )
    return tuple(contexts)
