"""Ibis-governed complete-key preparation for registered display methods."""

from __future__ import annotations

from dataclasses import replace

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    _pair_violations,
    canonical_layout,
    consumption_check,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.model import DisplayPart
from marivo.analysis.core.rules import DisplayRank, DisplayTable


def prepare(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, (DisplayRank, DisplayTable))
    first = inputs[0]
    table = first.expression
    keys = tuple(k.column for k in first.layout.keys)
    if isinstance(params, DisplayRank):
        previous_display = {
            f"{part.role}__{component}"
            for part in first.node.signature.parts
            if isinstance(part, DisplayPart)
            for component in part.components
        }
        table = table.select(*(name for name in table.columns if name not in previous_display))
    if isinstance(params, DisplayTable):
        table = table.select(
            *keys,
            **{
                f"column_0__{field}": table[field] for field in ("value", "cell_tag", "cell_reason")
            },
        )
    for index, other in enumerate(inputs[1:], 1):
        right = other.expression.view()
        checks.extend(
            consumption_check(
                stage,
                f"display:{index}",
                first.expression.anti_join(right, list(keys))
                if isinstance(params, DisplayRank) and index in params.inclusion_inputs
                else _pair_violations(first, other),
                tuple(dict.fromkeys((*first.source_ids, *other.source_ids))),
            )
        )
        joined = (
            table.inner_join(right, [table[k].identical_to(right[k]) for k in keys])
            if keys
            else table.cross_join(right)
        )
        fields = (
            {
                f"column_{index}__{field}": right[field]
                for field in ("value", "cell_tag", "cell_reason")
            }
            if isinstance(params, DisplayTable)
            else {f"partitions__partition_{index - 1}": right.value}
        )
        if isinstance(params, DisplayRank):
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "complete category partition",
                    right.filter(~right.cell_tag.isin(("defined", "null"))),
                    other.source_ids,
                )
            )
        table = joined.select(*(table[c] for c in table.columns), **fields)
    if isinstance(params, DisplayRank) and not params.partition_types:
        table = table.mutate(partitions__partition=ibis.literal(0).cast("int64"))
    for part in stage.node.signature.parts:
        if not isinstance(part, DisplayPart):
            continue
        fields = {}
        for component, dtype in zip(part.components, part.types, strict=True):
            name = f"{part.role}__{component}"
            if name in table.columns:
                continue
            source_name = (
                component
                if part.role in ("values", "ranking_domain")
                and component in ("value", "cell_tag", "cell_reason")
                else "cell_tag"
                if component in ("cell_tag", "rank_tag")
                else "cell_reason"
                if component in ("cell_reason", "rank_reason")
                else f"partitions__{component}"
                if part.role == "ranking_domain" and component.startswith("partition")
                else component
                if part.role == "columns"
                else None
            )
            fields[name] = (
                table[source_name]
                if source_name is not None
                else ibis.literal(part.identity)
                if part.role == "column_bindings" and isinstance(params, DisplayTable)
                else ibis.literal(0).cast("int64" if dtype.startswith("interval(") else dtype)
            )
        table = table.mutate(**fields)
    layout = canonical_layout(stage.node.signature, has_value=isinstance(params, DisplayRank))
    if isinstance(params, DisplayTable):
        layout = replace(
            layout,
            extras=tuple(
                f"column_{i}__{f}"
                for i in range(len(params.labels))
                for f in ("value", "cell_tag", "cell_reason")
            ),
        )
    return table.select(*layout.columns), layout
