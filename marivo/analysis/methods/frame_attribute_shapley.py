"""Whole-Metric replacement sampling and pure typed report assembly."""

from __future__ import annotations

import hashlib
import math
import random
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Literal, NoReturn, TypeAlias

import ibis
import pandas as pd
import pyarrow as pa

from marivo._definition.expressions import ContributionFilter, EntityCount, SourceAggregate
from marivo.analysis.binding.attribute_game import AttributeGame, GameLeaf, formula_id, state_unit
from marivo.analysis.core.attribute import (
    AttributeFailureContext,
    AttributeMethodPayload,
    AttributeNumericWitness,
    AttributeParentFiber,
)
from marivo.analysis.core.attribute_game import (
    AttributeGameProgram,
    CoalitionValue,
    CompletedGame,
    GameContext,
    GameSample,
)
from marivo.analysis.core.attribute_game_errors import AttributeGameError
from marivo.analysis.core.data_types import DataColumn, DataType, DecimalDataType, ScalarDataType
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAttributeMetric,
    FrameMethodCoordinate,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodTarget,
    FrameMethodValue,
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyMethod,
    FrameShapleyQuantile,
    FrameShapleyReduction,
    _key_order,
    state_metric_approximation,
)
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows, frozen_key_table
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.attribute_game_domain import Domain, DomainProofError, prove_domain
from marivo.analysis.methods.attribute_quantiles import FrequencyGame, FrequencyState
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary, _Value
from marivo.analysis.methods.frame_attribute_report import _report_value
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    lower_metric_formula,
    numeric_binary,
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


@contextmanager
def _attribute_numeric_context(
    inputs: tuple[StageOutput, ...],
    metric: str,
    parent: FrozenKey,
    columns: tuple[DataColumn, ...],
) -> Iterator[None]:
    """Keep the numeric owner's failure attached to its actual attribute parent."""
    from marivo.semantic.errors import SemanticExpressionError

    try:
        yield
    except SemanticExpressionError as error:
        error.details["attribute_context"] = AttributeFailureContext(
            tuple(
                leaf.use
                for source in inputs
                for root in source.attribute_inputs
                for leaf in root.layout.leaves
                if leaf.use.metric == metric
            ),
            AttributeParentFiber(parent, columns),
            AttributeNumericWitness((), "contribution", ()),
        )
        error.received = f"metric={metric}; parent={parent!r}; {error.received}"
        raise


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


