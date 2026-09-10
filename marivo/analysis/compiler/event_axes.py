"""Source-native governed subject-axis enrichment at exact journey entry instants."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.event import _struct
from marivo.analysis.compiler.event_sources import _boolean, _version_at
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.contracts import EventAxisBinding
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import normalize_target_entity


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected="exactly one governed subject-axis row at each first-step occurrence instant",
        repair="Repair subject identities, governed to-one keys or temporal intervals at journey entry.",
    )


def _validate_join(
    table: ir.Table,
    entity: TargetEntityContract,
    names: Mapping[str, str],
    add_validation: Callable[[CompiledValidation], None],
) -> None:
    missing: ir.BooleanValue = ibis.literal(False)
    for key in entity.primary_key:
        missing = missing | table[names[key]].isnull()
    add_validation(_check("event.axes.missing", table.filter(missing)))
    counts = table.group_by("__axis_subject").aggregate(__count=table.count())
    add_validation(_check("event.axes.unique", counts.filter(counts.__count != 1)))


def lower_event_axes(
    table: ir.Table,
    bindings: tuple[EventAxisBinding, ...],
    owner: ObservationOwner,
    tables: Mapping[str, ir.Table],
    *,
    step_key: str,
    freeze: Callable[[ir.Table], ir.Table],
    add_validation: Callable[[CompiledValidation], None],
) -> ir.Table:
    """Enrich exact journeys using captured Dimension paths, never occurrence sources."""
    if not bindings:
        return table
    anchors = table.filter(table.step_key == step_key).select(
        __axis_subject=table.entity_identity, __axis_instant=table.occurred_at
    )
    anchors = freeze(anchors)
    output = table
    for axis_index, binding in enumerate(bindings):
        subject = binding.subject
        identity = _struct(anchors.__axis_subject)
        source = tables[subject.ref.path].view()
        names = {name: f"__axis_{axis_index}_subject_{name}" for name in source.columns}
        right = source.select(**{new: source[old] for old, new in names.items()})
        conditions = tuple(
            _boolean(identity[key] == right[names[key]]) for key in subject.primary_key
        )
        joined = anchors.left_join(
            right, (*conditions, _version_at(right, names, subject.version, anchors.__axis_instant))
        )
        _validate_join(joined, subject, names, add_validation)
        current = subject.ref.path
        for hop, path in enumerate(binding.path):
            relationship = owner.semantic_registry.relationships[path]
            if current not in (relationship.from_entity, relationship.to_entity):
                raise compilation_error("the captured connected axis path", "changed axis path")
            forward = current == relationship.from_entity
            target = relationship.to_entity if forward else relationship.from_entity
            entity = normalize_target_entity(owner.semantic_registry, target)
            source = tables[target].view()
            renamed = {name: f"__axis_{axis_index}_hop_{hop}_{name}" for name in source.columns}
            right = source.select(**{new: source[old] for old, new in renamed.items()})
            conditions = tuple(
                _boolean(
                    joined[names[key.from_key if forward else key.to_key]]
                    == right[renamed[key.to_key if forward else key.from_key]]
                )
                for key in relationship.keys
            )
            joined = joined.left_join(
                right,
                (*conditions, _version_at(right, renamed, entity.version, joined.__axis_instant)),
            )
            _validate_join(joined, entity, renamed, add_validation)
            names, current = renamed, target
        if current != binding.dimension.entity_ref.path:
            raise compilation_error("the captured Dimension owner", "changed axis destination")
        name = binding.dimension.ref.path.rsplit(".", 1)[-1]
        values = joined.select(
            "__axis_subject", **{name: joined[names[binding.dimension.source_column]]}
        )
        if not binding.dimension.nullable:
            add_validation(_check("event.axes.non_nullable", values.filter(values[name].isnull())))
        values = freeze(values)
        output = output.left_join(values, output.entity_identity == values.__axis_subject).drop(
            "__axis_subject"
        )
    return output
