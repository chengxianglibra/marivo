"""Check bounded-depth authored selection trees against the emitted schedule."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import marivo.analysis as mv
from marivo.analysis.compiler.frame_filter_fusion import fuse_frame_filters
from marivo.analysis.compiler.optimization import optimize_query
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


Predicate: TypeAlias = Comparison | Junction
_OPERATORS: tuple[ComparisonOperator, ...] = ("eq", "ne", "lt", "le", "gt", "ge")
_CARRIERS: tuple[Carrier, ...] = ("x", "exact", "coordinate")
_BOUNDARIES = (_I64_MIN, -1, 0, 1, _I64_MAX)
_SHALLOW = st.builds(
    Comparison,
    st.sampled_from(_OPERATORS),
    st.one_of(st.sampled_from(_BOUNDARIES), st.sampled_from(_CARRIERS)),
)


def predicate_at_depth(depth: int) -> st.SearchStrategy[Predicate]:
    if depth == 1:
        return _SHALLOW
    inner = predicate_at_depth(depth - 1)
    joins: tuple[Literal["and", "or"], ...] = ("and", "or")
    return st.one_of(_SHALLOW, st.builds(Junction, st.sampled_from(joins), inner, inner))


def _author(frame: mv.AnalysisFrame, carrier: Carrier, predicate: Predicate) -> mv.Column:
    if isinstance(predicate, Junction):
        left = _author(frame, carrier, predicate.left)
        right = _author(frame, carrier, predicate.right)
        return left & right if predicate.operation == "and" else left | right
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
@pytest.mark.parametrize("length", (2, 3))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(predicates=st.tuples(_PREDICATE, _PREDICATE, _PREDICATE))
@example(predicates=(_DEPTH_FOUR, Comparison("ge", _I64_MAX), _LEAF))
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


@pytest.mark.parametrize(
    "problem, message",
    (
        ("missing", "missing right operand exact"),
        ("duplicate", "Duplicate right projection columns"),
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
    assert fusion is not None and "exact" in fusion.second_columns
    columns = (
        tuple(name for name in fusion.second_columns if name != "exact")
        if problem == "missing"
        else (*fusion.second_columns, "exact")
    )
    altered = replace(stage, filter_fusion=replace(fusion, second_columns=columns))
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
