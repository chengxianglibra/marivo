"""Governed entry-time Dimension joins over the original occurrence preparation."""

from __future__ import annotations

from datetime import datetime

import ibis.expr.types as ir

from marivo.analysis.compiler.domain_preparation import _version
from marivo.analysis.compiler.graph_lowering import (
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
    captured_match_check,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.domain_captures import EntryAxisCapture
from marivo.analysis.core.rules import FunnelAxesPrepare
from marivo.analysis.methods.physical import SourceShape


def lower_axes(
    stage: SourceMethodStage,
    occurrences: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
    part_expressions: list[tuple[str, ir.Table]],
    part_source_ids: list[tuple[str, tuple[str, ...]]],
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    assert isinstance(params, FunnelAxesPrepare)
    sources = {b.leaf.identity: b for b in bindings}
    keys = tuple(k.column for k in occurrences.layout.keys)
    base = occurrences.expression.filter(
        occurrences.expression[keys[0]] == params.first_event,
        occurrences.expression.occurrences__occurred_at
        >= datetime.fromisoformat(params.cohort_start),
        occurrences.expression.occurrences__occurred_at < datetime.fromisoformat(params.cohort_end),
    )
    table = base
    source_ids = tuple(
        dict.fromkeys((*occurrences.source_ids, *(n.identity for n in stage.node.sources)))
    )
    # Identical complete routes share a mapping, including different leaf Dimensions.
    groups: list[list[tuple[int, EntryAxisCapture]]] = []
    for axis_index, axis in enumerate(params.axes):
        group = next(
            (
                group
                for group in groups
                if (
                    group[0][1].subject == axis.subject
                    and group[0][1].path == axis.path
                    and group[0][1].entities == axis.entities
                    and group[0][1].source_ids == axis.source_ids
                )
            ),
            None,
        )
        if group is None:
            groups.append([(axis_index, axis)])
        else:
            group.append((axis_index, axis))
    independent = (
        isinstance(stage.implementation.key.shape, SourceShape)
        and stage.implementation.key.shape.backend == "sqlite"
    )
    captured: list[int] = []
    for group in groups:
        axis_index, axis = group[0]
        route = []
        for index, hop in enumerate(axis.path):
            forward = hop.from_entity_ref == axis.entities[index].ref
            route.append(hop.keys if forward else tuple((right, left) for left, right in hop.keys))
        # Retain the Subject image separately: each axis starts at the same entry instant.
        current = base.select(
            **{name: base[name] for name in keys},
            __instant=base.occurrences__occurred_at,
            **{
                f"__join_{i}": base[f"subject__key_{i}"]
                for i in range(len(axis.subject.primary_key))
            },
        )
        for index, entity in enumerate(axis.entities):
            bound = sources[axis.source_ids[index]]
            raw = bound.source.relation.view()
            join_keys = (
                entity.primary_key if index == 0 else tuple(right for _, right in route[index - 1])
            )
            current_ids = tuple(
                dict.fromkeys((*occurrences.source_ids, *axis.source_ids[: index + 1]))
            )
            joined = current.left_join(
                raw,
                [
                    *(current[f"__join_{i}"] == raw[key] for i, key in enumerate(join_keys)),
                    _version(raw, bound, current.__instant),
                ],
            )
            checks.append(
                captured_match_check(
                    stage,
                    f"axis:{axis_index}:hop:{index}",
                    joined.filter(raw[join_keys[0]].isnull()),
                    current_ids,
                )
            )
            fields = {name: current[name] for name in (*keys, "__instant")}
            if index < len(route):
                fields.update(
                    {f"__join_{i}": raw[left] for i, (left, _) in enumerate(route[index])}
                )
            else:
                for captured_index, captured_axis in group:
                    column = raw[captured_axis.dimension.source_column]
                    fields[f"axis_{captured_index}"] = column
            current = joined.select(**fields)
        if independent:
            part_expressions.append(
                (f"axis_{axis_index}", current.select(*keys, *(f"axis_{i}" for i, _ in group)))
            )
            part_source_ids.append((f"axis_{axis_index}", current_ids))
        else:
            table = table.join(current, [table[key] == current[key] for key in keys]).select(
                **{
                    name: table[name]
                    for name in (
                        *keys,
                        "occurrences__occurred_at",
                        *(f"axis_{i}" for i in captured),
                    )
                },
                **{f"axis_{i}": current[f"axis_{i}"] for i, _ in group},
            )
        captured.extend(i for i, _ in group)
    layout = RelationLayout(
        occurrences.layout.keys, None, (), tuple(f"axis_{i}" for i in range(len(params.axes)))
    )
    if independent:
        return (
            base.select(*keys),
            RelationLayout(occurrences.layout.keys, None, (), ()),
            occurrences.source_ids,
        )
    return table.select(*keys, *(f"axis_{i}" for i in range(len(params.axes)))), layout, source_ids