@dataclass(frozen=True, slots=True)
class _LeafState:
    leaf: GameLeaf
    columns: tuple[DataColumn, ...]
    current: tuple[tuple[_Number | None, ...], ...]
    baseline: tuple[tuple[_Number | None, ...], ...]
    input_types: tuple[DataType, ...]
    output: DataColumn

    @property
    def column(self) -> DataColumn:
        return self.output

    def selected(self, mask: int) -> tuple[tuple[_Number | None, ...], ...]:
        return tuple(
            self.current[i] if mask & (1 << i) else self.baseline[i]
            for i in range(len(self.current))
        )

    def invalid(self, mask: int) -> int:
        valid_slot = (
            0 if self.leaf.kind == "count" else 2 if self.leaf.kind == "weighted_mean" else 1
        )
        row_slot = 1 if self.leaf.kind == "count" else 3 if self.leaf.kind == "weighted_mean" else 2
        return sum(
            int(cell[row_slot] or 0) - int(cell[valid_slot] or 0) for cell in self.selected(mask)
        )

    def invalid_domain(self) -> Domain:
        valid_slot = (
            0 if self.leaf.kind == "count" else 2 if self.leaf.kind == "weighted_mean" else 1
        )
        row_slot = 1 if self.leaf.kind == "count" else 3 if self.leaf.kind == "weighted_mean" else 2
        pairs = [
            (
                int(a[row_slot] or 0) - int(a[valid_slot] or 0),
                int(b[row_slot] or 0) - int(b[valid_slot] or 0),
            )
            for a, b in zip(self.current, self.baseline, strict=True)
        ]
        return Domain(
            Fraction(sum(min(pair) for pair in pairs)), Fraction(sum(max(pair) for pair in pairs))
        )

    def reduce(self, mask: int) -> _Value:
        selected = self.selected(mask)
        kind, column = self.leaf.kind, self.columns[0]
        if kind in ("min", "max"):
            cells = [cell[0] for cell in selected if cell[1] and cell[0] is not None]
            value = min(cells) if kind == "min" and cells else max(cells) if cells else None
            return _series(value, self.output)
        with localcontext() as context:
            context.prec = 120
            value = sum((cell[0] for cell in selected if cell[0] is not None), 0)
            weight = (
                sum((cell[1] for cell in selected if cell[1] is not None), 0)
                if kind == "weighted_mean"
                else 0
            )
        if kind == "mean":
            count = sum(int(cell[1] or 0) for cell in selected)
            if count == 0:
                return _series(None, self.output)
            mean_value = _numeric_binary(
                "div",
                _series(value, column),
                _series(count, self.columns[1]),
                output=self.output.dtype,
            )
            return replace(mean_value, column=self.output)
        if kind == "weighted_mean":
            # The semantic owner casts each completed SUM before division.
            # Decimal division followed by a Float64 cast changes this recipe.
            table = ibis.table(
                {
                    "numerator": ibis_type(column.dtype),
                    "denominator": ibis_type(self.columns[1].dtype),
                },
                name="_attribute_weighted_finish",
            )
            expression = numeric_binary(
                "divide", NumericValue(table.numerator), NumericValue(table.denominator)
            )
            finished = evaluate_numeric(
                expression.value.op(),
                {
                    "numerator": _series(value, column).values,
                    "denominator": _series(weight, self.columns[1]).values,
                },
                pd.RangeIndex(1),
            )
            return _series(
                None if _null(finished.iloc[0]) else _number(finished.iloc[0]), self.output
            )
        if kind == "sum" and not any(cell[1] for cell in selected):
            value = None if any(cell[2] for cell in selected) else 0
        return _series(value, self.output)

    def domain(self) -> Domain:
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for cell in (*self.current, *self.baseline)
            for value in cell
        ):
            return Domain(None, None, True, finite=False)
        kind = self.leaf.kind
        valid_slot = 0 if kind == "count" else 2 if kind == "weighted_mean" else 1
        minvalid = sum(
            min(int(a[valid_slot] or 0), int(b[valid_slot] or 0))
            for a, b in zip(self.current, self.baseline, strict=True)
        )
        nullable = kind != "count" and minvalid == 0

        def bounds(slot: int) -> tuple[Fraction, Fraction]:
            pairs = [
                (Fraction(a[slot] or 0), Fraction(b[slot] or 0))
                for a, b in zip(self.current, self.baseline, strict=True)
            ]
            return sum((min(pair) for pair in pairs), Fraction(0)), sum(
                (max(pair) for pair in pairs), Fraction(0)
            )

        def magnitude(slot: int) -> Fraction:
            return sum(
                (
                    max(abs(Fraction(a[slot] or 0)), abs(Fraction(b[slot] or 0)))
                    for a, b in zip(self.current, self.baseline, strict=True)
                ),
                Fraction(0),
            )

        def mass_padding(slot: int) -> Fraction | None:
            lo, hi = bounds(slot)
            dtype = self.columns[slot].dtype
            if isinstance(dtype, DecimalDataType):
                limit = Fraction(10**dtype.precision - 1, 10**dtype.scale)
                return None if lo < -limit or hi > limit else Fraction(0)
            if dtype == ScalarDataType("int64"):
                return None if lo < -(2**63) or hi > 2**63 - 1 else Fraction(0)
            if dtype == ScalarDataType("float64"):
                absolute = magnitude(slot)
                if absolute > Fraction(sys.float_info.max):
                    return None
                if absolute == 0:
                    return Fraction(0)
                return Fraction(math.ulp(float(absolute))) * (len(self.current) + 1)
            return None

        if kind in ("min", "max"):
            cells = [
                Fraction(cell[0])
                for cell in (*self.current, *self.baseline)
                if cell[1] and cell[0] is not None
            ]
            return Domain(min(cells), max(cells), nullable) if cells else Domain.null()
        padding = mass_padding(0)
        if padding is None:
            return Domain(None, None, nullable, finite=False)
        lo, hi = bounds(0)
        lo, hi = lo - padding, hi + padding
        if kind == "mean":
            if mass_padding(1) is None:
                return Domain(None, None, nullable, finite=False)
            maximum_count = sum(
                max(int(a[1] or 0), int(b[1] or 0))
                for a, b in zip(self.current, self.baseline, strict=True)
            )
            if maximum_count == 0:
                return Domain.null()
            lower_count = max(1, minvalid)
            if isinstance(self.output.dtype, DecimalDataType):
                ratios = [
                    value / count for value in (lo, hi) for count in (lower_count, maximum_count)
                ]
                rounding = Fraction(1, 2 * 10**self.output.dtype.scale)
            else:
                limit = Fraction(sys.float_info.max)
                if lo < -limit or hi > limit:
                    return Domain(None, None, nullable, finite=False)
                endpoints = (float(lo), float(hi))
                numerator_bounds = (
                    math.nextafter(endpoints[0], -math.inf),
                    math.nextafter(endpoints[1], math.inf),
                )
                if not all(math.isfinite(value) for value in numerator_bounds):
                    return Domain(None, None, nullable, finite=False)
                numerators = tuple(Fraction(value) for value in numerator_bounds)
                denominators = (
                    Fraction(math.nextafter(float(lower_count), -math.inf)),
                    Fraction(math.nextafter(float(maximum_count), math.inf)),
                )
                ratios = [value / count for value in numerators for count in denominators]
                absolute = max(abs(value) for value in ratios)
                if absolute > Fraction(sys.float_info.max):
                    return Domain(None, None, nullable, finite=False)
                rounding = Fraction(math.ulp(float(absolute))) * 4
            return Domain(min(ratios) - rounding, max(ratios) + rounding, nullable)
        if kind == "weighted_mean":
            weight_padding = mass_padding(1)
            if weight_padding is None:
                return Domain(None, None, nullable, finite=False)
            dlow, dhigh = bounds(1)
            dlow, dhigh = dlow - weight_padding, dhigh + weight_padding
            if dlow <= 0 <= dhigh:
                if all((cell[1] or 0) == 0 for cell in (*self.current, *self.baseline)):
                    return Domain.null()
                return Domain(None, None, True, finite=False)
            values = [a / b for a in (lo, hi) for b in (dlow, dhigh)]
            absolute = max(abs(value) for value in values)
            if absolute > Fraction(sys.float_info.max):
                return Domain(None, None, False, finite=False)
            finishing = Fraction(math.ulp(float(absolute))) * 4
            return Domain(min(values) - finishing, max(values) + finishing, False)
        if kind == "sum":
            nullable = nullable and any(cell[2] for cell in (*self.current, *self.baseline))
        return Domain(lo, hi, nullable)


