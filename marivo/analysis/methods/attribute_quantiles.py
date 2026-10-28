"""Exact typed frequencies and incremental order statistics for Attribute games."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal, TypeAlias

from marivo.analysis.core.data_types import DataType, DecimalDataType, ScalarDataType
from marivo.analysis.methods.numeric_state import finish_division
from marivo.analysis.methods.physical import DecimalType

FrequencyValue: TypeAlias = int | float | Decimal
QuantileFinisher: TypeAlias = Literal["rank_bounds", "duckdb_native", "postgres_native"]


def _value_key(value: FrequencyValue) -> tuple[int, FrequencyValue]:
    # Native exact numeric ordering places valid NaN after every finite value
    # and infinity. A single token also preserves native GROUP BY NaN equality.
    return (1, 0) if isinstance(value, float) and math.isnan(value) else (0, value)


@dataclass(frozen=True, slots=True)
class FrequencyState:
    """One group's exact non-NULL values and its original input support."""

    dtype: DataType
    values: tuple[FrequencyValue, ...]
    frequencies: tuple[int, ...]
    support_count: int
    null_count: int

    def __post_init__(self) -> None:
        if not (
            isinstance(self.dtype, DecimalDataType)
            or self.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        ):
            raise ValueError("frequency inputs require a numeric DataType")
        if len(self.values) != len(self.frequencies):
            raise ValueError("frequency values and counts must have the same length")
        if any(type(count) is not int or count <= 0 for count in self.frequencies):
            raise ValueError("frequency counts must be positive integers")
        if (
            type(self.support_count) is not int
            or type(self.null_count) is not int
            or self.null_count < 0
            or self.support_count != sum(self.frequencies) + self.null_count
        ):
            raise ValueError("frequency support must equal valid and NULL counts")
        if any(
            type(value) not in (int, float, Decimal)
            or (isinstance(value, Decimal) and not value.is_finite())
            for value in self.values
        ):
            raise ValueError("frequency states require native numeric values")
        if any(
            _value_key(left) >= _value_key(right)
            for left, right in zip(self.values, self.values[1:], strict=False)
        ):
            raise ValueError("frequency values must be distinct and ordered in their native type")

    @property
    def valid_count(self) -> int:
        return self.support_count - self.null_count


def frequency_state(
    dtype: DataType,
    entries: Iterable[tuple[FrequencyValue | None, int]],
) -> FrequencyState:
    """Canonicalize a native grouped result without expanding repeated values."""
    counts: dict[tuple[int, FrequencyValue], tuple[FrequencyValue, int]] = {}
    null_count = 0
    for value, count in entries:
        if type(count) is not int or count <= 0:
            raise ValueError("frequency counts must be positive integers")
        if value is None:
            null_count += count
        else:
            key = _value_key(value)
            previous = counts.get(key)
            counts[key] = (value, count + (previous[1] if previous is not None else 0))
    ordered = tuple(counts[key] for key in sorted(counts))
    values = tuple(value for value, _ in ordered)
    frequencies = tuple(count for _, count in ordered)
    return FrequencyState(dtype, values, frequencies, sum(frequencies) + null_count, null_count)


def finish_quantile(
    lower: FrequencyValue,
    upper: FrequencyValue,
    fraction: Fraction,
    output_type: DataType,
) -> FrequencyValue:
    """Use the source exact-bound interpolation and owning Decimal finishing."""
    if any(isinstance(bound, float) and not math.isfinite(bound) for bound in (lower, upper)):
        left, right = float(lower), float(upper)
        if math.isnan(left) or math.isnan(right):
            return float("nan")
        if fraction == 0 or left == right:
            return left
        if math.isinf(left) and math.isinf(right):
            return float("nan")
        return left if math.isinf(left) else right
    exact = Fraction(lower) + fraction * (Fraction(upper) - Fraction(lower))
    return (
        finish_division(
            exact.numerator,
            exact.denominator,
            DecimalType(output_type.precision, output_type.scale),
        )
        if isinstance(output_type, DecimalDataType)
        else float(exact)
    )


