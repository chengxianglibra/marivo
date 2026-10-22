"""Shared nullable Ibis lowering for current Row and Metric definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.exceptions import IbisTypeError

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import ExactRef
from marivo._numeric_types import bind_numeric_literal, decimal_result_type
from marivo.refs import (
    SemanticKind,
)


@dataclass(frozen=True, slots=True, eq=False)
class NumericCheck:
    """A temporary boolean violation expression, consumed before publication."""

    violation: ir.BooleanValue
    expected: str
    received: str


@dataclass(frozen=True, slots=True, eq=False)
class NumericValue:
    value: ir.Value
    checks: tuple[NumericCheck, ...] = ()


def numeric_failure(expected: str, received: str) -> NoReturn:
    from marivo.semantic.errors import ErrorKind, SemanticExpressionError

    action = "Repair the input values to satisfy the expected numeric/type constraints, then retry."
    if "non-NA" in expected:
        action = "Supply non-NA inputs, or explicitly select nulls='ignore' on this reducer."
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        message="The numeric operation cannot produce an admitted nullable value.",
        expected=expected,
        received=received,
        action=action,
    )


def _finite_check(value: ir.Value, operation: str) -> tuple[NumericCheck, ...]:
    if not value.type().is_floating():
        return ()
    return (
        NumericCheck(
            value.notnull() & (value.isnan() | value.isinf()), "finite numeric values", operation
        ),
    )


def numeric_binary(operation: str, left: NumericValue, right: NumericValue) -> NumericValue:
    """Emit arithmetic with the denominator masked before division."""
    a, b = bind_numeric_literal(left.value), bind_numeric_literal(right.value)
    if a.type().is_floating() and b.type().is_decimal():
        b = b.cast("float64")
    elif b.type().is_floating() and a.type().is_decimal():
        a = a.cast("float64")
    checks = (*left.checks, *right.checks)
    if operation == "divide":
        zero = b == 0
        safe = zero.ifelse(ibis.null().cast(b.type().copy(nullable=True)), b)
        if a.type().is_decimal() or safe.type().is_decimal():
            a, safe = a.cast("float64"), safe.cast("float64")
        value = a / safe
    elif operation == "add":
        value = add_numeric(a, b)
    elif operation == "subtract":
        value = _binary_numeric("subtract", a, b)
    elif operation == "multiply":
        value = multiply_numeric(a, b)
    else:
        numeric_failure("add/subtract/multiply/divide", operation)
    return NumericValue(value, (*checks, *_finite_check(value, operation)))


def lower_row(node: RowNode, fields: Mapping[str, ir.Value]) -> NumericValue:
    """Lower one closed Row expression over already prepared physical fields."""
    if isinstance(node, RowExpression):
        from marivo.semantic._numeric_recipe import lower_recipe

        return lower_recipe(node.recipe, tuple(lower_row(item, fields) for item in node.inputs))
    if isinstance(node, RowField):
        from marivo._definition.types import field_read_key

        value = fields.get(
            field_read_key(node.read) if node.read is not None else node.ref.ref.path
        )
        if value is None:
            numeric_failure(
                f"a prepared physical field for {node.ref.ref.path}", "unbound field read"
            )
        if node.ref.ref.kind is SemanticKind.MEASURE and not value.type().is_numeric():
            numeric_failure(f"a numeric physical type for {node.ref.ref.path}", str(value.type()))
        if value.type().is_integer() and value.type().nbytes < 8:
            value = value.cast("int64")
        elif value.type().is_floating() and value.type().nbytes < 8:
            value = value.cast("float64")
        return NumericValue(value, _finite_check(value, node.ref.ref.path))
    if isinstance(node, MeasureAssertion):
        return lower_row(node.operand, fields)
    numeric_failure("an admitted native row recipe", type(node).__name__)


def lower_field_predicate(node: RowExpression, fields: Mapping[str, ir.Value]) -> NumericValue:
    """Lower a native boolean recipe with its demand-aware numeric checks."""
    if not isinstance(node, RowExpression):
        numeric_failure("an admitted native row predicate", type(node).__name__)
    return lower_row(node, fields)


def lower_predicate(
    node: PredicateExpression,
    fields: Mapping[str, ir.Value],
    memberships: tuple[ir.BooleanValue, ...] = (),
) -> NumericValue:
    """Bind each boolean slot and retain all field checks on the input domain."""
    from marivo._definition.relations import PopulationContains
    from marivo.semantic._numeric_recipe import lower_recipe

    inputs: list[NumericValue] = []
    markers = iter(memberships)
    for item in node.inputs:
        if isinstance(item, PopulationContains):
            marker = next(markers, None)
            if marker is None:
                numeric_failure(
                    "one prepared marker per Population membership leaf", "missing marker"
                )
            inputs.append(NumericValue(marker))
        else:
            field = lower_field_predicate(item, fields)
            if not field.value.type().is_boolean():
                numeric_failure("a physical boolean predicate", str(field.value.type()))
            inputs.append(field)
    if next(markers, None) is not None:
        numeric_failure("one prepared marker per Population membership leaf", "extra marker")
    checks = tuple(check for item in inputs for check in item.checks)
    result = lower_recipe(node.recipe, tuple(NumericValue(item.value) for item in inputs))
    if not result.value.type().is_boolean():
        numeric_failure("a physical boolean predicate", str(result.value.type()))
    return NumericValue(result.value, (*checks, *result.checks))


def force_cast(value: ir.Value, dtype: dt.DataType) -> ir.Value:
    """Retain a result CAST in SQL even when Ibis infers the same type."""
    return ops.Cast(value, to=dtype).to_expr()


def _source_sum(value: ir.Value) -> ir.Value:
    dtype = value.type()
    if dtype.is_integer():
        value = value.cast("decimal(38,0)")
        dtype = value.type()
    result = value.sum()
    return (
        force_cast(result, dt.Decimal(38, dtype.scale or 0))
        if isinstance(dtype, dt.Decimal)
        else result
    )


def lower_source_reduction(
    node: SourceAggregate | SourceWeightedMean | EntityCount,
    table: ir.Table,
    fields: Mapping[str, ir.Value],
) -> NumericValue:
    """Reduce one contribution. Checks returned here are scalar aggregates."""
    if isinstance(node, EntityCount):
        return NumericValue(table.count())
    item = lower_row(node.value if isinstance(node, SourceWeightedMean) else node.operand, fields)
    value = item.value
    checks = list(item.checks)
    if isinstance(node, SourceWeightedMean):
        weight = lower_row(node.weight, fields)
        checks.extend(weight.checks)
        pair = value.notnull() & weight.value.notnull()
        if node.nulls == "error":
            checks.append(
                NumericCheck(~pair, "non-NA value/weight pairs", "weighted_mean(nulls='error')")
            )
        product = numeric_binary("multiply", NumericValue(value), NumericValue(weight.value))
        checks.extend(product.checks)
        numerator = _source_sum(
            pair.ifelse(product.value, ibis.null().cast(product.value.type().copy(nullable=True)))
        )
        denominator = _source_sum(
            pair.ifelse(weight.value, ibis.null().cast(weight.value.type().copy(nullable=True)))
        )
        numerator = numerator.fill_null(ibis.literal(0).cast(numerator.type()))
        denominator = denominator.fill_null(ibis.literal(0).cast(denominator.type()))
        divided = numeric_binary(
            "divide",
            NumericValue(numerator, _finite_check(numerator, "weighted numerator sum")),
            NumericValue(denominator, _finite_check(denominator, "weight sum")),
        )
        return NumericValue(
            divided.value, (*(_aggregate_check(c) for c in checks), *divided.checks)
        )
    if node.nulls == "error":
        checks.append(
            NumericCheck(value.isnull(), "non-NA source values", f"{node.reducer}(nulls='error')")
        )
    if node.reducer == "count":
        result = value.count()
    elif node.reducer == "count_distinct":
        result = value.nunique()
    elif node.reducer == "sum":
        result = _source_sum(value)
        # An absent contribution is additive zero; existing unknown values are not.
        result = (table.count() == 0).ifelse(
            ibis.literal(0).cast(result.type()),
            (value.count() == 0).ifelse(
                ibis.null().cast(result.type().copy(nullable=True)), result
            ),
        )
        return NumericValue(
            result,
            (
                *(_aggregate_check(c) for c in checks),
                *_finite_check(result, node.reducer),
            ),
        )
    elif node.reducer == "approx_nunique":
        result = value.approx_nunique()
    elif node.reducer == "approx_median":
        result = value.approx_median()
    elif node.reducer == "approx_quantile":
        numeric_failure(
            "a qualified approximate quantile route",
            "Ibis 12 has no approximate quantile operation",
        )
    elif node.reducer == "mean":
        result = value.mean()
        if result.type().is_decimal():
            result = force_cast(result, result.type())
    elif node.reducer == "min":
        result = value.min()
    elif node.reducer == "max":
        result = value.max()
    elif node.reducer == "median":
        result = value.median()
    else:
        assert node.q is not None
        result = value.quantile(node.q)
    if node.reducer not in ("count", "count_distinct", "approx_nunique"):
        result = (value.count() == 0).ifelse(
            ibis.null().cast(result.type().copy(nullable=True)), result
        )
    return NumericValue(
        result, (*(_aggregate_check(c) for c in checks), *_finite_check(result, node.reducer))
    )


def _aggregate_check(check: NumericCheck) -> NumericCheck:
    return NumericCheck(check.violation.any().fill_null(False), check.expected, check.received)


def lower_metric_formula(
    node: MetricNode,
    occurrence: tuple[int, ...],
    values: Mapping[tuple[int, ...], NumericValue],
    declared: Mapping[ExactRef, MetricNode],
) -> NumericValue:
    """Compose completed reductions only; never join contribution rows."""
    if occurrence in values:
        return values[occurrence]
    from marivo._definition.expressions import MemberCount

    if isinstance(node, MemberCount):
        # Type preparation only; the local member input supplies completed counts.
        return NumericValue(ibis.literal(0).cast("int64"))
    if isinstance(node, MetricExpression):
        from marivo.semantic._numeric_recipe import lower_recipe

        return lower_recipe(
            node.recipe,
            tuple(
                lower_metric_formula(item, (*occurrence, i), values, declared)
                for i, item in enumerate(node.operands)
            ),
        )
    if isinstance(node, DeclaredMetricRef):
        return lower_metric_formula(declared[node.ref], (*occurrence, 0), values, declared)
    if isinstance(node, (ContributionFilter, MetricWindow)):
        return lower_metric_formula(node.operand, (*occurrence, 0), values, declared)
    if isinstance(node, PerReduce):
        if node.reducer == "approx_nunique":
            numeric_failure(
                "a qualified approximate internal distinct route",
                "no qualified local approximate distinct kernel",
            )
        item = lower_metric_formula(node.operand, (*occurrence, 0), values, declared)
        # Only type preparation reaches an unmaterialized Per node. Local sample
        # execution supplies its completed slot before numerical composition.
        value = ibis.table({"value": item.value.type()}, name="_internal_sample_types").value
        if node.reducer in ("count", "count_distinct", "approx_nunique"):
            return NumericValue(value.count())
        if node.reducer in ("mean", "median", "quantile", "approx_median", "approx_quantile"):
            return NumericValue(
                value.mean()
                if node.reducer == "mean"
                else value.quantile(node.q if node.q is not None else 0.5)
            )
        return NumericValue(_source_sum(value) if node.reducer == "sum" else value.min())
    if isinstance(node, PerWeightedMean):
        value = lower_metric_formula(node.value, (*occurrence, 0), values, declared)
        weight = lower_metric_formula(node.weight, (*occurrence, 1), values, declared)
        return numeric_binary(
            "divide",
            numeric_binary("multiply", value, weight),
            weight,
        )
    numeric_failure(
        "ordinary source reductions and Metric arithmetic",
        f"{type(node).__name__}; Population/sample preparation is delivered in R3",
    )


def _binary_numeric(operation: str, left: ir.Value, right: ir.Value) -> ir.Value:
    a, b = left.type(), right.type()
    if a.is_floating() and b.is_decimal():
        right = right.cast(a)
    elif b.is_floating() and a.is_decimal():
        left = left.cast(b)
    target = decimal_result_type(operation, a, b)
    if target is not None:
        if a.is_decimal() and b.is_integer():
            precision = len(str(2 ** (b.nbytes * 8 - int(b.is_signed_integer())) - 1))
            right = right.cast(dt.Decimal(precision, 0))
        elif b.is_decimal() and a.is_integer():
            precision = len(str(2 ** (a.nbytes * 8 - int(a.is_signed_integer())) - 1))
            left = left.cast(dt.Decimal(precision, 0))
    try:
        result = (
            left * right
            if operation == "multiply"
            else left - right
            if operation == "subtract"
            else left + right
        )
    except IbisTypeError:
        if target is None:
            raise
        # Ibis requires comparable Decimal metadata for some mixed p/s pairs.
        scale = max(
            a.scale or 0 if isinstance(a, dt.Decimal) else 0,
            b.scale or 0 if isinstance(b, dt.Decimal) else 0,
        )
        common = dt.Decimal(38, scale)
        left, right = left.cast(common), right.cast(common)
        result = (
            left * right
            if operation == "multiply"
            else left - right
            if operation == "subtract"
            else left + right
        )
    if target is not None:
        left_scale = (a.scale or 0) if isinstance(a, dt.Decimal) else 0
        right_scale = (b.scale or 0) if isinstance(b, dt.Decimal) else 0
        scale = (
            left_scale + right_scale if operation == "multiply" else max(left_scale, right_scale)
        )
        if target.scale < scale:
            result = result.round(target.scale)
        return force_cast(result, target)
    return result


def multiply_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Multiply with a bound Decimal result cast and native backend arithmetic."""
    return _binary_numeric("multiply", left, right)


def add_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Add with a bound Decimal result cast and native backend arithmetic."""
    return _binary_numeric("add", left, right)
