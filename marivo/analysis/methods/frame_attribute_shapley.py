"""Whole-Metric replacement sampling and pure typed report assembly."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Literal, TypeAlias

import ibis
import ibis.expr.datatypes as dt
import pandas as pd

from marivo._definition.expressions import ContributionFilter, EntityCount, SourceAggregate
from marivo.analysis.binding.attribute_game import AttributeGame, GameLeaf, formula_id, state_unit
from marivo.analysis.core.attribute import AttributeMethodPayload
from marivo.analysis.core.attribute_game import (
    AttributeGameProgram,
    CoalitionValue,
    CompletedGame,
    GameContext,
    GameSample,
)
from marivo.analysis.core.frame_method_facts import (
    FrameShapleyMethod,
)
from marivo.analysis.core.frame_shapley_facts import (
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyQuantile,
    FrameShapleyReduction,
    state_metric_approximation,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.attribute_game_diagnostics import attribute_numeric_context
from marivo.analysis.methods.attribute_game_domain import Domain, DomainProofError, prove_domain
from marivo.analysis.methods.attribute_game_sampling import (
    _fail,
    _input_number,
    _null,
    _number,
    _series,
    game_contexts,
    sample_game,
)
from marivo.analysis.methods.attribute_numeric import _numeric_binary, _Value
from marivo.analysis.methods.attribute_quantiles import FrequencyGame
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_game_report import assemble_game_report
from marivo.analysis.model.data_types import DataColumn, DataType, DecimalDataType, ScalarDataType
from marivo.analysis.model.frozen_keys import FrozenKey
from marivo.analysis.model.kernels import FrequencyState
from marivo.analysis.model.method_values import FrameMethodValue
from marivo.semantic.spi import (
    NumericCheck,
    NumericValue,
    lower_metric_formula,
    numeric_binary,
)

_Number: TypeAlias = int | float | Decimal


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

    def invalid_count_domain(self) -> Domain:
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

    def value_domain(self) -> Domain:
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

    def invalid_count_domain(self) -> Domain:
        pairs = [
            (a.null_count, b.null_count)
            for a, b in zip(self.game.baseline, self.game.current, strict=True)
        ]
        return Domain(
            Fraction(sum(min(pair) for pair in pairs)), Fraction(sum(max(pair) for pair in pairs))
        )

    def value_domain(self) -> Domain:
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


def _sample(
    game: AttributeGame,
    states: tuple[_LeafState | _QuantileState, ...],
    parent: FrozenKey,
    players: tuple[FrozenKey, ...],
    permutations: int,
    seed: int,
    paired_game: AttributeGame | None = None,
) -> tuple[GameSample, tuple[str, ...]]:
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
            fields[f"leaf_{alias}"] = state.value_domain()
            fields[f"invalid_{alias}"] = state.invalid_count_domain()
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


_FixedStateRoute: TypeAlias = Literal[
    "typed_sufficient_state/v1", "typed_value_frequency/v1", "typed_sufficient_frequency/v1"
]


def _capture_facts(
    left: StageOutput, right: StageOutput
) -> tuple[tuple[FrameShapleyCapture, ...], _FixedStateRoute]:
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
    combined_route: _FixedStateRoute = (
        next(iter(routes)) if len(routes) == 1 else "typed_sufficient_frequency/v1"
    )
    return tuple(captured_facts), combined_route


def _frequency_state(
    program: AttributeGameProgram,
    context: GameContext,
    left: StageOutput,
    right: StageOutput,
    leaf: GameLeaf,
    columns: tuple[DataColumn, ...],
    input_types: tuple[DataType, ...],
) -> _QuantileState:
    current_rows, baseline_rows = dict(context.current_rows), dict(context.baseline_rows)
    players = context.players
    paired_frequency = []
    paired_finishers = []
    for source, rows in ((left, current_rows), (right, baseline_rows)):
        frequency_capture = next(
            item
            for item in source.attribute_frequencies
            if item.value_slot
            == dict(program.state_value_slots[0 if source is left else 1])[leaf.slots[0]]
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
                == dict(program.state_value_slots[0 if source is left else 1])[leaf.slots[0]]
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
        _fail(f"occurrence={leaf.occurrence}: native endpoint quantile finishing policies differ")
    return _QuantileState(
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


def _state_cells(
    source: StageOutput,
    rows: dict[FrozenKey, int],
    player: FrozenKey,
    columns: tuple[DataColumn, ...],
) -> tuple[_Number | None, ...]:
    if player not in rows:
        return tuple(0 for _ in columns)
    return tuple(
        None
        if _null(source.table[column.slot_id].iloc[rows[player]])
        else _input_number(source.table[column.slot_id].iloc[rows[player]])
        for column in columns
    )


def _fixed_game_states(
    program: AttributeGameProgram,
    context: GameContext,
    left: StageOutput,
    right: StageOutput,
    input_type_maps: tuple[dict[str, tuple[DataType, ...]], ...],
    output_type_maps: tuple[dict[str, DataType], ...],
) -> tuple[_LeafState | _QuantileState, ...]:
    operation = program.operation
    players = context.players
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
        baseline_columns = tuple(program.right_schema.values[slot] for slot in baseline_leaf.slots)
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
            _fail(f"occurrence={leaf.occurrence}: endpoint sufficient-state physical types differ")
        if leaf.kind in ("quantile", "distinct"):
            states.append(
                _frequency_state(program, context, left, right, leaf, columns, input_types)
            )
            continue

        states.append(
            _LeafState(
                leaf,
                columns,
                tuple(_state_cells(left, current_rows, p, columns) for p in players),
                tuple(_state_cells(right, baseline_rows, p, baseline_columns) for p in players),
                input_types,
                original_output,
            )
        )
    return tuple(states)


def _fixed_leaf_facts(
    program: AttributeGameProgram,
    left: StageOutput,
    right: StageOutput,
    states: tuple[_LeafState | _QuantileState, ...],
) -> tuple[FrameShapleyLeaf, ...]:
    operation = program.operation
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
                if isinstance(original_node, SourceAggregate) and original_node.reducer == "median"
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
        for side, (source, game) in enumerate(zip((left, right), operation.games, strict=True)):
            original = dict(program.original_value_slots[side])[leaf.occurrence]
            endpoint_leaf = next(item for item in game.leaves if item.occurrence == leaf.occurrence)
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
    return tuple(leaf_facts)


def attribute_game_data(
    program: AttributeGameProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    _, left, right, _, _ = inputs
    operation, target = program.operation, program.operation.target
    parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
    output_columns = dict(
        zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
    )
    captured_facts, combined_route = _capture_facts(left, right)
    input_type_maps: tuple[dict[str, tuple[DataType, ...]], ...] = tuple(
        {
            slot: types
            for capture in source.attribute_state_captures
            for slot, types in capture.input_types
        }
        for source in (left, right)
    )
    output_type_maps: tuple[dict[str, DataType], ...] = tuple(
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
        states = _fixed_game_states(
            program, context, left, right, input_type_maps, output_type_maps
        )
        with attribute_numeric_context(
            (left, right), operation.games[0].metric, key, parent_columns
        ):
            sampled, proof = _sample(
                operation.games[0],
                states,
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
            leaf_facts = _fixed_leaf_facts(program, left, right, states)
            current_path = ("detail", "current", *target.path)
            baseline_path = ("detail", "baseline", *target.path)
            absolute_path = ("detail", "absolute", *target.path)
            method = FrameShapleyMethod(
                "shapley",
                FrameMethodValue(
                    output_columns[current_path],
                    _number(current.values.iloc[0]),
                ),
                FrameMethodValue(
                    output_columns[baseline_path],
                    _number(baseline.values.iloc[0]),
                ),
                FrameMethodValue(
                    output_columns[absolute_path],
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
                    state_metric_approximation(leaf_facts),
                    "permutation_mean/v1",
                    "typed_marginal_float64_sum/v1",
                ),
                leaf_facts,
                captured_facts,
            )
            completed_games.append(CompletedGame(context, sampled, method))
    return assemble_game_report(program, inputs, tuple(completed_games))
