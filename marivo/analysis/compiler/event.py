"""Source-native Event assignment and identity-free journey integrity proofs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.event import EveryStart, FirstPerSubject

_COLUMNS = (
    "journey_id",
    "completion_status",
    "entity_identity",
    "step_key",
    "event_identity",
    "occurred_at",
    "elapsed_from_start",
    "elapsed_from_previous",
)


@dataclass(frozen=True, slots=True, repr=False)
class EventStepRelation:
    """One role projection of an action-owned occurrence realization."""

    event_ref: str
    step_key: str
    expression: ir.Table


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    ambiguous = name == "event.ambiguous_event_order"
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected=(
            "one unambiguous governed occurrence assignment"
            if ambiguous
            else "dense ordered journeys with complete non-null governed occurrence identities"
        ),
        repair=(
            "Model a more precise occurrence time or governed occurrence identity order."
            if ambiguous
            else "Repair Event identity, participant, occurrence-time or retained journey integrity."
        ),
    )


def _struct(value: ir.Value) -> ir.StructValue:
    if not isinstance(value, ir.StructValue) or not value.type().names:
        raise compilation_error("a non-empty governed identity struct", "invalid Event identity")
    return value


def _timestamp(value: ir.Value) -> ir.TimestampValue:
    if not isinstance(value, ir.TimestampValue) or value.type().timezone != "UTC":
        raise compilation_error("a UTC occurrence timestamp", "invalid Event occurrence time")
    return value


def _valid_identity(value: ir.Value) -> ir.BooleanValue:
    identity = _struct(value)
    valid = identity.notnull()
    for name in identity.type().names:
        component = identity[name]
        valid = valid & component.notnull()
        if isinstance(component, ir.FloatingValue):
            valid = valid & ~component.isnan() & ~component.isinf()
    return valid.fill_null(False)


def _normalized_identity(value: ir.Value) -> ir.StructValue:
    identity = _struct(value)
    components: dict[str, ir.Value] = {}
    for name in identity.type().names:
        component = identity[name]
        components[name] = (
            (component == 0).ifelse(ibis.literal(0).cast(component.type()), component)
            if isinstance(component, ir.FloatingValue)
            else component
        )
    return ibis.struct(components)


def event_journey_id(
    subject: ir.Value, anchor_identity: ir.Value, *, definition_digest: str
) -> ir.StringValue:
    """Digest an ordered subject/anchor payload entirely inside the source."""
    payload = (
        ibis.struct(
            {
                "entity_identity": _normalized_identity(subject),
                "anchor_event_identity": _normalized_identity(anchor_identity),
            }
        )
        .cast("json")
        .cast("string")
    )
    encoded = ibis.literal("event_journey@v1:" + definition_digest + ":") + payload
    digest = ibis.literal("journey_") + encoded.hexdigest("sha256")
    if not isinstance(digest, ir.StringValue):
        raise compilation_error("a native journey digest", "invalid Event digest expression")
    return digest


def _duration(current: ir.Value, previous: ir.Value) -> ir.IntegerValue:
    return _timestamp(current).delta(_timestamp(previous), unit="microsecond")


def _input_checks(steps: tuple[EventStepRelation, ...]) -> tuple[CompiledValidation, ...]:
    checks: list[CompiledValidation] = []
    seen: set[int] = set()
    for index, step in enumerate(steps):
        table = step.expression
        if id(table.op()) in seen:
            continue
        seen.add(id(table.op()))
        valid = (
            _valid_identity(table.entity_identity)
            & _valid_identity(table.event_identity)
            & _timestamp(table.occurred_at).notnull()
        )
        identities = table.group_by("event_identity").aggregate(__count=table.count())
        checks.extend(
            (
                _check(f"event.input.{index}.identity_valid", table.filter(~valid)),
                _check(
                    f"event.input.{index}.identity_unique",
                    identities.filter(identities.__count > 1),
                ),
            )
        )
    return tuple(checks)


def _ordered_occurrences(
    steps: tuple[EventStepRelation, ...], *, cohort_start: datetime, completion_through: datetime
) -> tuple[ir.Table, ...]:
    relations = tuple(
        step.expression.filter(
            (step.expression.occurred_at >= ibis.literal(cohort_start))
            & (step.expression.occurred_at < ibis.literal(completion_through))
        ).select("entity_identity", "event_identity", "occurred_at")
        for step in steps
    )
    keys = ibis.union(*relations, distinct=True)
    order = [
        keys.occurred_at,
        *(_struct(keys.event_identity)[n] for n in _struct(keys.event_identity).type().names),
    ]
    keys = keys.mutate(
        __order=ibis.dense_rank().over(group_by=keys.entity_identity, order_by=order)
    )
    result: list[ir.Table] = []
    for relation in relations:
        result.append(
            relation.join(
                keys,
                predicates=(
                    relation.entity_identity == keys.entity_identity,
                    relation.event_identity == keys.event_identity,
                    relation.occurred_at == keys.occurred_at,
                ),
            ).select(
                relation.entity_identity,
                relation.event_identity,
                relation.occurred_at,
                keys.__order,
            )
        )
    return tuple(result)


def _attempts(
    relations: tuple[ir.Table, ...],
    steps: tuple[EventStepRelation, ...],
    *,
    cohort_end: datetime,
    matching: FirstPerSubject | EveryStart,
    inclusive_ties: bool,
) -> ir.Table:
    """Walk earliest successors; reserve only the final role of exclusive attempts."""
    anchors = relations[0].filter(relations[0].occurred_at < ibis.literal(cohort_end))
    anchors = anchors.mutate(
        __attempt=ibis.row_number().over(group_by=anchors.entity_identity, order_by=anchors.__order)
    )
    if isinstance(matching, FirstPerSubject):
        anchors = anchors.filter(anchors.__attempt == 0)
    attempts = anchors.select(
        "entity_identity",
        "__attempt",
        __identity_0=anchors.event_identity,
        __time_0=anchors.occurred_at,
        __order_0=anchors.__order,
    )
    for index, relation in enumerate(relations[1:], start=1):
        previous = attempts[f"__order_{index - 1}"]
        require_later: ir.BooleanValue = ibis.literal(not inclusive_ties)
        if inclusive_ties:
            for earlier in range(index):
                if steps[earlier].event_ref == steps[index].event_ref:
                    require_later = require_later | (attempts[f"__order_{earlier}"] == previous)
        attempts = attempts.mutate(__minimum_order=previous + require_later.cast("int64"))
        candidate = relation.mutate(
            __final_ordinal=ibis.row_number().over(
                group_by=relation.entity_identity, order_by=relation.__order
            )
        ).select(
            __subject=relation.entity_identity,
            **{
                f"__identity_{index}": relation.event_identity,
                f"__time_{index}": relation.occurred_at,
                f"__order_{index}": relation.__order,
                "__final_ordinal": ibis._.__final_ordinal,
            },
        )
        joined = attempts.asof_join(
            candidate,
            on=attempts.__minimum_order <= candidate[f"__order_{index}"],
            predicates=attempts.entity_identity == candidate.__subject,
        ).drop("__subject", "__minimum_order")
        # ASOF execution can retain a candidate for a null left ordering key.
        # Missing-step propagation remains an explicit domain condition.
        previous_present = joined[f"__order_{index - 1}"].notnull()
        joined = joined.mutate(
            **{
                name: previous_present.ifelse(joined[name], ibis.null().cast(joined[name].type()))
                for name in (
                    f"__identity_{index}",
                    f"__time_{index}",
                    f"__order_{index}",
                    "__final_ordinal",
                )
            }
        )
        if (
            index == len(steps) - 1
            and isinstance(matching, EveryStart)
            and matching.completion_assignment == "exclusive"
        ):
            eligible = joined.filter(joined.__final_ordinal.notnull())
            eligible = eligible.mutate(
                __eligible_ordinal=ibis.row_number().over(
                    group_by=eligible.entity_identity, order_by=eligible.__attempt
                )
            )
            # For ordered eligibility thresholds r_i, greedy reservation obeys
            # assigned_i = i + max(r_j - j, j <= i).
            eligible = eligible.mutate(
                __offset=eligible.__final_ordinal - eligible.__eligible_ordinal
            )
            eligible = eligible.mutate(
                __assigned=eligible.__eligible_ordinal
                + eligible.__offset.max().over(
                    group_by=eligible.entity_identity,
                    order_by=eligible.__eligible_ordinal,
                    rows=(None, 0),
                )
            )
            reserved = eligible.select("entity_identity", "__attempt", "__assigned")
            base = attempts.drop("__minimum_order")
            base = base.left_join(
                reserved,
                predicates=(
                    base.entity_identity == reserved.entity_identity,
                    base.__attempt == reserved.__attempt,
                ),
            ).select(*base.columns, reserved.__assigned)
            joined = base.left_join(
                candidate,
                predicates=(
                    base.entity_identity == candidate.__subject,
                    base.__assigned == candidate.__final_ordinal,
                ),
            ).drop("__subject", "__assigned")
        attempts = joined.drop("__final_ordinal")
    return attempts


def _ambiguity_check(strict: ir.Table, inclusive: ir.Table, size: int) -> CompiledValidation:
    """Compare tie-excluding and tie-admitting assignment without choosing an Event order."""
    right = inclusive.select(
        __subject=inclusive.entity_identity,
        __anchor=inclusive.__order_0,
        **{f"__other_{i}": inclusive[f"__order_{i}"] for i in range(1, size)},
    )
    pairs = strict.join(
        right,
        predicates=(strict.entity_identity == right.__subject, strict.__order_0 == right.__anchor),
    )
    ambiguous: ir.BooleanValue = ibis.literal(False)
    for index in range(1, size):
        original = pairs[f"__order_{index}"]
        alternative = pairs[f"__other_{index}"]
        ambiguous = ambiguous | ~original.identical_to(alternative)
    return _check("event.ambiguous_event_order", pairs.filter(ambiguous))


def _dense_rows(
    attempts: ir.Table,
    steps: tuple[EventStepRelation, ...],
    *,
    definition_digest: str,
    coverage_complete: bool,
) -> ir.Table:
    status = (
        attempts[f"__order_{len(steps) - 1}"]
        .notnull()
        .ifelse("complete", "incomplete" if coverage_complete else "coverage_censored")
    )
    rows: list[ir.Table] = []
    for index, step in enumerate(steps):
        rows.append(
            attempts.select(
                journey_id=event_journey_id(
                    attempts.entity_identity,
                    attempts.__identity_0,
                    definition_digest=definition_digest,
                ),
                completion_status=status,
                entity_identity=attempts.entity_identity,
                step_key=ibis.literal(step.step_key),
                event_identity=attempts[f"__identity_{index}"],
                occurred_at=attempts[f"__time_{index}"],
                elapsed_from_start=_duration(attempts[f"__time_{index}"], attempts.__time_0),
                elapsed_from_previous=_duration(
                    attempts[f"__time_{index}"], attempts[f"__time_{max(index - 1, 0)}"]
                ),
                __anchor_time=attempts.__time_0,
                __anchor_identity=attempts.__identity_0,
                __step_ordinal=ibis.literal(index),
            )
        )
    dense = ibis.union(*rows)
    return dense.order_by(
        "entity_identity", "__anchor_time", "__anchor_identity", "__step_ordinal"
    ).select(*_COLUMNS)


def _summary(table: ir.Table, violations: ir.IntegerValue) -> ir.Table:
    journeys = table.select("journey_id", "completion_status").distinct()
    return (
        table.aggregate(
            row_count=table.count(),
            subject_count=table.entity_identity.nunique(),
            matched_row_count=table.occurred_at.count(),
            missing_row_count=table.occurred_at.isnull().cast("int64").sum().fill_null(0),
        )
        .cross_join(
            journeys.aggregate(
                journey_count=journeys.count(),
                complete_journey_count=(journeys.completion_status == "complete")
                .cast("int64")
                .sum()
                .fill_null(0),
                incomplete_journey_count=(journeys.completion_status == "incomplete")
                .cast("int64")
                .sum()
                .fill_null(0),
                censored_journey_count=(journeys.completion_status == "coverage_censored")
                .cast("int64")
                .sum()
                .fill_null(0),
            )
        )
        .mutate(violations=violations)
    )


def compile_event_match(
    steps: tuple[EventStepRelation, ...],
    *,
    matching: FirstPerSubject | EveryStart,
    cohort_start: datetime,
    cohort_end: datetime,
    completion_through: datetime,
    definition_digest: str,
    coverage_complete: bool,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Lower the frozen matching policy without reading or collecting occurrence rows."""
    if not steps or len({step.step_key for step in steps}) != len(steps):
        raise compilation_error("non-empty distinct PatternStep keys", "invalid Event pattern")
    subject_type = _struct(steps[0].expression.entity_identity).type()
    event_type = _struct(steps[0].expression.event_identity).type()
    if any(
        _struct(step.expression.entity_identity).type() != subject_type
        or _struct(step.expression.event_identity).type() != event_type
        for step in steps
    ):
        raise compilation_error(
            "homogeneous governed identity structs", "incompatible Event identities"
        )
    relations = _ordered_occurrences(
        steps, cohort_start=cohort_start, completion_through=completion_through
    )
    strict = _attempts(
        relations, steps, cohort_end=cohort_end, matching=matching, inclusive_ties=False
    )
    checks = _input_checks(steps)
    if len(steps) > 1 and len({step.event_ref for step in steps}) > 1:
        inclusive = _attempts(
            relations, steps, cohort_end=cohort_end, matching=matching, inclusive_ties=True
        )
        checks += (_ambiguity_check(strict, inclusive, len(steps)),)
    rows = _dense_rows(
        strict, steps, definition_digest=definition_digest, coverage_complete=coverage_complete
    )
    return rows, checks, _summary(rows, ibis.literal(0, type="int64"))


