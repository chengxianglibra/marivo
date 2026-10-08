"""Ibis occurrence preparation; no packet SQL or identity-derived business order."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import reduce
from operator import or_

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
    canonical_layout,
    captured_match_check,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.compiler.source_time import encoded_time_predicate, source_time
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.rules import (
    ObserveCount,
    ObserveMetric,
    OccurrencePrepare,
    PreparedObservation,
)
from marivo.refs import ref
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic.ir import TargetSnapshotVersion, TargetValidityVersion


def capture_time(value: ir.Value) -> ir.TimestampValue:
    """Adopt the qualified driver's native microsecond carrier before all checks."""
    if not isinstance(value, ir.TimestampValue):
        fail(
            "physical_qualification",
            f"occurrence time requires timestamp, received {value.type()}",
            stage="lowering",
        )
    kind = value.type()
    if kind.scale is not None and kind.scale > 6:
        value = value.cast("timestamp('UTC')" if kind.timezone is not None else "timestamp")
    assert isinstance(value, ir.TimestampValue)
    return value


def _version(table: ir.Table, binding: SourceBinding, instant: ir.Value) -> ir.BooleanValue:
    from marivo.analysis.compiler.source_time import render, timestamp

    version = binding.leaf.definition.version
    if isinstance(version, TargetSnapshotVersion):
        column = table[version.source_column]
        point = render(version.timezone or "UTC", timestamp(instant)).cast("date")
        actual = (
            render(version.timezone or "UTC", timestamp(column)).cast("date")
            if column.type().is_timestamp() and column.type().timezone is not None
            else column.cast("date")
        )
        return actual == point
    if isinstance(version, TargetValidityVersion):
        start, end = table[version.valid_from_column], table[version.valid_to_column]
        open_end = reduce(
            or_,
            (
                end.isnull() if value is None else end == ibis.literal(value).cast(end.type())
                for value in version.open_end
            ),
            ibis.literal(False),
        )
        point = (
            instant
            if start.type().is_timestamp() and start.type().timezone is not None
            else render(version.timezone or "UTC", timestamp(instant))
        )
        return (start <= point) & (
            open_end | (end > point if version.interval == "closed_open" else end >= point)
        )
    return ibis.literal(True)


