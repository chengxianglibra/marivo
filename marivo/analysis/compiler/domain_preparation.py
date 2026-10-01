"""Ibis occurrence preparation; no packet SQL or identity-derived business order."""

from __future__ import annotations

from functools import reduce
from operator import or_

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
    TemporalCheck,
    canonical_layout,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.compiler.source_time import source_time
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.rules import OccurrencePrepare, PreparedObservation
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


def version_checks(
    table: ir.Table,
    binding: SourceBinding,
    keys: tuple[str, ...],
    output: str,
    source_ids: tuple[str, ...],
    checks: list[LoweredCheck],
) -> None:
    """Verify version intervals, including overlaps with no occurrence at the overlap."""
    source_ids = (binding.leaf.identity,)
    version = binding.leaf.definition.version
    if isinstance(version, TargetSnapshotVersion):
        counts = table.group_by(*keys, version.source_column).aggregate(__count=table.count())
        bad = counts.filter((counts.__count > 1) | counts[version.source_column].isnull())
        checks.append(
            IntegrityCheck(output, "r7.input_binding: unique snapshot version", bad, source_ids)
        )
    elif isinstance(version, TargetValidityVersion):

        def open_end(end: ir.Value) -> ir.BooleanValue:
            return reduce(
                or_,
                (
                    end.isnull() if value is None else end == ibis.literal(value).cast(end.type())
                    for value in version.open_end
                ),
                ibis.literal(False),
            )

        start, end = table[version.valid_from_column], table[version.valid_to_column]
        invalid = start.isnull() | (
            ~open_end(end) & (end < start if version.interval == "closed_closed" else end <= start)
        )
        checks.append(
            IntegrityCheck(
                output,
                "r7.input_binding: well-formed validity interval",
                table.filter(invalid),
                source_ids,
            )
        )
        left = table.select(
            **{f"left_key_{i}": table[key] for i, key in enumerate(keys)},
            left_start=start,
            left_end=end,
        ).view()
        right = table.select(
            **{f"right_key_{i}": table[key] for i, key in enumerate(keys)},
            right_start=start,
            right_end=end,
        ).view()
        before_right_end = open_end(right.right_end) | (
            left.left_start <= right.right_end
            if version.interval == "closed_closed"
            else left.left_start < right.right_end
        )
        before_left_end = open_end(left.left_end) | (
            right.right_start <= left.left_end
            if version.interval == "closed_closed"
            else right.right_start < left.left_end
        )
        joined = left.join(
            right,
            [
                *(left[f"left_key_{i}"] == right[f"right_key_{i}"] for i in range(len(keys))),
                before_right_end,
                before_left_end,
            ],
        )
        groups = joined.group_by(
            *(left[f"left_key_{i}"] for i in range(len(keys))), left.left_start
        ).aggregate(__count=joined.count())
        checks.append(
            IntegrityCheck(
                output,
                "r7.input_binding: non-overlapping validity versions",
                groups.filter(groups.__count > 1),
                source_ids,
            )
        )


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

    def check(expected: str, bad: ir.Table) -> None:
        checks.append(
            IntegrityCheck(
                stage.output, expected, bad.select(violation=ibis.literal(1)), current_ids
            )
        )

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
        version_checks(
            table,
            bound,
            tuple(field.source_column for field in event.identity),
            stage.output,
            current_ids,
            checks,
        )
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
        raw_time = capture_time(table[event.occurred_at.source_column])
        instant, _ = source_time(
            raw_time,
            event.occurred_at,
            boundary_timezone="UTC",
            read_timezone=event.occurred_at.timezone,
            engine="duckdb",
        )
        instant = instant.cast("timestamp('UTC')")
        table = table.mutate(__instant=instant, __raw_time=raw_time)
        invalid = table.__instant.isnull() | reduce(
            or_,
            (table[field.source_column].isnull() for field in event.identity),
            ibis.literal(False),
        )
        check("r7.occurrence_identity: non-null keys and governed instant", table.filter(invalid))
        if params.start is not None:
            table = table.filter(
                table.__instant >= ibis.literal(params.start).cast(table.__instant.type())
            )
        table = table.filter(
            table.__instant < ibis.literal(params.end).cast(table.__instant.type())
        )
        check(
            "r7.input_binding: source version at occurrence",
            table.filter(~_version(table, bound, table.__instant).fill_null(False)),
        )
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
            version_checks(
                destination,
                destination_binding,
                tuple(key for _, key in hop.keys),
                stage.output,
                current_ids,
                checks,
            )
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
            check(
                "r7.input_binding: complete participant mapping",
                joined.filter(destination[hop.keys[0][1]].isnull()),
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
        occurrence_columns = [table[f"__occ_{i}"] for i in range(len(event.identity))]
        counts = table.group_by(occurrence_columns).aggregate(__count=table.count())
        check(
            "r7.occurrence_identity: unique occurrence and participant version",
            counts.filter(counts.__count != 1),
        )
        selected = table.join(
            members.expression,
            [
                table[f"__subject_{i}"] == members.expression[key.column]
                for i, key in enumerate(members.layout.keys)
            ],
        )
        checks.append(
            TemporalCheck(
                stage.output,
                selected.select(
                    raw_time=selected.__raw_time, normalized_time=selected.__instant
                ).distinct(),
                tuple(dict.fromkeys((*current_ids, *members.source_ids))),
                event.occurred_at,
            )
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
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    from marivo.analysis.compiler.graph_lowering import CoordinateColumn, _contribution_rows
    from marivo.analysis.core.model import Coordinate

    params = stage.node.parameters
    assert isinstance(params, PreparedObservation)
    observation = params.observation
    rows, ids = _contribution_rows(stage, observation, bindings, relations, checks, prepared=True)
    rows = rows.mutate(event_time=rows.event_time.cast("timestamp('UTC')"))
    current_ids = ids
    invalid = rows.event_time.isnull() | reduce(
        or_,
        (rows[name].isnull() for name in rows.columns if name.startswith("candidate__key_")),
        ibis.literal(False),
    )
    checks.append(
        IntegrityCheck(
            stage.output,
            "r7.input_binding: complete candidate key and time",
            rows.filter(invalid),
            current_ids,
        )
    )
    assert observation.start is not None and observation.end is not None
    rows = rows.filter(
        (rows.event_time >= ibis.literal(observation.start).cast(rows.event_time.type()))
        & (rows.event_time < ibis.literal(observation.end).cast(rows.event_time.type()))
    )
    mapping = members.expression
    selected = rows.semi_join(
        mapping,
        [rows[f"member_{i}"] == mapping[key.column] for i, key in enumerate(members.layout.keys)],
    )
    ids = tuple(dict.fromkeys((*ids, *members.source_ids)))
    checks.append(
        TemporalCheck(
            stage.output,
            selected.select(
                raw_time=selected.__raw_event_time,
                normalized_time=selected.event_time.cast("date")
                if isinstance(selected.__raw_event_time, ir.DateValue)
                else selected.event_time,
            ).distinct(),
            ids,
            observation.event,
        )
    )
    selected = selected.drop("__raw_event_time")
    names = tuple(name for name in rows.columns if name.startswith("candidate__key_"))
    counts = selected.group_by(*names).aggregate(__count=selected.count())
    checks.append(
        IntegrityCheck(
            stage.output,
            "r7.input_binding: single historical contribution mapping",
            counts.filter(counts.__count != 1),
            ids,
        )
    )
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