@dataclass(frozen=True, slots=True)
class _QuantileState:
    leaf: GameLeaf
    columns: tuple[DataColumn, ...]
    game: FrequencyGame
    input_types: tuple[DataType, ...]

    @property
    def column(self) -> DataColumn:
        return self.columns[0]

    def reduce(self, mask: int) -> _Value:
        if self.leaf.kind == "distinct":
            return _series(self.game.distinct(mask), self.columns[0])
        assert self.leaf.q is not None
        return _series(self.game.value(mask, self.leaf.q, self.columns[0].dtype), self.columns[0])

    def invalid(self, mask: int) -> int:
        return sum(
            (self.game.current[i] if mask & (1 << i) else self.game.baseline[i]).null_count
            for i in range(len(self.game.current))
        )

    def invalid_domain(self) -> Domain:
        pairs = [
            (a.null_count, b.null_count)
            for a, b in zip(self.game.baseline, self.game.current, strict=True)
        ]
        return Domain(
            Fraction(sum(min(pair) for pair in pairs)), Fraction(sum(max(pair) for pair in pairs))
        )

    def domain(self) -> Domain:
        valid = sum(
            min(a.valid_count, b.valid_count)
            for a, b in zip(self.game.baseline, self.game.current, strict=True)
        )
        raw_cells = [
            value for state in (*self.game.baseline, *self.game.current) for value in state.values
        ]
        finite_cells = [
            value for value in raw_cells if not isinstance(value, float) or math.isfinite(value)
        ]
        if self.leaf.kind == "quantile" and self.leaf.q not in (0.0, 1.0) and finite_cells:
            native_low, native_high = min(finite_cells), max(finite_cells)
            if self.game.finisher == "postgres_native" and not math.isfinite(
                float(native_high) - float(native_low)
            ):
                return Domain(None, None, valid == 0, finite=False)
            if (
                self.game.finisher == "duckdb_native"
                and max(abs(float(native_low)), abs(float(native_high))) > sys.float_info.max / 2
            ):
                return Domain(None, None, valid == 0, finite=False)
        if self.leaf.kind != "distinct" and any(
            isinstance(value, float) and not math.isfinite(value) for value in raw_cells
        ):
            return Domain(None, None, valid == 0, finite=False)
        cells = [
            Fraction(value)
            for value in raw_cells
            if not isinstance(value, float) or math.isfinite(value)
        ]
        if self.leaf.kind == "distinct":
            return Domain(Fraction(0), Fraction(len(self.game.index.values)))
        if not cells:
            return Domain.null()
        low, high = min(cells), max(cells)
        if self.columns[0].dtype == ScalarDataType("float64"):
            low_float, high_float = float(low), float(high)
            if not math.isfinite(low_float) or not math.isfinite(high_float):
                return Domain(None, None, valid == 0, finite=False)
            padding = Fraction(math.ulp(max(abs(low_float), abs(high_float)))) * 4
            return Domain(Fraction(low_float) - padding, Fraction(high_float) + padding, valid == 0)
        return Domain(low, high, valid == 0)


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


