"""Governed checkpoint Dimension preparation on the complete original Subject domain."""

from __future__ import annotations

from datetime import datetime

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.domain_preparation import _version
from marivo.analysis.compiler.graph_lowering import (
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.rules import HistoryAxesPrepare


def lower_axes(
    stage: SourceMethodStage,
    occurrences: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    assert isinstance(params, HistoryAxesPrepare)
    sources = {b.leaf.identity: b for b in bindings}
    keys = tuple(k.column for k in occurrences.layout.keys)
    base = occurrences.expression
    table = base.select(*keys)
    source_ids = tuple(
        dict.fromkeys((*occurrences.source_ids, *(n.identity for n in stage.node.sources)))
    )
    for point_index, checkpoint in enumerate(params.request.at):
        for axis_index, axis in enumerate(params.request.axes):
            route = []
            for index, hop in enumerate(axis.path):
                forward = hop.from_entity_ref == axis.entities[index].ref
                route.append(
                    hop.keys if forward else tuple((right, left) for left, right in hop.keys)
                )
            # Retain the Subject image separately: each axis starts at the same entry instant.
            current = base.select(
                **{name: base[name] for name in keys},
                __instant=ibis.literal(datetime.fromisoformat(checkpoint)),
                **{f"__join_{i}": base[keys[i]] for i in range(len(axis.subject.primary_key))},
            )
            for index, entity in enumerate(axis.entities):
                bound = sources[axis.source_ids[index]]
                raw = bound.source.relation.view()
                join_keys = (
                    entity.primary_key
                    if index == 0
                    else tuple(right for _, right in route[index - 1])
                )
                joined = current.left_join(
                    raw,
                    [
                        *(current[f"__join_{i}"] == raw[key] for i, key in enumerate(join_keys)),
                        _version(raw, bound, current.__instant),
                    ],
                )
                fields = {name: current[name] for name in (*keys, "__instant")}
                if index < len(route):
                    fields.update(
                        {f"__join_{i}": raw[left] for i, (left, _) in enumerate(route[index])}
                    )
                else:
                    fields[f"axis_{point_index}_{axis_index}"] = raw[axis.dimension.source_column]
                current = joined.select(**fields)
            table = table.join(current, [table[key] == current[key] for key in keys]).select(
                **{name: table[name] for name in table.columns},
                **{f"axis_{point_index}_{axis_index}": current[f"axis_{point_index}_{axis_index}"]},
            )
    extras = tuple(
        f"axis_{point}_{axis}"
        for point in range(len(params.request.at))
        for axis in range(len(params.request.axes))
    )
    layout = RelationLayout(occurrences.layout.keys, None, (), extras)
    return table.select(*keys, *extras), layout, source_ids