def lower_occurrences(
    stage: SourceMethodStage,
    members: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    assert isinstance(params, OccurrencePrepare)
    owned = {leaf.identity for leaf in stage.node.sources}
    sources = {
        binding.leaf.identity: binding for binding in bindings if binding.leaf.identity in owned
    }
    by_entity = {binding.leaf.definition.ref.path: binding for binding in sources.values()}
    rows: list[ir.Table] = []
    source_ids = tuple(
        dict.fromkeys(
            (*members.source_ids, *(binding.leaf.identity for binding in sources.values()))
        )
    )

    current_ids: tuple[str, ...] = ()

    for event in params.events:
        bound = sources.get(event.source_id)
        if (
            bound is None
            or bound.leaf.definition.ref.path != event.source.ref.path
            or bound.leaf.definition.version != event.source.version
        ):
            fail("input_binding", f"missing exact source for {event.ref.path}", stage="lowering")
        current_ids = (bound.leaf.identity,)
        if bound.leaf.definition.fingerprint != event.source.dependency_fingerprint:
            fail("input_binding", "Event source definition fingerprint differs", stage="lowering")
        table = bound.source.relation.view()
        if any(
            not (
                (field.logical_type == "string" and table[field.source_column].type().is_string())
                or (field.logical_type == "int64" and table[field.source_column].type().is_int64())
            )
            for field in event.identity
        ):
            fail(
                "occurrence_identity",
                "Event identity requires string/int64 columns",
                stage="lowering",
            )
        if event.predicate_kind == "filtered":
            sidecar = bound.expression_sidecar
            body = None if sidecar is None else sidecar.bodies.get(event.ref)
            if sidecar is None or body is None or body.body_ast_hash != event.predicate_hash:
                fail(
                    "input_binding",
                    f"Event predicate differs for {event.ref.path}",
                    stage="lowering",
                )
            predicate = evaluate_expression_body(
                catalog_definition_fingerprint=event.dependency_fingerprint,
                expression_sidecar=sidecar,
                owning_ref=event.ref,
                body=body,
                entity_refs=(ref.entity(event.source.ref.path),),
                aliases=(table,),
            )
            if not isinstance(predicate, ir.BooleanValue):
                fail("input_binding", "Event predicate is not Boolean", stage="lowering")
            table = table.filter(predicate)
        raw_time = table[event.occurred_at.source_column]
        if isinstance(raw_time, ir.TimestampValue):
            raw_time = capture_time(raw_time)
        instant, authority = source_time(
            raw_time,
            event.occurred_at,
            boundary_timezone="UTC",
            read_timezone=event.occurred_at.timezone,
            engine=bound.leaf.definition.shape.backend,
        )
        instant = instant.cast(dt.Timestamp(timezone="UTC", scale=6))
        table = table.mutate(__instant=instant, __raw_time=raw_time)
        start = None if params.start is None else datetime.fromisoformat(params.start)
        end = datetime.fromisoformat(params.end)
        encoded = encoded_time_predicate(
            table.__raw_time,
            event.occurred_at,
            authority,
            start=None if start is None else start.astimezone(timezone.utc).replace(tzinfo=None),
            end=end.astimezone(timezone.utc).replace(tzinfo=None),
        )
        if encoded is not None:
            table = table.filter(encoded)
        else:
            if start is not None:
                table = table.filter(
                    table.__instant >= ibis.literal(start, type=table.__instant.type())
                )
            table = table.filter(table.__instant < ibis.literal(end, type=table.__instant.type()))
        original = {field.source_column: table[field.source_column] for field in event.identity}
        sequence_int: ir.Value = ibis.null().cast("int64")
        sequence_enum: ir.Value = ibis.null().cast("string")
        if params.order is not None:
            for sequence, field in zip(
                params.order.definition.sequences, params.order.fields, strict=True
            ):
                if sequence.event_ref == event.ref.path:
                    value = table[field.source_column]
                    if sequence.order == "integer":
                        if not value.type().is_integer():
                            fail(
                                "business_order",
                                f"integer sequence received {value.type()}",
                                stage="lowering",
                            )
                        sequence_int = value.cast("int64")
                    else:
                        if not value.type().is_string():
                            fail(
                                "business_order",
                                f"enum sequence received {value.type()}",
                                stage="lowering",
                            )
                        sequence_enum = value
        table = table.select(
            **{f"__occ_{i}": value for i, value in enumerate(original.values())},
            __instant=table.__instant,
            __raw_time=table.__raw_time,
            __sequence_int=sequence_int,
            __sequence_enum=sequence_enum,
            **{f"__join_{i}": table[source] for i, (source, _) in enumerate(event.path[0].keys)}
            if event.path
            else {f"__subject_{i}": table[key] for i, key in enumerate(event.subject.primary_key)},
        )
        for index, hop in enumerate(event.path):
            destination_binding = by_entity.get(hop.to_entity_ref.path)
            if destination_binding is None:
                fail(
                    "input_binding",
                    f"missing participant dependency {hop.to_entity_ref.path}",
                    stage="lowering",
                )
            current_ids = tuple(dict.fromkeys((*current_ids, destination_binding.leaf.identity)))
            destination = destination_binding.source.relation.view()
            if destination_binding.leaf.definition.ref.path == event.subject.ref.path and (
                destination_binding.leaf.definition.fingerprint
                != event.subject.dependency_fingerprint
                or destination_binding.leaf.definition.version != event.subject.version
            ):
                fail(
                    "input_binding",
                    "participant Subject version/fingerprint differs",
                    stage="lowering",
                )
            joined = table.left_join(
                destination,
                [
                    *(
                        table[f"__join_{i}"] == destination[key]
                        for i, (_, key) in enumerate(hop.keys)
                    ),
                    _version(destination, destination_binding, table.__instant),
                ],
            )
            checks.append(
                captured_match_check(
                    stage,
                    f"event:{event.ref.path}:hop:{index}",
                    joined.filter(destination[hop.keys[0][1]].isnull()),
                    current_ids,
                )
            )
            fields = {name: table[name] for name in table.columns if not name.startswith("__join_")}
            fields.update(
                {
                    f"__join_{i}": destination[source]
                    for i, (source, _) in enumerate(event.path[index + 1].keys)
                }
                if index + 1 < len(event.path)
                else {
                    f"__subject_{i}": destination[key]
                    for i, key in enumerate(event.subject.primary_key)
                }
            )
            table = joined.select(**fields)
        selected = table.join(
            members.expression,
            [
                table[f"__subject_{i}"] == members.expression[key.column]
                for i, key in enumerate(members.layout.keys)
            ],
        )
        fields = {
            "key_0": ibis.literal(event.ref.path),
            **{f"key_{i + 1}": selected[f"__occ_{i}"] for i in range(len(event.identity))},
            **{
                f"subject__key_{i}": selected[f"__subject_{i}"]
                for i in range(len(event.subject.primary_key))
            },
            "occurrences__occurred_at": selected.__instant,
            "occurrences__sequence_int": selected.__sequence_int,
            "occurrences__sequence_enum": selected.__sequence_enum,
        }
        rows.append(selected.select(**fields))
    result = reduce(lambda first, second: first.union(second, distinct=False), rows)
    return result, canonical_layout(stage.node.signature, has_value=False), source_ids


def lower_candidates(
    stage: SourceMethodStage,
    members: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    relations: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
    *,
    captured_columns: tuple[tuple[str, str], ...] = (),
    observation: ObserveMetric | ObserveCount | None = None,
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    from marivo.analysis.compiler.graph_lowering import CoordinateColumn, _contribution_rows
    from marivo.analysis.core.model import Coordinate

    params = stage.node.parameters
    if observation is None:
        assert isinstance(params, PreparedObservation)
        observation = params.observation
    rows, ids, authority = _contribution_rows(
        stage,
        observation,
        bindings,
        relations,
        checks,
        prepared=True,
        captured_columns=captured_columns,
    )
    rows = rows.mutate(event_time=rows.event_time.cast(dt.Timestamp(timezone="UTC", scale=6)))
    assert observation.start is not None and observation.end is not None
    start, end = (datetime.fromisoformat(value) for value in (observation.start, observation.end))
    if start.utcoffset() is None or end.utcoffset() is None:
        fail(
            "input_binding",
            "prepared observation bounds require explicit timezone",
            stage="lowering",
        )
    start, end = (value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start, end))
    encoded = encoded_time_predicate(
        rows.__raw_event_time, observation.event, authority, start=start, end=end
    )
    rows = rows.filter(
        encoded
        if encoded is not None
        else (rows.event_time >= ibis.literal(start, type=rows.event_time.type()))
        & (rows.event_time < ibis.literal(end, type=rows.event_time.type()))
    )
    mapping = members.expression
    selected = rows.semi_join(
        mapping,
        [rows[f"member_{i}"] == mapping[key.column] for i, key in enumerate(members.layout.keys)],
    )
    ids = tuple(dict.fromkeys((*ids, *members.source_ids)))
    selected = selected.drop("__raw_event_time")
    keys = tuple(
        CoordinateColumn(Coordinate(observation.contribution, name, "identity"), name)
        for name in rows.columns
        if name.startswith("candidate__key_")
    )
    ids = tuple(dict.fromkeys((*ids, *members.source_ids)))
    layout = RelationLayout(
        keys,
        None,
        (),
        tuple(name for name in selected.columns if name not in {key.column for key in keys}),
    )
    return selected, layout, ids