class FrequencyRankIndex:
    """An integer Fenwick index over the fixed invocation's typed value catalog."""

    def __init__(self, states: tuple[FrequencyState, ...]) -> None:
        if not states:
            raise ValueError("a rank index requires at least one typed state")
        self.dtype = states[0].dtype
        if any(state.dtype != self.dtype for state in states):
            raise ValueError("all rank-index states must retain one native input type")
        catalog = {_value_key(value): value for state in states for value in state.values}
        self.values = tuple(catalog[key] for key in sorted(catalog))
        self._positions = {_value_key(value): index for index, value in enumerate(self.values)}
        self._tree = [0] * (len(self.values) + 1)
        self._counts = [0] * len(self.values)
        self.count = 0
        self.support_count = 0
        self.null_count = 0
        self.distinct_count = 0

    def add(self, state: FrequencyState, multiplier: int = 1) -> None:
        """Add or remove one captured state using exact integer frequencies."""
        if state.dtype != self.dtype or multiplier not in (-1, 1):
            raise ValueError("rank updates require the original input type and a sign of -1 or 1")
        updates = tuple(
            (self._positions[_value_key(value)], multiplier * count)
            for value, count in zip(state.values, state.frequencies, strict=True)
        )
        if (
            any(self._counts[position] + count < 0 for position, count in updates)
            or self.support_count + multiplier * state.support_count < 0
            or self.null_count + multiplier * state.null_count < 0
        ):
            raise ValueError("a rank update cannot remove uncaptured frequency mass")
        for position, count in updates:
            before = self._counts[position]
            self._counts[position] += count
            self.distinct_count += int(self._counts[position] > 0) - int(before > 0)
            index = position + 1
            while index < len(self._tree):
                self._tree[index] += count
                index += index & -index
        self.count += multiplier * state.valid_count
        self.support_count += multiplier * state.support_count
        self.null_count += multiplier * state.null_count

    def at_rank(self, rank: int) -> FrequencyValue:
        """Return a zero-based exact order statistic in O(log K)."""
        if type(rank) is not int or not 0 <= rank < self.count:
            raise ValueError("rank must identify a present non-NULL value")
        index = 0
        remaining = rank + 1
        step = 1 << (len(self.values).bit_length() - 1)
        while step:
            candidate = index + step
            if candidate < len(self._tree) and self._tree[candidate] < remaining:
                remaining -= self._tree[candidate]
                index = candidate
            step >>= 1
        return self.values[index]

    def quantile(
        self, q: float, output_type: DataType, *, finisher: QuantileFinisher = "rank_bounds"
    ) -> FrequencyValue | None:
        """Use the captured source finish recipe while keeping value ordering typed."""
        if not math.isfinite(q) or not 0 <= q <= 1:
            raise ValueError("quantile probability must be finite and within [0, 1]")
        if not self.count:
            return None
        if finisher != "rank_bounds":
            if finisher == "duckdb_native" and q not in (0.0, 1.0):
                raise ValueError("native DuckDB interpolation has no qualified state finisher")
            position_native = (self.count - 1) * q
            low_native, high_native = math.floor(position_native), math.ceil(position_native)
            left = float(self.at_rank(low_native))
            if low_native == high_native:
                return left
            right = float(self.at_rank(high_native))
            fraction_native = position_native - low_native
            assert finisher == "postgres_native"
            return left + fraction_native * (right - left)
        position = Fraction(str(q)) * (self.count - 1)
        low, high = math.floor(position), math.ceil(position)
        return finish_quantile(self.at_rank(low), self.at_rank(high), position - low, output_type)


class FrequencyGame:
    """Switch complete groups between two fixed endpoint distributions."""

    def __init__(
        self,
        baseline: tuple[FrequencyState, ...],
        current: tuple[FrequencyState, ...],
        input_type: DataType | None = None,
        finisher: QuantileFinisher = "rank_bounds",
    ) -> None:
        if len(baseline) != len(current) or (not baseline and input_type is None):
            raise ValueError("a frequency game needs paired states for every player")
        self.baseline = baseline
        self.current = current
        states = (*baseline, *current)
        if not states:
            assert input_type is not None
            states = (FrequencyState(input_type, (), (), 0, 0),)
        self.index = FrequencyRankIndex(states)
        self.mask = 0
        self.finisher = finisher
        for state in baseline:
            self.index.add(state)

    def _switch(self, mask: int) -> None:
        if type(mask) is not int or not 0 <= mask < 1 << len(self.baseline):
            raise ValueError("coalition mask must name only captured players")
        changed = self.mask ^ mask
        while changed:
            bit = changed & -changed
            player = bit.bit_length() - 1
            before, after = (
                (self.current[player], self.baseline[player])
                if self.mask & bit
                else (self.baseline[player], self.current[player])
            )
            self.index.add(before, -1)
            self.index.add(after)
            changed ^= bit
        self.mask = mask

    def value(self, mask: int, q: float, output_type: DataType) -> FrequencyValue | None:
        self._switch(mask)
        return self.index.quantile(q, output_type, finisher=self.finisher)

    def distinct(self, mask: int) -> int:
        """Count the union of qualified exact numeric membership states."""
        self._switch(mask)
        return self.index.distinct_count
