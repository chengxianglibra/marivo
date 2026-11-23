"""Lower native sample values and identify equivalent sample coordinate slots."""

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import PerReduce, PerWeightedMean
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.spi import (
    NumericValue,
    finish_nullable_sum,
    force_cast,
    native_mean,
    numeric_binary,
)


def _null(value: ir.Value) -> ir.Value:
    return ibis.null().cast(value.type().copy(nullable=True))


def coincident_sample_keys(table: ir.Table, facts: UniqueKeys) -> UniqueKeys:
    """Elide duplicate slots of the same aggregate grouping field."""
    relation = table.op()
    if not isinstance(relation, ops.Aggregate):
        return facts
    first: dict[ops.Field, str] = {}
    aliases = {
        slot: first.setdefault(group, slot)
        for slot, group in relation.groups.items()
        if isinstance(group, ops.Field)
    }
    # These are identical projected fields, with no SQL comparison.
    candidates = tuple(
        tuple(dict.fromkeys(aliases.get(slot, slot) for slot in key)) for key in facts.keys
    )
    return UniqueKeys(facts.slots, (*facts.keys, *candidates))


def reduce_sample_value(
    node: PerReduce | PerWeightedMean, table: ir.Table, operands: tuple[str, ...]
) -> tuple[ir.Value, bool]:
    value = table[operands[0]]
    zero = False
    if isinstance(node, PerWeightedMean):
        weight = table[operands[1]]
        pair = value.notnull() & weight.notnull()
        product = numeric_binary("multiply", NumericValue(value), NumericValue(weight)).value
        numerator = pair.ifelse(product, _null(product)).sum().fill_null(0)
        denominator = pair.ifelse(weight, _null(weight)).sum().fill_null(0)
        reduced = numeric_binary("divide", NumericValue(numerator), NumericValue(denominator)).value
    elif node.reducer == "mean":
        reduced = native_mean(value)
    elif node.reducer == "sum":
        reduced = finish_nullable_sum(value.sum(), table.count(), value.count())
        zero = True
    elif node.reducer in ("count", "count_distinct"):
        reduced = value.count() if node.reducer == "count" else value.nunique()
        zero = True
    elif node.reducer in ("approx_median", "approx_quantile"):
        reduced = value.cast("float64").approx_quantile(node.q if node.q is not None else 0.5)
        if value.type().is_decimal():
            reduced = force_cast(reduced, value.type())
        reduced = (value.count() == 0).ifelse(_null(reduced), reduced)
    else:
        reduced = (
            value.min()
            if node.reducer == "min"
            else value.max()
            if node.reducer == "max"
            else value.quantile(node.q if node.q is not None else 0.5)
        )
        if value.type().is_decimal() and node.reducer in ("median", "quantile"):
            reduced = force_cast(reduced, reduced.type())
        reduced = (value.count() == 0).ifelse(_null(reduced), reduced)
    return reduced, zero
