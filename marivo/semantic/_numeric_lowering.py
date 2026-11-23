"""Shared nullable Ibis lowering for current Row and Metric definitions."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
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
from marivo._definition.relations import PopulationContains
from marivo._definition.types import ExactRef, field_read_key
from marivo._kernel.numeric_types import exact_unsigned
from marivo._kernel.refs import SemanticKind
from marivo.semantic._numeric_recipe import lower_recipe
from marivo.semantic.numeric_operations import (
    NumericCheck,
    NumericValue,
    force_cast,
    numeric_binary,
    numeric_failure,
)


def lower_row(node: RowNode, fields: Mapping[str, ir.Value]) -> NumericValue:
    """Lower one closed Row expression over already prepared physical fields."""
    if isinstance(node, RowExpression):
        return lower_recipe(node.recipe, tuple(lower_row(item, fields) for item in node.inputs))
    if isinstance(node, RowField):
        value = fields.get(
            field_read_key(node.read) if node.read is not None else node.ref.ref.path
        )
        if value is None:
            numeric_failure(
                f"a prepared physical field for {node.ref.ref.path}", "unbound field read"
            )
        if node.ref.ref.kind is SemanticKind.MEASURE and not value.type().is_numeric():
            numeric_failure(f"a numeric physical type for {node.ref.ref.path}", str(value.type()))

        value = exact_unsigned(value)
        return NumericValue(value)
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


def _source_sum(value: ir.Value, where: ir.BooleanValue | None = None) -> ir.Value:
    dtype = value.type()
    result = value.sum(where=where)
    return (
        force_cast(result, dt.Decimal(38, dtype.scale or 0))
        if isinstance(dtype, dt.Decimal)
        else result
    )


def finish_nullable_sum(
    total: ir.Value, contribution_count: ir.IntegerScalar, valid_count: ir.IntegerScalar
) -> ir.Value:
    """Complete a native SUM using counts over the same contribution group."""
    dtype = total.type()
    return (contribution_count == 0).ifelse(
        ibis.literal(0).cast(dtype),
        (valid_count == 0).ifelse(ibis.null().cast(dtype.copy(nullable=True)), total),
    )


def native_mean(value: ir.Value, where: ir.BooleanValue | None = None) -> ir.Value:
    """Keep native AVG while completing an empty input as a typed NULL."""
    if not value.type().nullable:
        # ClickHouse AVG over a nonnullable carrier returns NaN on empty input.
        # Its nullable wrapper retains AVG's algorithm and returns NULL instead.
        value = force_cast(value, value.type().copy(nullable=True))
    result = value.mean(where=where)
    if value.type().is_integer() or result.type().is_decimal():
        # Native AVG may return Decimal for integers (PostgreSQL), or Float64
        # for Decimals (ClickHouse). Bind Ibis's declared transport result type.
        result = force_cast(result, result.type())
    return result


def _weighted_reduction(
    node: SourceWeightedMean,
    fields: Mapping[str, ir.Value],
    value: ir.Value,
    checks: list[NumericCheck],
    where: ir.BooleanValue | None = None,
) -> NumericValue:
    weight = lower_row(node.weight, fields)
    checks.extend(weight.checks)
    weights = weight.value
    if where is not None:
        # Backends can evaluate aggregate arguments before FILTER or *If.
        # A noncontributing row must stay outside the native product domain.
        value = where.ifelse(value, ibis.null().cast(value.type().copy(nullable=True)))
        weights = where.ifelse(weights, ibis.null().cast(weights.type().copy(nullable=True)))
    pair = value.notnull() & weights.notnull()
    if node.nulls == "error":
        checks.append(
            NumericCheck(~pair, "non-NA value/weight pairs", "weighted_mean(nulls='error')")
        )
    product = numeric_binary("multiply", NumericValue(value), NumericValue(weights))
    checks.extend(product.checks)
    numerator = _source_sum(
        pair.ifelse(product.value, ibis.null().cast(product.value.type().copy(nullable=True))),
        where,
    )
    denominator = _source_sum(
        pair.ifelse(weights, ibis.null().cast(weights.type().copy(nullable=True))),
        where,
    )
    numerator = numerator.fill_null(ibis.literal(0).cast(numerator.type()))
    denominator = denominator.fill_null(ibis.literal(0).cast(denominator.type()))
    divided = numeric_binary(
        "divide",
        NumericValue(numerator),
        NumericValue(denominator),
    )
    return NumericValue(
        divided.value, (*(_aggregate_check(c, where) for c in checks), *divided.checks)
    )


def _approx_quantile(
    node: SourceAggregate,
    value: ir.Value,
    where: ir.BooleanValue | None,
) -> ir.Value:
    q = 0.5 if node.reducer == "approx_median" else node.q
    assert q is not None
    # Native sketches consume floating values. Retain the declared Decimal
    # carrier explicitly rather than relying on Ibis's inferred SQL type.
    result = value.cast("float64").approx_quantile(q, where=where)
    if value.type().is_decimal():
        result = force_cast(result, value.type())
    return result


def lower_source_reduction(
    node: SourceAggregate | SourceWeightedMean | EntityCount,
    table: ir.Table,
    fields: Mapping[str, ir.Value],
    *,
    where: ir.BooleanValue | None = None,
) -> NumericValue:
    """Reduce one contribution, with optional native gating on its own input rows."""
    if isinstance(node, EntityCount):
        return NumericValue(source_contribution_count(table, where))
    item = lower_row(node.value if isinstance(node, SourceWeightedMean) else node.operand, fields)
    value = item.value
    checks = list(item.checks)
    if isinstance(node, SourceWeightedMean):
        return _weighted_reduction(node, fields, value, checks, where)
    if node.nulls == "error":
        checks.append(
            NumericCheck(value.isnull(), "non-NA source values", f"{node.reducer}(nulls='error')")
        )
    if node.reducer == "count":
        result = value.count(where=where)
    elif node.reducer == "count_distinct":
        result = value.nunique(where=where)
    elif node.reducer == "sum":
        return NumericValue(
            finish_nullable_sum(
                _source_sum(value, where),
                source_contribution_count(table, where),
                value.count(where=where),
            ),
            tuple(_aggregate_check(c, where) for c in checks),
        )
    elif node.reducer == "approx_nunique":
        result = value.approx_nunique(where=where)
    elif node.reducer in ("approx_median", "approx_quantile"):
        result = _approx_quantile(node, value, where)
    elif node.reducer == "mean":
        result = native_mean(value, where)
    elif node.reducer == "min":
        result = value.min(where=where)
    elif node.reducer == "max":
        result = value.max(where=where)
    elif node.reducer == "median":
        result = value.median(where=where)
    else:
        assert node.q is not None
        result = value.quantile(node.q, where=where)
    if node.reducer in ("median", "quantile") and value.type().is_decimal():
        # PostgreSQL percentile_cont returns double precision for numeric input.
        # Bind the declared Decimal transport explicitly without changing its algorithm.
        result = force_cast(result, result.type())
    if node.reducer not in ("count", "count_distinct", "approx_nunique", "mean"):
        result = (value.count(where=where) == 0).ifelse(
            ibis.null().cast(result.type().copy(nullable=True)), result
        )
    return NumericValue(result, tuple(_aggregate_check(c, where) for c in checks))


def source_contribution_count(table: ir.Table, where: ir.BooleanValue | None) -> ir.IntegerScalar:
    """Count admitted rows and retain zero for an empty contribution."""
    count = table.count(where=where)
    # Some dialects compile filtered COUNT(*) through SUM, which is NULL on empty input.
    return count if where is None else count.fill_null(0)


def _aggregate_check(check: NumericCheck, where: ir.BooleanValue | None = None) -> NumericCheck:
    violation = check.violation & where if where is not None else check.violation
    return NumericCheck(violation.any().fill_null(False), check.expected, check.received)


def lower_metric_formula(
    node: MetricNode,
    occurrence: tuple[int, ...],
    values: Mapping[tuple[int, ...], NumericValue],
    declared: Mapping[ExactRef, MetricNode],
) -> NumericValue:
    """Compose completed reductions only; never join contribution rows."""
    if occurrence in values:
        return values[occurrence]

    if isinstance(node, MemberCount):
        # Type preparation only; the local member input supplies completed counts.
        return NumericValue(ibis.literal(0).cast("int64"))
    if isinstance(node, MetricExpression):
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
