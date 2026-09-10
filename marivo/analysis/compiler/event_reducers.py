"""Native reductions over exact dense journey assignments and retained coverage."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.event import _timestamp, _valid_identity
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.completeness import EventCoverageFact, EventCoverageResolution
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.event import FirstPerSubject, PatternStep
from marivo.analysis.subject import DroppedBefore

FUNNEL_COUNTS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)
FUNNEL_COLUMNS = (
    "step_key",
    *FUNNEL_COUNTS[:-1],
    "conversion_from_first",
    "conversion_from_previous",
    "loss_rate_from_previous",
    "coverage_censored_count",
)
TIME_TO_EVENT_COLUMNS = (
    "journey_id",
    "entity_identity",
    "from_event_identity",
    "from_time",
    "to_event_identity",
    "to_time",
    "duration",
    "followup_until",
    "observed_duration",
    "completion_status",
)
TIME_TO_EVENT_STATUSES = (
    "complete",
    "incomplete",
    "coverage_censored",
    "not_entered",
    "entry_unknown",
)


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected="exact dense journey reductions with reconciled counts and complete selection truth",
        repair="Rebuild the unfiltered journey with complete Event coverage and valid governed identities.",
    )


def _violations(checks: tuple[CompiledValidation, ...]) -> ir.IntegerValue:
    counts = ibis.union(*(item.expression for item in checks))
    return counts.violations.sum().fill_null(0)


def _sum(value: ir.BooleanValue) -> ir.IntegerValue:
    return value.cast("int64").sum().fill_null(0)


def _ratio(numerator: ir.Value, denominator: ir.Value) -> ir.Value:
    return (denominator > 0).ifelse(numerator / denominator, ibis.null().cast("float64"))


def _step_index(semantics: EventJourneySemantics, step: PatternStep) -> int:
    indices = tuple(
        index for index, retained in enumerate(semantics.pattern.steps) if retained == step
    )
    if type(step) is not PatternStep or len(indices) != 1:
        raise compilation_error("one exact retained PatternStep", "foreign or ambiguous step")
    return indices[0]


def _facts(
    semantics: EventJourneySemantics, coverage: EventCoverageResolution
) -> dict[str, EventCoverageFact]:
    result = {fact.event_ref: fact for fact in coverage.events}
    if len(result) != len(coverage.events) or set(result) != {
        step.event.key for step in semantics.pattern.steps
    }:
        raise compilation_error(
            "coverage for exactly the retained Events", "changed coverage authority"
        )
    return result


def event_reach_states(
    table: ir.Table, *, semantics: EventJourneySemantics, coverage: EventCoverageResolution
) -> ir.Table:
    """Classify every step using the first missing assignment, entirely natively."""
    steps = semantics.pattern.steps
    facts = _facts(semantics, coverage)
    ordinal = ibis.cases(
        *((table.step_key == step.key, index) for index, step in enumerate(steps)),
        else_=len(steps),
    )
    states = table.mutate(__ordinal=ordinal, __reached=table.occurred_at.notnull())
    states = states.mutate(
        __first_missing=states.__ordinal.min(where=~states.__reached).over(
            group_by=states.journey_id
        ),
        __last_reached=states.occurred_at.max().over(group_by=states.journey_id),
    )
    unknown = ibis.cases(
        *(
            (
                states.__first_missing == index,
                ~_complete_followup(
                    states.__last_reached,
                    facts[step.event.key],
                    datetime.fromisoformat(semantics.completion_through),
                ),
            )
            for index, step in enumerate(steps)
        ),
        else_=False,
    )
    states = states.mutate(__unknown=~states.__reached & unknown)
    previous = states.select(
        __previous_journey=states.journey_id,
        __previous_ordinal=states.__ordinal + 1,
        __previous_reached=states.__reached,
    )
    joined = states.left_join(
        previous,
        (
            states.journey_id == previous.__previous_journey,
            states.__ordinal == previous.__previous_ordinal,
        ),
    )
    return joined.select(
        *states.columns,
        __entered=(joined.__ordinal == 0).ifelse(True, joined.__previous_reached),
    )


def _complete_followup(
    start: ir.Value, fact: EventCoverageFact, through: datetime
) -> ir.BooleanValue:
    if fact.complete_through is None:
        return ibis.literal(False)
    valid = ibis.literal(datetime.fromisoformat(fact.complete_through) >= through)
    if fact.complete_from is not None:
        valid = valid & (start >= ibis.literal(datetime.fromisoformat(fact.complete_from)))
    return valid.fill_null(False)


def _funnel_rows(
    states: ir.Table, *, step_keys: tuple[str, ...], axes: tuple[str, ...]
) -> ir.Table:
    rows: list[ir.Table] = []
    for index, key in enumerate(step_keys):
        selected = states.filter(states.step_key == key)
        aggregate = selected.aggregate(
            by=axes,
            cohort_count=selected.count(),
            resolved_cohort_count=_sum(~selected.__unknown),
            entry_count=_sum(selected.__entered),
            reached_count=_sum(selected.__reached),
            coverage_censored_count=_sum(selected.__entered & selected.__unknown),
        )
        aggregate = aggregate.mutate(
            resolved_entry_count=aggregate.entry_count - aggregate.coverage_censored_count
        )
        aggregate = aggregate.mutate(
            lost_count=aggregate.resolved_entry_count - aggregate.reached_count,
            step_key=ibis.literal(key),
        )
        aggregate = aggregate.mutate(
            conversion_from_first=_ratio(aggregate.reached_count, aggregate.resolved_cohort_count),
            conversion_from_previous=(
                _ratio(aggregate.reached_count, aggregate.resolved_entry_count)
                if index
                else ibis.null().cast("float64")
            ),
            loss_rate_from_previous=(
                _ratio(aggregate.lost_count, aggregate.resolved_entry_count)
                if index
                else ibis.null().cast("float64")
            ),
        )
        rows.append(aggregate.select(*axes, *FUNNEL_COLUMNS))
    return canonical_funnel_rows(ibis.union(*rows), step_keys=step_keys, axis_columns=axes)


def canonical_funnel_rows(
    table: ir.Table, *, step_keys: tuple[str, ...], axis_columns: tuple[str, ...] = ()
) -> ir.Table:
    """Order realized axis groups and retained steps deterministically."""
    ordinal = ibis.cases(
        *((table.step_key == key, index) for index, key in enumerate(step_keys)),
        else_=len(step_keys),
    )
    return table.order_by(*(table[name].asc(nulls_first=False) for name in axis_columns), ordinal)


def funnel_output_proof(
    table: ir.Table,
    *,
    step_keys: tuple[str, ...],
    axis_columns: tuple[str, ...] = (),
    require_dense: bool = True,
) -> ir.Table:
    """Validate cells, density, predecessor equations and finite recomputed rates."""
    if tuple(table.columns) != (*axis_columns, *FUNNEL_COLUMNS) or not step_keys:
        raise compilation_error(
            "the exact funnel schema and retained steps", "changed funnel schema"
        )
    valid = table.step_key.isin(step_keys)
    for name in FUNNEL_COUNTS:
        value = table[name]
        if not isinstance(value, ir.IntegerValue):
            raise compilation_error("integer funnel counts", "changed funnel count type")
        valid = valid & value.notnull() & (value >= 0)
    initial = table.step_key == step_keys[0]
    valid = (
        valid
        & (table.resolved_entry_count == table.entry_count - table.coverage_censored_count)
        & (table.lost_count == table.resolved_entry_count - table.reached_count)
        & (table.resolved_cohort_count <= table.cohort_count)
        & (table.coverage_censored_count <= table.cohort_count - table.resolved_cohort_count)
        & (table.entry_count <= table.cohort_count)
        & (table.resolved_entry_count <= table.resolved_cohort_count)
        & (
            table.conversion_from_first.identical_to(
                _ratio(table.reached_count, table.resolved_cohort_count)
            )
        )
        & table.conversion_from_previous.identical_to(
            initial.ifelse(
                ibis.null().cast("float64"), _ratio(table.reached_count, table.resolved_entry_count)
            )
        )
        & table.loss_rate_from_previous.identical_to(
            initial.ifelse(
                ibis.null().cast("float64"), _ratio(table.lost_count, table.resolved_entry_count)
            )
        )
        & (
            ~initial
            | (
                (table.reached_count == table.cohort_count)
                & (table.entry_count == table.cohort_count)
                & (table.resolved_cohort_count == table.cohort_count)
                & (table.coverage_censored_count == 0)
                & (table.lost_count == 0)
            )
        )
    )
    cells = table.group_by(*axis_columns, "step_key").aggregate(__rows=table.count())
    checks: tuple[CompiledValidation, ...] = (
        _check("event.funnel.rows", table.filter(~valid.fill_null(False))),
        _check("event.funnel.cells_unique", cells.filter(cells.__rows != 1)),
    )
    groups = table.aggregate(
        by=axis_columns,
        __steps=table.step_key.nunique(),
        __cohorts=table.cohort_count.nunique(),
        __cohort=table.cohort_count.max().fill_null(0),
    )
    if not require_dense:
        groups = groups.filter(groups.__steps > 0)
    if require_dense:
        checks += (
            _check(
                "event.funnel.density",
                groups.filter((groups.__steps != len(step_keys)) | (groups.__cohorts != 1)),
            ),
        )
        for previous_key, key in pairwise(step_keys):
            previous = table.filter(table.step_key == previous_key).select(
                **{f"__axis_{index}": table[name] for index, name in enumerate(axis_columns)},
                __preceding_reached=table.reached_count,
            )
            current = table.filter(table.step_key == key)
            conditions = tuple(
                current[name].identical_to(previous[f"__axis_{index}"])
                for index, name in enumerate(axis_columns)
            )
            pair = current.left_join(previous, conditions or (ibis.literal(True),))
            checks += (
                _check(
                    "event.funnel.predecessor",
                    pair.filter(~(pair.entry_count == pair.__preceding_reached).fill_null(False)),
                ),
            )
    return (
        table.aggregate(row_count=table.count())
        .cross_join(
            groups.aggregate(
                group_count=groups.count(), cohort_count=groups.__cohort.sum().fill_null(0)
            )
        )
        .mutate(violations=_violations(checks))
    )


def compile_event_funnel(
    table: ir.Table,
    *,
    semantics: EventJourneySemantics,
    coverage: EventCoverageResolution,
    axis_columns: tuple[str, ...] = (),
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Reduce native journey states into complete summary cells without rematching."""
    if type(semantics.matching) is not FirstPerSubject:
        raise compilation_error("first_per_subject journey matching", "repeated journey attempts")
    states = event_reach_states(table, semantics=semantics, coverage=coverage)
    keys = tuple(step.key for step in semantics.pattern.steps)
    rows = _funnel_rows(states, step_keys=keys, axes=axis_columns)
    anchors = table.filter(table.step_key == keys[0])
    counts = anchors.group_by("entity_identity").aggregate(__rows=anchors.count())
    checks: tuple[CompiledValidation, ...] = (
        _check("event.funnel.subject_unique", counts.filter(counts.__rows != 1)),
    )
    if axis_columns:
        ungrouped = _funnel_rows(states, step_keys=keys, axes=())
        grouped = rows.group_by("step_key").aggregate(
            **{name: rows[name].sum() for name in FUNNEL_COUNTS}
        )
        right = grouped.select(**{f"__grouped_{name}": grouped[name] for name in grouped.columns})
        compared = ungrouped.left_join(right, ungrouped.step_key == right.__grouped_step_key)
        equal: ir.BooleanValue = ibis.literal(True)
        for name in FUNNEL_COUNTS:
            equal = equal & (compared[name] == compared[f"__grouped_{name}"].fill_null(0))
        checks += (_check("event.funnel.reconciliation", compared.filter(~equal)),)
    proof = funnel_output_proof(rows, step_keys=keys, axis_columns=axis_columns)
    return rows, checks, proof


