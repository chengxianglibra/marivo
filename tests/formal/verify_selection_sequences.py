"""Check bounded-depth authored selection trees against the emitted schedule."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import marivo.analysis as mv
from marivo._definition.query import FilterValues, ValueBinary, ValueUnary
from marivo.analysis.compiler.frame_filter_fusion import fuse_frame_filters
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_program import ResultProgram
from tests.formal._l1_validator import (
    frame_chain,
    result_plan,
    validate_short_circuit,
    validate_total_selection,
)
from tests.formal._smt import prove_unsat
from tests.formal.verify_query_rewrites import (
    _I64_MAX,
    _I64_MIN,
    _compile_fixed_frame,
    _filters,
    _frame_filter,
    _selected_operand,
)
from tests.formal.verify_query_rewrites import filter_frame as filter_frame

Carrier: TypeAlias = Literal["x", "exact", "coordinate"]
ComparisonOperator: TypeAlias = Literal["eq", "ne", "lt", "le", "gt", "ge"]


@dataclass(frozen=True)
class Comparison:
    operation: ComparisonOperator
    operand: int | Carrier


@dataclass(frozen=True)
class Junction:
    operation: Literal["and", "or"]
    left: Predicate
    right: Predicate


@dataclass(frozen=True)
class Missing:
    carrier: Carrier


@dataclass(frozen=True)
class Negated:
    predicate: Predicate


Predicate: TypeAlias = Comparison | Junction | Missing | Negated
_OPERATORS: tuple[ComparisonOperator, ...] = ("eq", "ne", "lt", "le", "gt", "ge")
_CARRIERS: tuple[Carrier, ...] = ("x", "exact", "coordinate")
_BOUNDARIES = (_I64_MIN, -1, 0, 1, _I64_MAX)
_SHALLOW = st.builds(
    Comparison,
    st.sampled_from(_OPERATORS),
    st.one_of(st.sampled_from(_BOUNDARIES), st.sampled_from(_CARRIERS)),
)


def predicate_at_depth(depth: int) -> st.SearchStrategy[Predicate]:
    leaf = st.one_of(_SHALLOW, st.builds(Missing, st.sampled_from(_CARRIERS)))
    if depth == 1:
        return leaf
    inner = predicate_at_depth(depth - 1)
    joins: tuple[Literal["and", "or"], ...] = ("and", "or")
    return st.one_of(
        leaf, st.builds(Junction, st.sampled_from(joins), inner, inner), st.builds(Negated, inner)
    )


def _author(frame: mv.AnalysisFrame, carrier: Carrier, predicate: Predicate) -> mv.Column:
    if isinstance(predicate, Junction):
        left = _author(frame, carrier, predicate.left)
        right = _author(frame, carrier, predicate.right)
        return left & right if predicate.operation == "and" else left | right
    if isinstance(predicate, Missing):
        return _selected_operand(frame, predicate.carrier).is_null()
    if isinstance(predicate, Negated):
        return ~_author(frame, carrier, predicate.predicate)
    value = _selected_operand(frame, carrier)
    target = (
        predicate.operand
        if isinstance(predicate.operand, int)
        else _selected_operand(frame, predicate.operand)
    )
    operators = {
        "eq": mv.Column.__eq__,
        "ne": mv.Column.__ne__,
        "lt": mv.Column.__lt__,
        "le": mv.Column.__le__,
        "gt": mv.Column.__gt__,
        "ge": mv.Column.__ge__,
    }
    return operators[predicate.operation](value, target)


_LEAF = Comparison("le", 0)
_DEPTH_FOUR = Junction(
    "and",
    Junction("or", _LEAF, Junction("and", Comparison("eq", "coordinate"), Comparison("ne", -1))),
    Junction("or", Comparison("lt", "exact"), Comparison("gt", _I64_MIN)),
)
_PREDICATE = predicate_at_depth(4)


@pytest.mark.parametrize("carrier", _CARRIERS)
@pytest.mark.parametrize("length", (2, 3, 4))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(predicates=st.tuples(_PREDICATE, _PREDICATE, _PREDICATE, _PREDICATE))
@example(predicates=(_DEPTH_FOUR, Comparison("ge", _I64_MAX), _LEAF, Comparison("ne", -1)))
def test_actual_emitted_selection_sequence(
    filter_frame: mv.AnalysisFrame,
    carrier: Carrier,
    length: int,
    predicates: tuple[Predicate, ...],
) -> None:
    # The fixture is a fixed, immutable Frame reused across generated examples.
    query = filter_frame
    for predicate in predicates[:length]:
        query = query.where(_author(query, carrier, predicate))
    original = _compile_fixed_frame(query)
    rewritten = fuse_frame_filters(original)
    validate_short_circuit(original.lowered.plan, rewritten.lowered.plan)

    steps = tuple(_frame_filter(stage) for stage in frame_chain(original.lowered.plan))
    schema_program, _ = steps[0]
    expressions = tuple(expression for _, expression in steps)
    total = result_plan(schema_program, expressions)
    swapped = result_plan(schema_program, expressions[::-1])
    validate_total_selection(schema_program, total, optimize_query(total), optimize_query(swapped))


def test_emitted_short_circuit_preserves_fix_and_presence_masks(
    filter_frame: mv.AnalysisFrame,
) -> None:
    left = filter_frame.where(filter_frame["x"] >= 0).compute()
    right = filter_frame.where(filter_frame["y"] <= 0).compute()
    aligned = left.align(right).compute()
    first = aligned.where(aligned["left_present"])
    fixed = first.fix(first.axes[0], _I64_MAX)
    third = fixed.where(~fixed["right_present"])
    query = third.where(~third["left.x"].is_null())
    original = _compile_fixed_frame(query)
    rewritten = fuse_frame_filters(original)
    validate_short_circuit(original.lowered.plan, rewritten.lowered.plan)


@pytest.mark.parametrize(
    "problem, message",
    (
        ("missing", "missing operand exact"),
        ("duplicate", "Duplicate projection columns"),
    ),
)
def test_validator_rejects_invalid_deep_right_projection(
    filter_frame: mv.AnalysisFrame, problem: str, message: str
) -> None:
    first = filter_frame.where(filter_frame["x"] >= 0)
    query = first.where(_author(first, "x", _DEPTH_FOUR))
    compiled = _compile_fixed_frame(query)
    original = compiled.lowered.plan
    plan = fuse_frame_filters(compiled).lowered.plan
    (stage,) = frame_chain(plan)
    fusion = stage.filter_fusion
    assert fusion is not None
    first_step, second_step = fusion.steps
    assert "exact" in second_step.columns
    columns = (
        tuple(name for name in second_step.columns if name != "exact")
        if problem == "missing"
        else (*second_step.columns, "exact")
    )
    altered = replace(
        stage,
        filter_fusion=replace(fusion, steps=(first_step, replace(second_step, columns=columns))),
    )
    mutant = replace(
        plan,
        stages=tuple(
            altered if item.stage_id == altered.stage_id else item for item in plan.stages
        ),
    )
    with pytest.raises(AssertionError, match=message):
        validate_short_circuit(original, mutant)


def test_partial_predicates_cannot_be_exchanged(filter_frame: mv.AnalysisFrame) -> None:
    # On a rejected row, running the guarded predicate first can introduce an error.
    with pytest.raises(AssertionError, match="counterexample"):
        prove_unsat(
            """(set-logic QF_UF)