def canonical_event_rows(table: ir.Table, step_keys: tuple[str, ...]) -> ir.Table:
    """Reconstruct the family-owned presentation order from retained rows alone."""
    anchors = table.filter(table.step_key == step_keys[0]).select(
        __journey=table.journey_id,
        __anchor_time=table.occurred_at,
        __anchor_identity=table.event_identity,
    )
    ordered = table.left_join(anchors, table.journey_id == anchors.__journey)
    ordinal = ibis.cases(
        *((ordered.step_key == key, index) for index, key in enumerate(step_keys)),
        else_=len(step_keys),
    )
    return ordered.order_by(
        ordered.entity_identity, ordered.__anchor_time, ordered.__anchor_identity, ordinal
    ).select(*_COLUMNS)


def _identity_after(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
    first, second = _struct(left), _struct(right)
    equal: ir.BooleanValue = ibis.literal(True)
    after: ir.BooleanValue = ibis.literal(False)
    for name in first.type().names:
        after = after | (equal & (first[name] > second[name]))
        equal = equal & (first[name] == second[name])
    return after


def event_output_proof(
    table: ir.Table,
    *,
    step_keys: tuple[str, ...],
    event_refs: tuple[str, ...],
    matching: FirstPerSubject | EveryStart,
    cohort_start: datetime,
    cohort_end: datetime,
    completion_through: datetime,
    definition_digest: str,
    coverage_complete: bool,
) -> ir.Table:
    """Validate retained journey authority using only source-native scalar counts."""
    if not step_keys or len(step_keys) != len(event_refs):
        raise compilation_error("one Event per retained PatternStep", "invalid journey authority")
    if tuple(table.columns) != _COLUMNS:
        raise compilation_error("the exact eight-column journey schema", "changed journey schema")
    present = table.occurred_at.notnull()
    row_valid = (
        _valid_identity(table.entity_identity)
        & table.journey_id.notnull()
        & table.step_key.isin(step_keys)
        & table.completion_status.isin(("complete", "incomplete", "coverage_censored"))
        & (present.cast("int64") == table.event_identity.notnull().cast("int64"))
        & (present.cast("int64") == table.elapsed_from_start.notnull().cast("int64"))
        & (present.cast("int64") == table.elapsed_from_previous.notnull().cast("int64"))
        & (~present | _valid_identity(table.event_identity))
        & (
            ~present
            | (
                (table.occurred_at >= ibis.literal(cohort_start))
                & (table.occurred_at < ibis.literal(completion_through))
                & (table.elapsed_from_start >= 0)
                & (table.elapsed_from_previous >= 0)
            )
        )
    )
    checks = [_check("event.output.rows", table.filter(~row_valid.fill_null(False)))]
    groups = table.group_by("journey_id").aggregate(
        __rows=table.count(),
        __steps=table.step_key.nunique(),
        __subjects=table.entity_identity.nunique(),
        __statuses=table.completion_status.nunique(),
    )
    checks.append(
        _check(
            "event.output.density",
            groups.filter(
                (groups.__rows != len(step_keys))
                | (groups.__steps != len(step_keys))
                | (groups.__subjects != 1)
                | (groups.__statuses != 1)
            ),
        )
    )
    anchors = table.filter(table.step_key == step_keys[0])
    anchor_valid = (
        anchors.occurred_at.notnull()
        & (anchors.occurred_at >= ibis.literal(cohort_start))
        & (anchors.occurred_at < ibis.literal(cohort_end))
        & (anchors.elapsed_from_start == 0)
        & (anchors.elapsed_from_previous == 0)
        & (
            anchors.journey_id
            == event_journey_id(
                anchors.entity_identity,
                anchors.event_identity,
                definition_digest=definition_digest,
            )
        )
    )
    checks.append(_check("event.output.anchors", anchors.filter(~anchor_valid.fill_null(False))))
    unique_anchors = anchors.group_by("entity_identity", "event_identity").aggregate(
        __count=anchors.count()
    )
    checks.append(
        _check("event.output.anchor_unique", unique_anchors.filter(unique_anchors.__count > 1))
    )
    if isinstance(matching, FirstPerSubject):
        subjects = anchors.group_by("entity_identity").aggregate(__count=anchors.count())
        checks.append(_check("event.output.subject_unique", subjects.filter(subjects.__count > 1)))
    previous = anchors.select(
        "journey_id",
        __previous_identity=anchors.event_identity,
        __previous_time=anchors.occurred_at,
        __anchor_time=anchors.occurred_at,
    )
    for index, key in enumerate(step_keys[1:], start=1):
        current = table.filter(table.step_key == key)
        paired = current.left_join(previous, current.journey_id == previous.journey_id).select(
            *current.columns,
            previous.__previous_identity,
            previous.__previous_time,
            previous.__anchor_time,
        )
        reached = paired.occurred_at.notnull()
        after = (paired.occurred_at > paired.__previous_time) | (
            (paired.occurred_at == paired.__previous_time)
            & _identity_after(paired.event_identity, paired.__previous_identity)
        )
        valid = ~reached | (
            paired.__previous_time.notnull()
            & after
            & (paired.elapsed_from_start == _duration(paired.occurred_at, paired.__anchor_time))
            & (
                paired.elapsed_from_previous
                == _duration(paired.occurred_at, paired.__previous_time)
            )
        )
        checks.append(_check(f"event.output.step.{index}", paired.filter(~valid.fill_null(False))))
        for earlier, earlier_ref in enumerate(event_refs[:index]):
            if earlier_ref != event_refs[index]:
                continue
            used = table.filter(table.step_key == step_keys[earlier]).select(
                __journey=table.journey_id, __used_identity=table.event_identity
            )
            duplicates = current.join(used, current.journey_id == used.__journey)
            checks.append(
                _check(
                    f"event.output.repeated.{earlier}.{index}",
                    duplicates.filter(
                        duplicates.event_identity.notnull()
                        & (duplicates.event_identity == duplicates.__used_identity)
                    ),
                )
            )
        previous = paired.select(
            "journey_id",
            __previous_identity=paired.event_identity,
            __previous_time=paired.occurred_at,
            __anchor_time=paired.__anchor_time,
        )
    final = table.filter(table.step_key == step_keys[-1])
    expected_status = final.occurred_at.notnull().ifelse(
        "complete", "incomplete" if coverage_complete else "coverage_censored"
    )
    checks.append(
        _check(
            "event.output.status",
            final.filter(~final.completion_status.identical_to(expected_status)),
        )
    )
    if isinstance(matching, EveryStart) and matching.completion_assignment == "exclusive":
        completions = final.filter(final.occurred_at.notnull())
        final_counts = completions.group_by("entity_identity", "event_identity").aggregate(
            __count=completions.count()
        )
        checks.append(
            _check("event.output.final_unique", final_counts.filter(final_counts.__count > 1))
        )
    violations = ibis.union(*(check.expression for check in checks))
    total = violations.aggregate(violations=violations.violations.sum())
    counts = _summary(table, ibis.literal(0, type="int64")).drop("violations")
    return counts.cross_join(total)