def canonical_time_to_event_rows(table: ir.Table) -> ir.Table:
    """Reconstruct attempt presentation order using retained public fields only."""
    return table.order_by(
        *(
            table[name].asc(nulls_first=False)
            for name in ("entity_identity", "from_time", "from_event_identity", "journey_id")
        )
    )


def _followup(
    start: ir.Value,
    *,
    facts: tuple[EventCoverageFact, ...],
    completion_through: datetime,
) -> ir.Value:
    result: ir.Value = ibis.literal(completion_through)
    for fact in facts:
        if fact.complete_through is None:
            return start
        end = ibis.literal(datetime.fromisoformat(fact.complete_through))
        valid = end >= start
        if fact.complete_from is not None:
            valid = valid & (ibis.literal(datetime.fromisoformat(fact.complete_from)) <= start)
        result = valid.ifelse(ibis.least(result, end), start)
    return ibis.greatest(result, start)


def compile_event_time_to_event(
    table: ir.Table,
    *,
    semantics: EventJourneySemantics,
    coverage: EventCoverageResolution,
    from_step: PatternStep,
    to_step: PatternStep,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Project one pair-local observation per attempt from exact retained assignments."""
    start_index, end_index = _step_index(semantics, from_step), _step_index(semantics, to_step)
    if start_index >= end_index:
        raise compilation_error("from_step preceding to_step", "reversed selected steps")
    states = event_reach_states(table, semantics=semantics, coverage=coverage)
    starts = states.filter(states.step_key == from_step.key).select(
        "journey_id",
        "entity_identity",
        from_event_identity=states.event_identity,
        from_time=states.occurred_at,
        __from_unknown=states.__unknown,
    )
    ends = states.filter(states.step_key == to_step.key).select(
        __to_journey=states.journey_id,
        to_event_identity=states.event_identity,
        to_time=states.occurred_at,
        __to_unknown=states.__unknown,
    )
    pairs = starts.join(ends, starts.journey_id == ends.__to_journey)
    entered = pairs.from_time.notnull()
    status = ibis.cases(
        (~entered & pairs.__from_unknown, "entry_unknown"),
        (~entered, "not_entered"),
        (pairs.to_time.notnull(), "complete"),
        (pairs.__to_unknown, "coverage_censored"),
        else_="incomplete",
    )
    pairs = pairs.mutate(completion_status=status)
    facts = _facts(semantics, coverage)
    through = datetime.fromisoformat(semantics.completion_through)
    prefix = _followup(
        pairs.from_time,
        facts=tuple(
            facts[step.event.key]
            for step in semantics.pattern.steps[start_index + 1 : end_index + 1]
        ),
        completion_through=through,
    )
    pairs = pairs.mutate(
        followup_until=ibis.cases(
            (pairs.completion_status == "complete", pairs.to_time),
            (pairs.completion_status == "incomplete", ibis.literal(through)),
            (pairs.completion_status == "coverage_censored", prefix),
            else_=ibis.null().cast(pairs.from_time.type()),
        )
    )
    pairs = pairs.mutate(
        duration=_timestamp(pairs.to_time).delta(_timestamp(pairs.from_time), unit="microsecond"),
        observed_duration=_timestamp(pairs.followup_until).delta(
            _timestamp(pairs.from_time), unit="microsecond"
        ),
    )
    rows = canonical_time_to_event_rows(pairs.select(*TIME_TO_EVENT_COLUMNS))
    return rows, (), time_to_event_output_proof(rows, completion_through=through)


def time_to_event_output_proof(table: ir.Table, *, completion_through: datetime) -> ir.Table:
    """Validate closed pair-local states, exact duration equations and unique attempts."""
    if tuple(table.columns) != TIME_TO_EVENT_COLUMNS:
        raise compilation_error(
            "the exact ten-column time-to-event schema", "changed attempt schema"
        )
    complete = table.completion_status == "complete"
    entered = table.completion_status.isin(TIME_TO_EVENT_STATUSES[:3])
    valid = (
        table.journey_id.notnull()
        & _valid_identity(table.entity_identity)
        & table.completion_status.isin(TIME_TO_EVENT_STATUSES)
        & (table.from_time.notnull() == entered)
        & (table.from_event_identity.notnull() == entered)
        & (table.to_time.notnull() == complete)
        & (table.to_event_identity.notnull() == complete)
        & (table.duration.notnull() == complete)
        & (table.followup_until.notnull() == entered)
        & (table.observed_duration.notnull() == entered)
        & (~entered | _valid_identity(table.from_event_identity))
        & (~complete | _valid_identity(table.to_event_identity))
        & (
            ~entered
            | (
                (table.followup_until >= table.from_time)
                & (table.followup_until <= ibis.literal(completion_through))
                & (
                    table.observed_duration
                    == _timestamp(table.followup_until).delta(
                        _timestamp(table.from_time), unit="microsecond"
                    )
                )
            )
        )
        & (
            ~complete
            | (
                (table.to_time >= table.from_time)
                & (table.followup_until == table.to_time)
                & (
                    table.duration
                    == _timestamp(table.to_time).delta(
                        _timestamp(table.from_time), unit="microsecond"
                    )
                )
            )
        )
        & (
            (table.completion_status != "incomplete")
            | (table.followup_until == ibis.literal(completion_through))
        )
    )
    groups = table.group_by("journey_id").aggregate(__count=table.count())
    checks = (
        _check("event.time_to_event.rows", table.filter(~valid.fill_null(False))),
        _check("event.time_to_event.journey_unique", groups.filter(groups.__count != 1)),
    )
    return table.aggregate(
        row_count=table.count(),
        subject_count=table.entity_identity.nunique(),
        **{
            status + "_count": _sum(table.completion_status == status)
            for status in TIME_TO_EVENT_STATUSES
        },
    ).mutate(violations=_violations(checks))


def compile_event_subject_selection(
    table: ir.Table,
    *,
    semantics: EventJourneySemantics,
    coverage: EventCoverageResolution,
    selection: DroppedBefore,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Select complete resolved-loss membership and expose an upstream uncertainty fence."""
    if type(selection) is not DroppedBefore or type(semantics.matching) is not FirstPerSubject:
        raise compilation_error(
            "DroppedBefore on first_per_subject journeys", "unsupported selection"
        )
    if _step_index(semantics, selection.step) == 0:
        raise compilation_error("a non-initial retained PatternStep", "initial step selection")
    states = event_reach_states(table, semantics=semantics, coverage=coverage)
    targets = states.filter(states.step_key == selection.step.key)
    selected = targets.filter(targets.__entered & ~targets.__reached & ~targets.__unknown)
    rows = selected.select("entity_identity").distinct().order_by("entity_identity")
    subjects = targets.group_by("entity_identity").aggregate(__count=targets.count())
    checks = (
        _check("event.selection.subject_unique", subjects.filter(subjects.__count != 1)),
        _check(
            "event.selection.identity_valid",
            targets.filter(~_valid_identity(targets.entity_identity)),
        ),
        CompiledValidation(
            "event.selection.truth_complete",
            targets.aggregate(violations=_sum(targets.__unknown)),
            expected="complete selection truth for every input subject, including a complete empty result",
            repair="Supply exact coverage for the missing Event inputs and rebuild the unfiltered journey.",
        ),
    )
    proof = targets.aggregate(
        input_subject_count=targets.entity_identity.nunique(),
        selected_subject_count=_sum(targets.__entered & ~targets.__reached & ~targets.__unknown),
        unknown_subject_count=_sum(targets.__unknown),
    ).mutate(violations=_violations(checks))
    return rows, checks, proof