(declare-const admitted Bool)
(declare-const bad Bool)
(define-fun sequential_error () Bool (and admitted bad))
(define-fun swapped_error () Bool bad)
(assert (distinct sequential_error swapped_error))""",
            claim="Non-total selection exchange",
            witness=("admitted", "bad", "sequential_error", "swapped_error"),
        )

    for swap in (False, True):
        frame = filter_frame
        for partial in (swap, not swap):
            predicate = (frame["x"] + 1) > 0 if partial else frame["y"] < 0
            frame = frame.where(predicate)
        compiled = _compile_fixed_frame(frame)
        after = fuse_frame_filters(compiled).lowered.plan
        assert all(stage.filter_fusion is None for stage in frame_chain(after))
        steps = tuple(_frame_filter(stage) for stage in frame_chain(after))
        direct = result_plan(steps[0][0], tuple(expression for _, expression in steps))
        optimized = optimize_query(direct)
        assert not optimized.fusion_groups
        assert any(
            decision.rule == "L1" and decision.status == "refused"
            for decision in optimized.optimization_decisions
        )


def test_emitted_short_circuit_distinguishes_valid_nan_and_missing(
    filter_frame: mv.AnalysisFrame,
) -> None:
    first = filter_frame.where(~(filter_frame["floating"] < 0))
    query = first.where(~first["floating"].is_null())
    original = _compile_fixed_frame(query)
    validate_short_circuit(original.lowered.plan, fuse_frame_filters(original).lowered.plan)


def test_total_selection_validator_rejects_negation_that_drops_nan(
    filter_frame: mv.AnalysisFrame,
) -> None:
    first = filter_frame.where(~(filter_frame["floating"] < 0))
    query = first.where(~first["floating"].is_null())
    frame_plan = _compile_fixed_frame(query).lowered.plan
    (program, negated), (_, second) = tuple(
        _frame_filter(stage) for stage in frame_chain(frame_plan)
    )
    assert isinstance(negated, ValueUnary) and isinstance(negated.operand, ValueBinary)
    original = result_plan(program, (negated, second))
    reversed_plan = optimize_query(result_plan(program, (second, negated)))
    fused = optimize_query(original)
    validate_total_selection(program, original, fused, reversed_plan)
    (stage,) = _filters(fused)
    assert isinstance(stage.program, ResultProgram)
    wrong = ValueBinary("ge", negated.operand.left, negated.operand.right)
    mutant = replace(
        stage,
        program=replace(stage.program, operation=FilterValues(ValueBinary("and", wrong, second))),
    )
    mutated_plan = replace(
        fused, stages=tuple(mutant if item is stage else item for item in fused.stages)
    )
    with pytest.raises(AssertionError, match="counterexample"):
        validate_total_selection(program, original, mutated_plan, reversed_plan)


def test_short_circuit_validator_rejects_removal_of_fractional_float_rows(
    filter_frame: mv.AnalysisFrame,
) -> None:
    floating = filter_frame["floating"]
    first = filter_frame.where((floating > 0) & (floating < 1))
    query = first.where(~first["floating"].is_null())
    compiled = _compile_fixed_frame(query)
    fused = fuse_frame_filters(compiled).lowered.plan
    validate_short_circuit(compiled.lowered.plan, fused)
    (stage,) = frame_chain(fused)
    fusion = stage.filter_fusion
    assert fusion is not None
    left, right = fusion.steps
    assert isinstance(left.program.operation, FilterValues)
    operand = left.program.operation.predicate
    assert isinstance(operand, ValueBinary) and isinstance(operand.left, ValueBinary)
    never = ValueBinary("lt", operand.left.left, operand.left.left)
    wrong = replace(left, program=replace(left.program, operation=FilterValues(never)))
    mutant = replace(stage, filter_fusion=replace(fusion, steps=(wrong, right)))
    mutated_plan = replace(
        fused, stages=tuple(mutant if item is stage else item for item in fused.stages)
    )
    with pytest.raises(AssertionError, match="counterexample"):
        validate_short_circuit(compiled.lowered.plan, mutated_plan)
