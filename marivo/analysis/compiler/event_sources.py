"""Governed source-private Event occurrence and temporal participant relations."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.event import EventStepRelation
from marivo.analysis.compiler.event_time import event_instant
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.contracts import EventDefinition, EventStepBinding
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.observation.coordinates import relationship_columns
from marivo.refs import ref
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic.ir import TargetSnapshotVersion, TargetValidityVersion
from marivo.semantic.validator import normalize_target_entity


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected="unique non-null Event occurrences and one exact participant at each occurrence instant",
        repair="Repair Event occurrence identities or the governed participant path and temporal coverage.",
    )


def _boolean(value: ir.Value) -> ir.BooleanValue:
    if not isinstance(value, ir.BooleanValue):
        raise compilation_error("an Ibis boolean predicate", "invalid Event source predicate")
    return value


def _version_at(
    table: ir.Table,
    columns: Mapping[str, str],
    version: TargetSnapshotVersion | TargetValidityVersion | None,
    instant: ir.Value,
) -> ir.BooleanValue:
    if isinstance(version, TargetSnapshotVersion):
        coordinate = table[columns[version.source_column]]
        return _boolean(coordinate == instant.cast("date").cast(coordinate.type()))
    if isinstance(version, TargetValidityVersion):
        start = table[columns[version.valid_from_column]]
        end = table[columns[version.valid_to_column]]
        boundary = instant.cast(start.type())
        open_end = ibis.literal(False)
        for value in version.open_end:
            open_end = open_end | (
                end.isnull()
                if value is None
                else _boolean(end == ibis.literal(value).cast(end.type()))
            )
        return _boolean(
            (start <= boundary)
            & (
                open_end
                | (end > boundary if version.interval == "closed_open" else end >= boundary)
            )
        )
    return ibis.literal(True)


def _participants(
    table: ir.Table,
    step: EventStepBinding,
    owner: ObservationOwner,
    tables: Mapping[str, ir.Table],
    add_validation: Callable[[CompiledValidation], None],
) -> ir.Table:
    current = step.source.ref.path
    columns = {name: name for name in table.columns}
    original = tuple(table.columns)
    valid_source = _version_at(table, columns, step.source.version, table["__event_instant"])
    add_validation(
        _check("event.source_version_at_occurrence", table.filter(~valid_source.fill_null(False)))
    )
    for index, path in enumerate(step.participant_path):
        relationship = owner.semantic_registry.relationships[path]
        if relationship.from_entity != current:
            raise compilation_error("the exact directed participant path", "reversed Event path")
        destination = normalize_target_entity(owner.semantic_registry, relationship.to_entity)
        right_source = tables[destination.ref.path].view()
        names = {name: f"__event_role_{index}_{name}" for name in right_source.columns}
        right = right_source.select(
            **{names[name]: right_source[name] for name in right_source.columns}
        )
        conditions = [
            _boolean(table[columns[left_key]] == right[names[right_key]])
            for left_key, right_key in relationship_columns(owner.semantic_registry, relationship)
        ]
        conditions.append(_version_at(right, names, destination.version, table["__event_instant"]))
        table = table.join(right, conditions, how="left")
        missing = ibis.literal(False)
        for key in destination.primary_key:
            missing = missing | table[names[key]].isnull()
        add_validation(_check("event.participant_missing", table.filter(missing)))
        grouped = table.group_by("__event_identity").aggregate(__count=table.count())
        add_validation(_check("event.participant_unique", grouped.filter(grouped.__count != 1)))
        columns = names
        current = destination.ref.path
    identity = ibis.struct({name: table[columns[name]] for name in step.subject.primary_key})
    return table.select(*original, entity_identity=identity)


def lower_event_sources(
    definition: EventDefinition,
    owner: ObservationOwner,
    tables: Mapping[str, ir.Table],
    membership: ir.Table,
    *,
    freeze: Callable[[ir.Table], ir.Table],
    add_validation: Callable[[CompiledValidation], None],
    from_inception: bool = False,
) -> tuple[EventStepRelation, ...]:
    """Resolve each Event once and each role at its own occurrence instant."""
    sources: dict[str, ir.Table] = {}
    roles: dict[tuple[str, tuple[str, ...]], ir.Table] = {}
    result: list[EventStepRelation] = []
    for step in definition.steps:
        event_ref = step.step.event
        event_key = event_ref.path
        if event_key not in sources:
            source = tables[step.source.ref.path]
            body = owner.sidecar.bodies.get(event_ref)
            if body is None:
                raise compilation_error("the captured Event expression body", "missing Event body")
            predicate = evaluate_expression_body(
                catalog_definition_fingerprint=definition.source_dependency_fingerprint,
                expression_sidecar=owner.sidecar,
                owning_ref=event_ref,
                body=body,
                entity_refs=(ref.entity(step.source.ref.path),),
                aliases=(source,),
            )
            source = source.filter(_boolean(predicate))
            identity = ibis.struct(
                {f"k{i}": source[axis.source_column] for i, axis in enumerate(step.identity)}
            )
            instant = event_instant(
                source[step.occurred_at.source_column], step.occurred_at, owner.semantic_registry
            )
            source = source.mutate(__event_identity=identity, __event_instant=instant)
            # Coverage and participant admission consume the exact requested range.
            source = source.filter(
                source.__event_instant.isnull()
                | (
                    (
                        ibis.literal(True)
                        if from_inception
                        else source.__event_instant >= definition.cohort_window.start
                    )
                    & (source.__event_instant < definition.completion_through)
                )
            )
            source = freeze(source)
            invalid = source.__event_instant.isnull()
            for axis in step.identity:
                component = source[axis.source_column]
                invalid = invalid | component.isnull()
                if isinstance(component, ir.FloatingValue):
                    invalid = invalid | component.isnan() | component.isinf()
            add_validation(_check("event.occurrence_valid", source.filter(invalid)))
            grouped = source.group_by("__event_identity").aggregate(__count=source.count())
            add_validation(_check("event.occurrence_unique", grouped.filter(grouped.__count != 1)))
            sources[event_key] = source
        role_key = (event_key, step.participant_path)
        if role_key not in roles:
            selected = _participants(sources[event_key], step, owner, tables, add_validation)
            selected = selected.select(
                "entity_identity",
                event_identity=selected.__event_identity,
                occurred_at=selected.__event_instant,
            )
            identity = selected.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error("a governed subject identity", "invalid Event participant")
            member = membership.select(*step.subject.primary_key).view()
            selected = selected.join(
                member,
                [_boolean(identity[name] == member[name]) for name in step.subject.primary_key],
                how="semi",
            )
            roles[role_key] = freeze(selected)
        result.append(EventStepRelation(event_key, step.step.key, roles[role_key]))
    return tuple(result)
