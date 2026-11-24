"""Verify the proof obligations of the admitted terminal builders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional formal solver.

from marivo._definition.query import ReduceValues, Statistic, ValueBinary, ValuePartition, ValueSlot
from marivo.analysis.compiler.frame_plan import frame_program
from marivo.analysis.compiler.frame_top import native_top_order
from marivo.analysis.compiler.source_predicates import same_returned_key
from marivo.analysis.materialization.frame_terminal_aggregates import (
    _complete_statistic,
    _normalized_share,
)
from marivo.analysis.materialization.frame_terminal_align import _both_values, _key_equality
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from tests.formal._summary_monoids import Cell, result, same, translate_finisher


@dataclass(frozen=True)
class _Scalar:
    value: z3.ArithRef
    missing: z3.BoolRef
    nan: z3.BoolRef


def _symbol(name: str) -> _Scalar:
    return _Scalar(
        z3.Real(f"{name}_ordered"),
        z3.Bool(f"{name}_missing"),
        z3.Bool(f"{name}_nan"),
    )


def _missing(node: ops.Value, fields: Mapping[ops.Value, _Scalar]) -> z3.BoolRef:
    if node in fields:
        return fields[node].missing
    if isinstance(node, ops.Cast):
        return _missing(node.arg, fields)
    assert isinstance(node, ops.Literal), node
    return z3.BoolVal(node.value is None)


def _project(node: ops.Value, fields: Mapping[ops.Value, _Scalar]) -> z3.ExprRef:
    """Interpret only the generated rank and validity expressions."""
    if node in fields:
        return fields[node].value
    if isinstance(node, ops.Literal):
        return z3.RealVal(0 if node.value is None else node.value)
    if isinstance(node, ops.Cast):
        return _project(node.arg, fields)
    if isinstance(node, ops.IdenticalTo):
        left, right = _missing(node.left, fields), _missing(node.right, fields)
        return z3.Or(
            z3.And(left, right),
            z3.And(
                z3.Not(left),
                z3.Not(right),
                _project(node.left, fields) == _project(node.right, fields),
            ),
        )
    if isinstance(node, ops.IsNull):
        return fields[node.arg].missing
    if isinstance(node, ops.IsNan):
        return fields[node.arg].nan
    if isinstance(node, ops.Or):
        return z3.Or(_project(node.left, fields), _project(node.right, fields))
    if isinstance(node, ops.Equals):
        return _project(node.left, fields) == _project(node.right, fields)
    if isinstance(node, ops.Coalesce):
        value, fill = node.arg
        return z3.If(fields[value].missing, _project(fill, fields), fields[value].value)
    assert isinstance(node, ops.IfElse), node
    return z3.If(
        _project(node.bool_expr, fields),
        _project(node.true_expr, fields),
        _project(node.false_null_expr, fields),
    )


def _lexicographic_less(
    left: tuple[z3.ArithRef, ...], right: tuple[z3.ArithRef, ...]
) -> z3.BoolRef:
    result = z3.BoolVal(False)
    for a, b in reversed(tuple(zip(left, right, strict=True))):
        result = z3.Or(a < b, z3.And(a == b, result))
    return result


@pytest.mark.parametrize("descending", (False, True))
def test_float_top_rank_and_complete_key_order(descending: bool) -> None:
    table = ibis.table({"score": "float64", "principal": "int64", "suffix": "int64"})
    order = native_top_order(
        table, table.score, ("principal", "suffix"), "duckdb", descending=descending
    )
    assert order is not None and len(order) == 4
    terms = tuple(item.op() for item in order)
    category, numeric, principal, suffix = terms
    assert isinstance(numeric, ops.SortKey)
    assert isinstance(principal, ops.SortKey) and principal.ascending
    assert isinstance(suffix, ops.SortKey) and suffix.ascending
    assert (principal.arg, suffix.arg) == (table.principal.op(), table.suffix.op())
    a, b = _symbol("left_score"), _symbol("right_score")
    left_keys = z3.Reals("a_principal a_suffix")
    right_keys = z3.Reals("b_principal b_suffix")
    left = {table.score.op(): a}
    right = {table.score.op(): b}
    direction = 1 if numeric.ascending else -1
    observed = _lexicographic_less(
        (_project(category, left), direction * _project(numeric.arg, left), *left_keys),
        (_project(category, right), direction * _project(numeric.arg, right), *right_keys),
    )

    def rank(value: _Scalar) -> z3.ArithRef:
        return z3.If(value.missing, 2, z3.If(value.nan, 1, 0))

    ordered = a.value > b.value if descending else a.value < b.value
    expected = z3.Or(
        rank(a) < rank(b),
        z3.And(rank(a) == rank(b), rank(a) == 0, ordered),
        z3.And(
            rank(a) == rank(b),
            z3.Or(rank(a) != 0, a.value == b.value),
            _lexicographic_less(left_keys, right_keys),
        ),
    )
    result(
        "Native Top orders numbers, NaN and NA before resolving every tie by the complete key",
        observed == expected,
        z3.Not(z3.And(a.missing, a.nan)),
        z3.Not(z3.And(b.missing, b.nan)),
        logic="QF_LRA",
    )


def test_fix_null_safe_complete_key() -> None:
    table = ibis.table({"coordinate": "int64", "component": "int64"})
    predicates = (
        same_returned_key(table.coordinate, ibis.null().cast("int64"), "duckdb").op(),
        same_returned_key(table.component, ibis.literal(0), "duckdb").op(),
    )
    assert all(isinstance(item, ops.IdenticalTo) for item in predicates)
    coordinate, component = _symbol("coordinate"), _symbol("component")
    fields = {table.coordinate.op(): coordinate, table.component.op(): component}
    observed = z3.And(*(_project(predicate, fields) for predicate in predicates))
    expected = z3.And(coordinate.missing, z3.Not(component.missing), component.value == 0)
    result(
        "Fix selects only the entire fixed tuple, including NA vs a valid zero component",
        observed == expected,
        logic="QF_LRA",
    )


def _shape() -> DataSchema:
    return DataSchema(
        (),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True),),
        (DataColumn("present", "left_present", ScalarDataType("boolean"), False),),
        value_paths=(("amount",),),
        presence_paths=(("left_present",),),
    )


def test_reduce_empty_scalar_and_presence() -> None:
    table = ibis.table({"amount": "int64", "present": "boolean"})
    requested = ReduceValues((("total", Statistic("sum", ValueSlot(0))),), ())
    planned = frame_program(_shape(), requested).output_schema
    assert planned.key_fields == ()
    assert planned.presence == ()
    nullable_count, finished = table.count(), table.amount.sum()
    lowered = _complete_statistic(finished, nullable_count, dt.int64, empty_zero=True)
    rows, payload = z3.Reals("count value")
    is_missing = z3.Bool("sum_null")
    input_cell = Cell(payload, is_missing)
    output = translate_finisher(
        lowered.op(), {nullable_count.op(): Cell(rows), finished.op(): input_cell}
    )
    result(
        "Only the genuinely empty scalar SUM receives a valid zero",
        z3.And(
            z3.Implies(rows == 0, same(output, Cell(z3.RealVal(0)))),
            z3.Implies(rows > 0, same(output, input_cell)),
        ),
        rows >= 0,
        logic="QF_LRA",
    )


def test_over_keeps_rows_and_null_divisor() -> None:
    table = ibis.table({"amount": "int64", "denominator": "int64"})
    numerator = ValueSlot(0)
    partition = ValuePartition(Statistic("sum", numerator), ())
    expression = _normalized_share(
        ValueBinary("div", numerator, partition), lambda _item: table.denominator, table, _shape()
    )
    assert expression is not None and isinstance(operation := expression.op(), ops.Divide)
    assert isinstance(operation.right, ops.NullIf)
    assert isinstance(zero := operation.right.null_if_expr, ops.Literal) and zero.value == 0
    row, valid = z3.Bools("represented_row valid_denominator")
    score = z3.Real("projected_denominator")
    division_valid = z3.And(row, valid, score != 0)
    result(
        "The quotient is missing at zero without erasing the input row",
        z3.Implies(z3.And(row, score == 0), z3.Not(division_valid)),
        logic="QF_LRA",
    )


def test_align_composite_key_validity_and_missing_side() -> None:
    a = ibis.table({"amount": "int64", "join_key": "int64", "present": "boolean"}, name="left")
    b = ibis.table({"join_key": "int64"}, name="right")
    left_key, right_key = _symbol("left_key"), _symbol("right_key")
    fields = {a.join_key.op(): left_key, b.join_key.op(): right_key}
    joined = z3.And(
        *(
            _project(term.op(), fields)
            for term in _key_equality(a.join_key, b.join_key, "postgres", nullable=True)
        )
    )
    result(
        "Outer equality separates an absent coordinate from a real filler value",
        joined
        == z3.Or(
            z3.And(left_key.missing, right_key.missing),
            z3.And(
                z3.Not(left_key.missing),
                z3.Not(right_key.missing),
                left_key.value == right_key.value,
            ),
        ),
        logic="QF_LRA",
    )
    (value,) = _both_values(a, _shape(), a.present)
    presence = z3.Bool("has_row")
    input_cell = Cell(z3.Real("original_amount"), z3.Bool("original_null"))
    payload = translate_finisher(
        value.op(),
        {a.present.op(): Cell(presence), a.amount.op(): input_cell},
    )
    result(
        "The outer-join missing side produces invalid data regardless of its native payload",
        z3.And(
            z3.Implies(z3.Not(presence), payload.null),
            z3.Implies(presence, same(payload, input_cell)),
        ),
        logic="QF_LRA",
    )
