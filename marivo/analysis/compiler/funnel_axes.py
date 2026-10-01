"""Governed entry-time Dimension joins over the original occurrence preparation."""

from __future__ import annotations

from datetime import datetime

import ibis.expr.types as ir

from marivo.analysis.compiler.domain_preparation import _version, version_checks
from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.rules import FunnelAxesPrepare


def lower_axes(
    stage: SourceMethodStage,
    occurrences: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
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
    for axis_index, axis in enumerate(params.axes):
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
            version_checks(raw, bound, join_keys, stage.output, current_ids, checks)
            joined = current.left_join(
                raw,
                [
                    *(current[f"__join_{i}"] == raw[key] for i, key in enumerate(join_keys)),
                    _version(raw, bound, current.__instant),
                ],
            )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "r7.funnel_axes: complete historical path",
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
                fields[f"axis_{axis_index}"] = raw[axis.dimension.source_column]
                if not axis.dimension.nullable:
                    checks.append(
                        IntegrityCheck(
                            stage.output,
                            "r7.funnel_axes: declared non-null Dimension",
                            joined.filter(raw[axis.dimension.source_column].isnull()),
                            current_ids,
                        )
                    )
            current = joined.select(**fields)
        counts = current.group_by(*keys).aggregate(__count=current.count())
        checks.append(
            IntegrityCheck(
                stage.output,
                "r7.funnel_axes: exact entry mapping",
                counts.filter(counts.__count != 1),
                current_ids,
            )
        )
        table = table.join(current, [table[key] == current[key] for key in keys]).select(
            **{
                name: table[name]
                for name in (
                    *keys,
                    "occurrences__occurred_at",
                    *(f"axis_{i}" for i in range(axis_index)),
                )
            },
            **{f"axis_{axis_index}": current[f"axis_{axis_index}"]},
        )
    layout = RelationLayout(
        occurrences.layout.keys, None, (), tuple(f"axis_{i}" for i in range(len(params.axes)))
    )
    return table.select(*keys, *(f"axis_{i}" for i in range(len(params.axes)))), layout, source_ids
