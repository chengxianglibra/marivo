"""Ibis-owned preparation of explicit observation partitions and target checks."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    _pair_violations,
    canonical_layout,
    consumption_check,
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
    checks.extend(
        consumption_check(
            stage,
            "attribution",
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
    stage: SourceMethodStage,
    source: LoweredRelation,
    table: ir.Table,
    layout: RelationLayout,
    coordinate_relations: list[tuple[str, ir.Table]],
    transports: list[ir.Table],
) -> tuple[ir.Table, RelationLayout]:
    """Retain flat allocation evidence when original reduction removes axes."""
    from dataclasses import replace

    from marivo.analysis.compiler.coordinate_state import entries, finish_sum, sum_value
    from marivo.analysis.core.model import CoordinateStatePart, part_role
    from marivo.analysis.core.rules import OriginalReduce

    params = stage.node.parameters
    assert isinstance(params, OriginalReduce)
    coordinate = next(
        (p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart)), None
    )
    if coordinate is None:
        return table, layout
    original = next(p for p in source.node.signature.parts if isinstance(p, CoordinateStatePart))
    exploded = entries(source, original)
    source_keys = source.node.signature.domain.instance_key
    if params.time_mapping:
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
            key: exploded[f"key_{source_keys.index(c)}"]
            if c in source_keys
            else exploded[original.columns[original.coordinates.index(c)]]
            for key, c in zip(keys, params.coordinates, strict=True)
        },
        **{name: exploded[name] for name in (*original.columns, *original.components)},
    )
    fields: dict[str, ir.Value] = {}
    for name in original.components:
        value = selected[name]
        if name in ("min", "max"):
            value = (selected.non_null_count > 0).ifelse(value, ibis.null().cast(value.type()))
            reduced = value.min() if name == "min" else value.max()
        else:
            reduced = sum_value(stage, value)
        fields[name] = finish_sum(stage, reduced, selected[name].type())
    grouped = selected.group_by(*keys, *original.columns).aggregate(**fields)
    coordinate_relations.append((part_role(coordinate), grouped))
    from marivo.analysis.compiler.coordinate_state import window_result

    targets = (
        table.select(*keys).distinct()
        if keys
        else table.aggregate(__target=ibis.literal(0, type="int64"))
    )
    if not keys:
        grouped = grouped.mutate(__target=ibis.literal(0, type="int64"))
    primary, target, stream = window_result(stage, grouped, targets, keys or ("__target",))
    transports.append(stream)
    return primary, target