def _sample(
    game: AttributeGame,
    states: tuple[_LeafState | _QuantileState, ...],
    parent: FrozenKey,
    players: tuple[FrozenKey, ...],
    permutations: int,
    seed: int,
    paired_game: AttributeGame | None = None,
) -> tuple[GameSample, tuple[str, ...]]:
    import ibis.expr.datatypes as dt

    types: dict[str, dt.DataType] = {}
    fields: dict[str, Domain] = {}
    source_aliases: dict[tuple[object, ...], int] = {}
    aliases = []
    other_leaves = game.leaves if paired_game is None else paired_game.leaves
    for index, (state, other_leaf) in enumerate(zip(states, other_leaves, strict=True)):
        identity = (state.leaf.kind, state.leaf.q, state.leaf.slots, other_leaf.slots)
        aliases.append(source_aliases.setdefault(identity, index))
    try:
        for index, state in enumerate(states):
            alias = aliases[index]
            types[f"leaf_{alias}"] = ibis_type(state.column.dtype)
            types[f"invalid_{alias}"] = dt.int64
            fields[f"leaf_{alias}"] = state.domain()
            fields[f"invalid_{alias}"] = state.invalid_domain()
        table = ibis.table(types, name="_attribute_game")
        leaves = {
            state.leaf.occurrence: NumericValue(
                table[f"leaf_{aliases[index]}"],
                (
                    NumericCheck(
                        table[f"invalid_{aliases[index]}"] > 0,
                        "non-NULL demanded original inputs",
                        f"occurrence={state.leaf.occurrence}; nulls=error",
                    ),
                )
                if state.leaf.nulls == "error"
                else (),
            )
            for index, state in enumerate(states)
        }
        expression = lower_metric_formula(game.node, (0,), leaves, dict(game.declared))
        proof = prove_domain(
            expression.value.op(),
            tuple(check.violation.op() for check in expression.checks),
            fields,
        )
        proof += tuple(
            f"occurrence={state.leaf.occurrence}: fixed interval [{fields[f'leaf_{aliases[index]}'].low}, {fields[f'leaf_{aliases[index]}'].high}], NULL possible={fields[f'leaf_{aliases[index]}'].nullable}, null-count interval [{fields[f'invalid_{aliases[index]}'].low}, {fields[f'invalid_{aliases[index]}'].high}]"
            for index, state in enumerate(states)
        )
    except DomainProofError as proof_error:
        _fail(str(proof_error), metric=game.metric, parent=parent)
    output = DataColumn("coalition", "coalition", data_type(expression.value.type()), False)

    def evaluate(mask: int) -> CoalitionValue:
        physical = {
            f"leaf_{aliases[i]}": (
                _series(None, state.column).values
                if not fields[f"leaf_{aliases[i]}"].finite
                else state.reduce(mask).values
            )
            for i, state in enumerate(states)
        }
        physical.update(
            {
                f"invalid_{aliases[i]}": pd.Series([state.invalid(mask)], dtype="int64")
                for i, state in enumerate(states)
            }
        )
        for check in expression.checks:
            if bool(
                evaluate_numeric(check.violation.op(), physical, pd.RangeIndex(1))
                .astype("boolean")
                .fillna(False)
                .iloc[0]
            ):
                _fail(f"coalition={mask}; {check.received}", metric=game.metric, parent=parent)
        series = evaluate_numeric(expression.value.op(), physical, pd.RangeIndex(1))
        return CoalitionValue(output, _number(series.iloc[0]))

    return sample_game(parent, players, permutations, seed, evaluate), proof


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


