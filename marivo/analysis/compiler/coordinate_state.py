"""Flat Ibis contribution relations and one closed terminal transport."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.core.model import CoordinateStatePart, OriginalStatePart, part_role
from marivo.analysis.methods.coordinate_state import (
    KeyedCoordinateLayout,
    PartitionCoordinateLayout,
    declarations,
    layout,
)
from marivo.analysis.methods.physical import DecimalType, DurationType, SourceShape

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_lowering import LoweredRelation, RelationLayout
    from marivo.analysis.compiler.graph_plan import SourceMethodStage


def entries(source: LoweredRelation, part: CoordinateStatePart) -> ir.Table:
    role = part_role(part)
    retained = next((value for name, value in source.coordinate_relations if name == role), None)
    if retained is not None:
        return retained
    spec = layout(source.node.signature, role)
    assert isinstance(spec, KeyedCoordinateLayout)
    table = source.expression.filter(source.expression[role + "__present"])
    return table.select(
        *spec.keys,
        **{
            name: table[column] for name, column in zip(part.columns, spec.coordinates, strict=True)
        },
        **{
            name: table[column]
            for name, column in zip(part.components, spec.components, strict=True)
        },
    )


def physical(source: LoweredRelation) -> tuple[tuple[str, ir.Table], ...]:
    result: list[tuple[str, ir.Table]] = []
    for role, table in source.coordinate_relations:
        spec = layout(source.node.signature, role)
        if isinstance(spec, PartitionCoordinateLayout):
            declaration = next(
                part for name, part, _, _ in declarations(source.node.signature) if name == role
            )
            fields = {key: table[key] for key in spec.keys}
            fields.update(
                (column, table[name])
                for name, column in zip(declaration.columns, spec.coordinates, strict=True)
                if column not in fields
            )
            fields.update((name, table[name]) for name in spec.components)
            result.append((role, table.select(**fields)))
        elif isinstance(spec, KeyedCoordinateLayout) and spec.owner != "original_state":
            parent = source.expression
            result.append(
                (
                    role,
                    parent.select(
                        *spec.keys,
                        **{role + "__present": parent[spec.owner + "__contribution_present"]},
                    ),
                )
            )
    return tuple(result)


def transport(source: LoweredRelation) -> ir.Table | None:
    children = physical(source)
    if not children:
        return None
    tables = (source.expression, *(table for _, table in children))
    fields = tuple(
        (index, name, dtype)
        for index, table in enumerate(tables)
        for name, dtype in table.schema().items()
    )
    branches = tuple(
        table.select(
            __marivo_row_kind=ibis.literal(index, type="int8"),
            **{
                f"__part_{owner}__{name}": table[name].cast(dtype)
                if owner == index
                else ibis.null().cast(dtype)
                for owner, name, dtype in fields
            },
        )
        for index, table in enumerate(tables)
    )
    return branches[0].union(*branches[1:], distinct=False)


def complete_join(
    left: ir.Table, right: ir.Table, first: tuple[ir.Value, ...], second: tuple[ir.Value, ...]
) -> ir.Table:
    """Align independent roots over the union of their complete tuple domains."""
    if not first:
        return left.cross_join(right)
    names = tuple(f"__union_key_{i}" for i in range(len(first)))
    domain = left.select(**dict(zip(names, first, strict=True))).union(
        right.select(**dict(zip(names, second, strict=True))), distinct=True
    )
    joined = domain.left_join(
        left, [domain[name] == value for name, value in zip(names, first, strict=True)]
    )
    return joined.left_join(
        right, [domain[name] == value for name, value in zip(names, second, strict=True)]
    )


def sum_value(stage: SourceMethodStage, value: ir.Value) -> ir.Value:
    """Preserve the existing ClickHouse widened accumulator before reduction."""
    shape = stage.implementation.key.shape
    dtype = value.type()
    if (
        isinstance(shape, SourceShape)
        and shape.backend == "clickhouse"
        and (dtype.is_integer() or isinstance(dtype, dt.Decimal))
    ):
        value = value.cast(dt.Decimal(76, dtype.scale if isinstance(dtype, dt.Decimal) else 0))
    return value.sum()


def finish_sum(stage: SourceMethodStage, value: ir.Value, dtype: dt.DataType) -> ir.Value:
    """Keep signed int64 and exact Decimal range failures at native transport."""
    from marivo.analysis.methods.native_numeric import bounded_transport_cast, transport_cast

    shape = stage.implementation.key.shape
    value = value.fill_null(0)
    if (
        isinstance(shape, SourceShape)
        and shape.backend == "clickhouse"
        and (dtype.is_integer() or isinstance(dtype, dt.Decimal))
    ):
        if isinstance(dtype, dt.Decimal):
            assert dtype.precision is not None and dtype.scale is not None
            limit = Decimal((0, (1,), dtype.precision - dtype.scale))
            valid = (value > ibis.literal(-limit, type=dt.Decimal(76, 0))) & (
                value < ibis.literal(limit, type=dt.Decimal(76, 0))
            )
        else:
            valid = value.between(-(2**63), 2**63 - 1)
        value = bounded_transport_cast(value, str(dtype), valid)
    return transport_cast(value, str(dtype))


def window_result(
    stage: SourceMethodStage, full: ir.Table, targets: ir.Table, keys: tuple[str, ...]
) -> tuple[ir.Table, RelationLayout, ir.Table]:
    """Finish one dense target window while retaining each actual partition row."""
    from marivo.analysis.compiler.graph_lowering import (
        _linear_finish,
        _mean_finish,
        _ratio_finish,
        _reduction_subjects,
        _weighted_finish,
        canonical_layout,
    )

    coordinate = next(p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart))
    state = next(p for p in stage.node.signature.parts if isinstance(p, OriginalStatePart))
    marked = full.mutate(__marivo_coordinate_row=ibis.literal(True))
    dense = targets.left_join(marked, list(keys)) if keys else targets.cross_join(marked)
    table = dense.select(
        **{key: targets[key] for key in keys},
        **{
            f"__coordinate__{name}": marked[name]
            for name in (*coordinate.columns, *coordinate.components)
        },
        __marivo_coordinate_row=marked.__marivo_coordinate_row.fill_null(False),
    )
    window = ibis.window(group_by=[table[key] for key in keys])
    fields: dict[str, ir.Value] = {}
    for name in state.components:
        value = table[f"__coordinate__{name}"]
        if name in ("min", "max"):
            value = (table.__coordinate__non_null_count > 0).ifelse(
                value, ibis.null().cast(value.type())
            )
            reduced = value.min() if name == "min" else value.max()
        else:
            reduced = sum_value(stage, value)
        fields["original_state__" + name] = finish_sum(
            stage, reduced.over(window), full[name].type()
        )
    table = table.mutate(
        **fields,
        coverage__complete=ibis.literal(True),
        **{part_role(coordinate) + "__present": table.__marivo_coordinate_row.any().over(window)},
    )
    layout = canonical_layout(stage.node.signature, has_value=True)
    extra = (
        *tuple(name for name in table.columns if name.startswith("__coordinate__")),
        "__marivo_coordinate_row",
    )
    extended = replace(layout, extras=extra)
    table = _reduction_subjects(table, stage.node.signature)
    method = state.method_version.removesuffix("@v1")
    if method == "mean":
        table = _mean_finish(
            table,
            extended,
            duration=isinstance(stage.node.value_type, DurationType),
            result_type=stage.node.value_type.name
            if isinstance(stage.node.value_type, DecimalType)
            else None,
        )
    elif method == "weighted_mean":
        table = _weighted_finish(
            table, extended, duration=isinstance(stage.node.value_type, DurationType)
        )
    elif method == "ratio":
        table = _ratio_finish(table, extended, stage.node.signature)
    elif method == "linear":
        table = _linear_finish(table, extended, stage.node.signature, stage.node.value_type.name)
    else:
        value = table[
            "original_state__"
            + ("count" if method == "count" else method if method in ("min", "max") else "sum")
        ]
        defined = (
            ibis.literal(True)
            if method in ("count", "sum_zero")
            else table.original_state__non_null_count > 0
        )
        table = table.mutate(
            value=defined.ifelse(value, ibis.null().cast(value.type())),
            cell_tag=defined.ifelse("defined", "null"),
            cell_reason=defined.ifelse(ibis.null().cast("string"), "empty_contribution"),
        )
    stream = table.select(*layout.columns, *extra)
    return stream.select(*layout.columns).distinct(), layout, stream
