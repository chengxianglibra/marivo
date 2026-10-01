"""Ibis-owned preparation of explicit observation partitions and target checks."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    _pair_violations,
    canonical_layout,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.model import AttributionPart
from marivo.analysis.core.rules import AttributionDerive
from marivo.analysis.methods.attribution import columns, part_keys


def prepare(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
    parts: list[tuple[str, ir.Table]],
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, AttributionDerive)
    first, expanded = inputs
    keys = tuple(k.column for k in first.layout.keys)
    checks.append(
        IntegrityCheck(
            stage.output,
            "exact original and expanded attribution scope",
            _pair_violations(first, expanded),
            tuple(dict.fromkeys((*first.source_ids, *expanded.source_ids))),
        )
    )
    left, right = first.expression.view(), expanded.expression.view()
    joined = left.inner_join(right, list(keys)) if keys else left.cross_join(right)
    fields: dict[str, ir.Value] = {k: left[k] for k in keys}
    fields[f"key_{len(keys)}"] = ibis.literal(len(params.axes)).cast("int64")
    fields.update({f"key_{len(keys) + 1 + i}": ibis.literal("") for i in range(len(params.axes))})
    fields[f"key_{len(keys) + 1 + len(params.axes)}"] = ibis.literal(0).cast("int64")
    dtype = "int64" if params.value_type.startswith("interval(") else params.value_type
    fields.update(
        value=ibis.literal(0).cast(dtype),
        cell_tag=ibis.literal("defined"),
        cell_reason=ibis.null().cast("string"),
    )
    for declaration in stage.node.signature.parts:
        assert isinstance(declaration, AttributionPart)
        role = declaration.role
        if declaration.endpoint is not None:
            expression = expanded.expression.select(
                *keys, *(f"{role}__{c}" for c in columns(declaration))
            )
            fields.update({f"{role}__{c}": right[f"{role}__{c}"] for c in columns(declaration)})
            parts.append((role, expression))
        elif role == "basis":
            expression = first.expression.select(*keys, basis__value=first.expression.value)
            fields["basis__value"] = left.value
            parts.append((role, expression))
        else:
            for c in columns(declaration):
                fields[f"{role}__{c}"] = (
                    ibis.literal(True)
                    if c in ("complete", "selected")
                    else ibis.literal(0).cast("float64")
                    if c.endswith("_error_bound")
                    else ibis.literal(0).cast(dtype)
                )
    table = joined.select(**fields)
    layout = canonical_layout(stage.node.signature, has_value=True)
    table = table.select(*layout.columns)
    for p in stage.node.signature.parts:
        assert isinstance(p, AttributionPart)
        if p.role in ("allocation", "reconciliation"):
            parts.append(
                (p.role, table.select(*part_keys(p), *(p.role + "__" + c for c in columns(p))))
            )
    return table, layout


def retain_partition(
    stage: SourceMethodStage, source: LoweredRelation, table: ir.Table, layout: RelationLayout
) -> tuple[ir.Table, RelationLayout]:
    """Retain only allocation evidence when original reduction removes axes."""
    from marivo.analysis.compiler.graph_lowering import coordinate_state_type
    from marivo.analysis.core.model import CoordinateStatePart
    from marivo.analysis.core.rules import OriginalReduce

    params = stage.node.parameters
    assert isinstance(params, OriginalReduce)
    coordinate = next(
        (p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart)), None
    )
    if coordinate is None:
        return table, layout
    original = next(p for p in source.node.signature.parts if isinstance(p, CoordinateStatePart))
    from marivo.analysis.core.model import part_role

    raw = source.expression
    exploded = raw.select(
        *(k.column for k in source.layout.keys),
        group=raw[part_role(original) + "__groups"].unnest(),
    )
    source_keys = source.node.signature.domain.instance_key
    if params.time_mapping:
        from dataclasses import replace

        grid = params.output_domain.time_grid
        assert grid is not None
        index = next(i for i, c in enumerate(source_keys) if c.role == "anchor")
        field = f"key_{index}"
        exploded = exploded.mutate(
            **{
                field: ibis.cases(
                    *((exploded[field] == old, new) for old, new in params.time_mapping),
                    else_=ibis.null().cast("string"),
                )
            }
        )
        source_keys = tuple(
            replace(c, field="time:" + grid.identity) if c.role == "anchor" else c
            for c in source_keys
        )
    keys = tuple(f"key_{i}" for i in range(len(params.coordinates)))
    selected = exploded.select(
        **{
            k: exploded[f"key_{source_keys.index(c)}"]
            if c in source_keys
            else exploded.group[original.columns[original.coordinates.index(c)]]
            for k, c in zip(keys, params.coordinates, strict=True)
        },
        **{c: exploded.group[c] for c in (*original.columns, *original.components)},
    )
    grouped = selected.group_by(*keys, *original.columns).aggregate(
        **{c: selected[c].sum().cast(selected[c].type()) for c in original.components}
    )
    cell = ibis.struct({c: grouped[c] for c in (*original.columns, *original.components)})
    nested = (grouped.group_by(*keys) if keys else grouped).aggregate(
        allocation_state__groups=cell.collect(order_by=[grouped[c] for c in original.columns])
    )
    joined = table.left_join(nested, list(keys)) if keys else table.cross_join(nested)
    output = joined.select(
        *(table[c] for c in table.columns),
        allocation_state__groups=nested.allocation_state__groups.fill_null(
            ibis.literal([], type=coordinate_state_type(coordinate))
        ),
    )
    target = canonical_layout(stage.node.signature, has_value=True)
    return output.select(*target.columns), target