def attribute_game_data(
    program: AttributeGameProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    _, left, right, _, _ = inputs
    operation, target = program.operation, program.operation.target
    parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
    output_columns = dict(
        zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
    )
    capture_directory = {
        capture.stage_id: capture
        for source in (left, right)
        for capture in source.attribute_state_captures
    }
    current_ids = {capture.stage_id for capture in left.attribute_state_captures}
    baseline_ids = {capture.stage_id for capture in right.attribute_state_captures}
    captured_facts = []
    for capture_id, receipt in sorted(capture_directory.items()):
        endpoint: Literal["current", "baseline", "both"] = (
            "both"
            if capture_id in current_ids and capture_id in baseline_ids
            else "current"
            if capture_id in current_ids
            else "baseline"
        )
        route: Literal["typed_sufficient_state/v1", "typed_value_frequency/v1"] = (
            "typed_value_frequency/v1"
            if receipt.state_route in ("typed_frequencies", "typed_membership")
            else "typed_sufficient_state/v1"
        )
        captured_facts.append(
            FrameShapleyCapture(
                capture_id,
                endpoint,
                receipt.provider,
                None,
                route,
                receipt.state_entries,
                receipt.state_bytes,
                receipt.returned_rows,
                receipt.decoded_arrow_bytes,
                1,
                0,
                0,
                None,
                None,
                None,
            )
        )
    if not captured_facts:
        _fail("no actual fixed-state capture receipts")
    routes = {capture.state_route for capture in captured_facts}
    combined_route: Literal[
        "typed_sufficient_state/v1", "typed_value_frequency/v1", "typed_sufficient_frequency/v1"
    ] = next(iter(routes)) if len(routes) == 1 else "typed_sufficient_frequency/v1"
    input_type_maps = tuple(
        {
            slot: types
            for capture in source.attribute_state_captures
            for slot, types in capture.input_types
        }
        for source in (left, right)
    )
    output_type_maps = tuple(
        {
            slot: dtype
            for capture in source.attribute_state_captures
            for slot, dtype in capture.output_types
        }
        for source in (left, right)
    )
    completed_games = []
    for context in game_contexts(program, inputs):
        key, players = context.parent, context.players
        current_rows, baseline_rows = dict(context.current_rows), dict(context.baseline_rows)
        states: list[_LeafState | _QuantileState] = []
        for leaf in operation.games[0].leaves:
            columns = tuple(program.left_schema.values[slot] for slot in leaf.slots)
            original_slot = dict(program.original_value_slots[0])[leaf.occurrence]
            input_types = input_type_maps[0].get(original_slot)
            if input_types is None:
                _fail(
                    f"occurrence={leaf.occurrence}: captured original physical input types are missing"
                )
            baseline_leaf = next(
                item for item in operation.games[1].leaves if item.occurrence == leaf.occurrence
            )
            baseline_original_slot = dict(program.original_value_slots[1])[baseline_leaf.occurrence]
            if input_type_maps[1].get(baseline_original_slot) != input_types:
                _fail(f"occurrence={leaf.occurrence}: endpoint physical reducer input types differ")
            baseline_columns = tuple(
                program.right_schema.values[slot] for slot in baseline_leaf.slots
            )
            original_output_type = output_type_maps[0].get(original_slot)
            if (
                original_output_type is None
                or output_type_maps[1].get(baseline_original_slot) != original_output_type
            ):
                _fail(
                    f"occurrence={leaf.occurrence}: original native reducer output carriers are missing or differ"
                )
            original_output = replace(
                columns[0], dtype=original_output_type, unit=state_unit(leaf.source)
            )
            if tuple(column.dtype for column in baseline_columns) != tuple(
                column.dtype for column in columns
            ):
                _fail(
                    f"occurrence={leaf.occurrence}: endpoint sufficient-state physical types differ"
                )
            if leaf.kind in ("quantile", "distinct"):
                paired_frequency = []
                paired_finishers = []
                for source, rows in ((left, current_rows), (right, baseline_rows)):
                    frequency_capture = next(
                        item
                        for item in source.attribute_frequencies
                        if item.value_slot
                        == dict(program.state_value_slots[0 if source is left else 1])[
                            leaf.slots[0]
                        ]
                    )
                    paired_finishers.append(frequency_capture.finisher)
                    directory = dict(frequency_capture.groups)
                    exemplar = next(iter(directory.values()), None)
                    if exemplar is None:
                        other_source = right if source is left else left
                        other_capture = next(
                            item
                            for item in other_source.attribute_frequencies
                            if item.value_slot
                            == dict(program.state_value_slots[0 if source is left else 1])[
                                leaf.slots[0]
                            ]
                        )
                        exemplar = next((state for _, state in other_capture.groups), None)
                    empty = FrequencyState(
                        exemplar.dtype if exemplar is not None else input_types[0], (), (), 0, 0
                    )
                    captured_states = []
                    for player in players:
                        if player not in rows:
                            captured_states.append(empty)
                        else:
                            row_key = tuple(
                                None
                                if _null(source.table[slot].iloc[rows[player]])
                                else source.table[slot].iloc[rows[player]]
                                for slot in frequency_capture.keys
                            )
                            captured_states.append(directory.get(row_key, empty))
                    paired_frequency.append(tuple(captured_states))
                if paired_finishers[0] != paired_finishers[1]:
                    _fail(
                        f"occurrence={leaf.occurrence}: native endpoint quantile finishing policies differ"
                    )
                states.append(
                    _QuantileState(
                        leaf,
                        columns,
                        FrequencyGame(
                            paired_frequency[1],
                            paired_frequency[0],
                            input_type=input_types[0],
                            finisher=paired_finishers[0],
                        ),
                        input_types,
                    )
                )
                continue

            def cells(
                source: StageOutput,
                schema: object,
                rows: dict[FrozenKey, int],
                player: FrozenKey,
                columns: tuple[DataColumn, ...] = columns,
            ) -> tuple[_Number | None, ...]:
                if player not in rows:
                    return tuple(0 for _ in columns)
                return tuple(
                    None
                    if _null(source.table[column.slot_id].iloc[rows[player]])
                    else _input_number(source.table[column.slot_id].iloc[rows[player]])
                    for column in columns
                )

            states.append(
                _LeafState(
                    leaf,
                    columns,
                    tuple(cells(left, program.left_schema, current_rows, p) for p in players),
                    tuple(
                        cells(right, program.right_schema, baseline_rows, p, baseline_columns)
                        for p in players
                    ),
                    input_types,
                    original_output,
                )
            )
        with _attribute_numeric_context(
            (left, right), operation.games[0].metric, key, parent_columns
        ):
            sampled, proof = _sample(
                operation.games[0],
                tuple(states),
                key,
                players,
                operation.permutations,
                operation.seed,
                operation.games[1],
            )
            current = _series(sampled.current.value, sampled.current.column)
            baseline = _series(sampled.baseline.value, sampled.baseline.column)
            change = _numeric_binary("sub", current, baseline)
            coalitions = sampled.unique_coalitions
            leaf_facts = []
            for state in states:
                leaf = state.leaf
                original_node = leaf.source
                while isinstance(original_node, ContributionFilter):
                    original_node = original_node.operand
                entity = isinstance(original_node, EntityCount)
                if leaf.kind == "quantile":
                    assert leaf.q is not None
                    reduction: FrameShapleyQuantile | FrameShapleyReduction = FrameShapleyQuantile(
                        "quantile",
                        "median"
                        if isinstance(original_node, SourceAggregate)
                        and original_node.reducer == "median"
                        else "quantile",
                        float(leaf.q),
                        state.game.finisher if isinstance(state, _QuantileState) else "rank_bounds",
                    )
                else:
                    reduction = FrameShapleyReduction(
                        "aggregate",
                        "entity_count"
                        if entity
                        else "count_distinct"
                        if leaf.kind == "distinct"
                        else leaf.kind,
                    )
                policy: Literal["error", "skip"] = "error" if leaf.nulls == "error" else "skip"
                dependency_ids = []
                for side, (source, game) in enumerate(
                    zip((left, right), operation.games, strict=True)
                ):
                    original = dict(program.original_value_slots[side])[leaf.occurrence]
                    endpoint_leaf = next(
                        item for item in game.leaves if item.occurrence == leaf.occurrence
                    )
                    state_slots = dict(program.state_value_slots[side])
                    relevant = {original, *(state_slots[slot] for slot in endpoint_leaf.slots)}
                    dependencies = tuple(
                        sorted(
                            capture.stage_id
                            for capture in source.attribute_state_captures
                            if relevant.intersection(capture.value_slots)
                        )
                    )
                    if not dependencies:
                        _fail(
                            f"occurrence={leaf.occurrence}; endpoint={side}: no actual state component capture dependencies"
                        )
                    dependency_ids.append(dependencies)
                leaf_facts.append(
                    FrameShapleyLeaf(
                        leaf.occurrence,
                        dependency_ids[0],
                        dependency_ids[1],
                        reduction,
                        state.input_types,
                        state.column,
                        "rows"
                        if entity
                        else "co_valid_value_weight"
                        if leaf.kind == "weighted_mean"
                        else "non_null_value",
                        "not_applicable" if entity else policy,
                        policy if leaf.kind == "weighted_mean" else "not_applicable",
                    )
                )
            method = FrameShapleyMethod(
                "shapley",
                FrameMethodValue(
                    output_columns[("detail", "current", *target.path)],
                    _number(current.values.iloc[0]),
                ),
                FrameMethodValue(
                    output_columns[("detail", "baseline", *target.path)],
                    _number(baseline.values.iloc[0]),
                ),
                FrameMethodValue(
                    output_columns[("detail", "absolute", *target.path)],
                    _number(change.values.iloc[0]),
                ),
                operation.permutations,
                operation.seed,
                "permutation/v1",
                combined_route,
                sum(capture.state_entries for capture in captured_facts),
                len(players),
                coalitions,
                coalitions,
                sum(capture.source_reads for capture in captured_facts),
                operation.permutations * len(players),
                FrameShapleyGame(
                    "group_replacement",
                    "group_replacement/v1",
                    formula_id(operation.games[0]),
                    formula_id(operation.games[1]),
                    "joint_key_union/v1",
                    "finite_interval_demand/v1",
                    proof,
                    state_metric_approximation(tuple(leaf_facts)),
                    "permutation_mean/v1",
                    "typed_marginal_float64_sum/v1",
                ),
                tuple(leaf_facts),
                tuple(captured_facts),
            )
            completed_games.append(CompletedGame(context, sampled, method))
    return assemble_game_report(program, inputs, tuple(completed_games))


def assemble_game_report(
    program: AttributeGameProgram,
    inputs: tuple[StageOutput, ...],
    completed_games: tuple[CompletedGame, ...],
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    parent, left, right, _, _ = inputs
    operation, target = program.operation, program.operation.target
    parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
    fine_columns = tuple(
        c
        for axis in program.output_schema.axes[len(program.parent_schema.axes) :]
        for c in axis.columns
    )
    output_columns = dict(
        zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
    )
    result_keys: list[FrozenKey] = []
    records: list[dict[str, object]] = []
    parents = []
    mappings = (
        FrameMethodOutput("contribution", ("contribution", *target.path)),
        FrameMethodOutput("total", ("total", *target.path)),
        FrameMethodOutput("observed_total", ("observed_total", *target.path)),
        FrameMethodOutput("share", ("contribution_share", *target.path)),
        FrameMethodOutput("contribution_std_error", ("contribution_std_error", *target.path)),
        FrameMethodOutput("current", ("detail", "current", *target.path)),
        FrameMethodOutput("baseline", ("detail", "baseline", *target.path)),
        FrameMethodOutput("absolute", ("detail", "absolute", *target.path)),
        FrameMethodOutput("current_present", ("detail", "current", "present", *target.path)),
        FrameMethodOutput("baseline_present", ("detail", "baseline", "present", *target.path)),
    )
    if tuple(item.context for item in completed_games) != game_contexts(program, inputs):
        _fail("completed samples must retain the exact frozen parent/player directory")
    for completed in completed_games:
        context, sampled = completed.context, completed.sample
        key, players, parent_index = context.parent, context.players, context.parent_index
        current_rows, baseline_rows = dict(context.current_rows), dict(context.baseline_rows)
        with _attribute_numeric_context(
            (left, right), operation.games[0].metric, key, parent_columns
        ):
            means, errors = sampled.means, sampled.errors
            current = _series(sampled.current.value, sampled.current.column)
            baseline = _series(sampled.baseline.value, sampled.baseline.column)
            closure_budget = sampled.closure_budget
            change = _numeric_binary("sub", current, baseline)
            total = change
            if target.mode == "relative":
                denominator = _number(abs(_number(baseline.values.iloc[0])))
                if denominator == 0:
                    _fail(
                        f"{'.'.join(target.path)}: baseline=0; relative attribution requires nonzero baseline; check the baseline window and source retention/filter conditions or select absolute change",
                        metric=operation.games[0].metric,
                        parent=key,
                    )
                total = _numeric_binary("div", change, _series(denominator, baseline.column))
                relative_roundoff = tuple(
                    math.ulp(v / float(denominator))
                    + abs(v / float(denominator))
                    * (math.ulp(float(denominator)) / float(denominator))
                    for v in means
                )
                closure_budget = (
                    closure_budget / float(denominator) + _scaled_sum(relative_roundoff)[0]
                )
                means, errors = (
                    tuple(v / float(denominator) for v in means),
                    tuple(v / float(denominator) for v in errors),
                )
                if not all(math.isfinite(v) for v in (*means, *errors, closure_budget)):
                    numeric_failure(
                        "finite Float64 relative contributions, standard errors and roundoff",
                        f"baseline magnitude={denominator!r}",
                    )
            total_column = output_columns[("total", *target.path)]
            observed_column = output_columns[("observed_total", *target.path)]
            observed = _number(
                parent.table[program.parent_schema.values[target.observed].slot_id].iloc[
                    parent_index
                ]
            )
            total = _report_value(total, total_column)
            total_value = _number(total.values.iloc[0])
            original_observed = program.parent_schema.values[target.observed]
            observed = _number(
                _report_value(_series(observed, original_observed), observed_column).values.iloc[0]
            )
            typed_total = FrameMethodValue(total_column, total_value)
            metric = FrameAttributeMetric(
                target.path,
                target.mode,
                completed.method,
                FrameMethodValue(observed_column, observed),
                typed_total,
                mappings,
                "zero_total" if total_value == 0 else "defined",
            )
            parents.append(
                FrameMethodParent(
                    tuple(
                        FrameMethodValue(column, value)
                        for column, (_, value) in zip(parent_columns, key, strict=True)
                    ),
                    (metric,),
                )
            )
            contribution_sum, sum_roundoff = _scaled_sum(means)
            closure_budget, budget_roundoff = _scaled_sum(
                (closure_budget, math.ulp(float(total_value)), sum_roundoff)
            )
            closure_budget += budget_roundoff
            if isinstance(total.column.dtype, DecimalDataType):
                closure_budget += float(Decimal(1).scaleb(-total.column.dtype.scale)) / 2
            if not math.isfinite(closure_budget):
                numeric_failure("a finite Float64 closure roundoff budget", repr(closure_budget))
            if abs(contribution_sum - float(total_value)) > closure_budget:
                _fail(
                    f"parent={key!r}: sampled contributions do not close to the cached endpoint change"
                )
            for index, player in enumerate(players):
                result_keys.append((*key, *player))
                record: dict[str, object] = {}
                for prefix, value in (
                    (("contribution",), means[index]),
                    (("total",), total_value),
                    (("observed_total",), observed),
                    (
                        ("contribution_share",),
                        None if total_value == 0 else means[index] / float(total_value),
                    ),
                    (("contribution_std_error",), errors[index]),
                ):
                    record[output_columns[(*prefix, *target.path)].slot_id] = value
                values = []
                for source, schema, rows in (
                    (left, program.left_schema, current_rows),
                    (right, program.right_schema, baseline_rows),
                ):
                    values.append(
                        None
                        if player not in rows
                        else source.table[
                            schema.values[
                                operation.games[0 if source is left else 1].target_slot
                            ].slot_id
                        ].iloc[rows[player]]
                    )
                for detail_prefix, value in zip(
                    (("detail", "current"), ("detail", "baseline"), ("detail", "absolute")),
                    (
                        *values,
                        None
                        if any(_null(v) for v in values)
                        else _number(
                            _numeric_binary(
                                "sub",
                                _series(
                                    _number(values[0]),
                                    program.left_schema.values[operation.games[0].target_slot],
                                ),
                                _series(
                                    _number(values[1]),
                                    program.right_schema.values[operation.games[1].target_slot],
                                ),
                            ).values.iloc[0]
                        ),
                    ),
                    strict=True,
                ):
                    record[output_columns[(*detail_prefix, *target.path)].slot_id] = value
                for column, present in zip(
                    program.output_schema.presence,
                    (player in current_rows, player in baseline_rows),
                    strict=True,
                ):
                    record[column.slot_id] = present
                records.append(record)
    result = frozen_key_table((*parent_columns, *fine_columns), tuple(result_keys))
    for column in (*program.output_schema.values, *program.output_schema.presence):
        result[column.slot_id] = pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array(
                    [
                        None if _null(record[column.slot_id]) else record[column.slot_id]
                        for record in records
                    ],
                    type=stage_arrow_type(column.dtype),
                )
            )
        )
    coordinates = tuple(
        FrameMethodCoordinate(
            a.name,
            a.schema,
            tuple(tuple(v for _, v in key) for key in frozen_key_rows(a.rows, a.schema.columns)),
        )
        for a in sorted(parent.frame_attachments, key=lambda a: a.name)
    )
    payload = AttributeMethodPayload(
        program.output_schema,
        len(program.parent_schema.axes),
        tuple(sorted(parents, key=_key_order)),
        coordinates,
        (FrameMethodTarget(target.path, target.mode, mappings),),
        parent_schema=program.parent_schema,
    )
    return result, payload
