"""Prove a native sample fold can use the source reducer at the parent grain."""

from __future__ import annotations

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    FieldCalendarSample,
    PerReduce,
    RowField,
    SourceAggregate,
)
from marivo._definition.relations import SampleDomain, sample_population
from marivo._definition.types import AxisBinding, NullPolicy
from marivo._kernel.temporal import Grain
from marivo.analysis.compiler.native_formula import normalized_returned_key
from marivo.analysis.compiler.sample_values import reduce_sample_value
from marivo.analysis.model.data_types import DurationDataType
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.summary_traits import SummaryReducer, summary_trait


def _sample_width(node: PerReduce, source_id: str) -> int:
    samples = node.samples
    if (
        sample_population(samples) is not None
        or isinstance(samples, (Grain, FieldCalendarSample))
        or (isinstance(samples, SampleDomain) and samples.memberships)
    ):
        return 0
    axes = samples if isinstance(samples, tuple) else samples.axes
    if any(
        not isinstance(axis, AxisBinding)
        or axis.role != "field"
        or axis.entity.ref.path != source_id
        or axis.path
        or axis.versioned
        or axis.at is not None
        or axis.source_at is not None
        for axis in axes
    ):
        return 0
    return len(axes)


def _native_pair(node: PerReduce, table: ir.Table, value: str) -> str | None:
    inner = node.operand
    reducer: SummaryReducer
    nulls: NullPolicy
    if isinstance(inner, EntityCount):
        reducer, nulls = "entity.count", "ignore"
    elif isinstance(inner, SourceAggregate) and inner.reducer in ("count", "sum", "min", "max"):
        if not isinstance(inner.operand, RowField):
            return "The source reduction contains a computed row expression."
        read = inner.operand.read
        if read is not None and (read.path or read.versioned or read.at or read.source_at):
            return "The source reduction reads a related or versioned field."
        reducer, nulls = inner.reducer, inner.nulls
    else:
        return "The child is not a direct source count, sum or extremum."
    if (
        node.reducer not in ("sum", "min", "max")
        or (reducer, node.reducer, node.nulls)
        not in summary_trait(reducer, nulls_policy=nulls).implicit_single_value_collapse_pairs
    ):
        return "The original reducers and NULL policies have no implicit partition law."
    if reducer == "sum":
        return "The source SUM has no static bound preserving native intermediate overflow."
    dtype = table[value].type().copy(nullable=True)
    if not (dtype.is_signed_integer() or dtype.is_decimal()):
        return "The native result carrier is not an exact integer or Decimal."
    finished, _ = reduce_sample_value(node, table, (value,))
    if dtype != finished.type().copy(nullable=True):
        return "The original outer reducer changes the native result carrier."
    return None


def _exact_parent_key(dtype: dt.DataType) -> bool:
    if isinstance(dtype, dt.Decimal):
        return dtype.precision is not None and dtype.scale is not None
    return isinstance(dtype, (dt.SignedInteger, dt.Boolean, dt.Date))


def collapse_implicit_sample(
    node: PerReduce,
    table: ir.Table,
    keys: tuple[str, ...],
    binding: FormulaInput,
    source_id: str,
    provider: str,
) -> ir.Table | str:
    """Drop only proven sample grouping slots from the actual native source aggregate."""
    if binding.checks or isinstance(binding.column.dtype, DurationDataType):
        return "The source input has independent checks or an exact local tick finisher."
    complete_keys, value = binding.keys, binding.column.slot_id
    width = _sample_width(node, source_id)
    if not width or len(complete_keys) != len(keys) + width:
        return "The sample is not an implicit tuple of direct single-valued source fields."
    relation = table.op()
    if not (
        isinstance(relation, ops.Aggregate)
        and complete_keys[: len(keys)] == keys
        and tuple(relation.groups) == complete_keys
        and tuple(relation.metrics) == (value,)
    ):
        return "The lowered read retains presence, checks or an independent source boundary."
    # The outer fold groups normalized returned identities. Raw string collations
    # can choose different representatives per subgroup, and floating encodings
    # have their own returned equality. Only exact parent keys preserve both.
    if any(not _exact_parent_key(relation.schema[key]) for key in keys):
        return "The retained parent keys have no proven native equality."
    refusal = _native_pair(node, table, value)
    if refusal is not None:
        return refusal
    collapsed = ops.Aggregate(
        relation.parent,
        {key: relation.groups[key] for key in keys},
        relation.metrics,
    ).to_expr()
    return collapsed.mutate(
        **{key: normalized_returned_key(collapsed[key], provider) for key in keys}
    )
