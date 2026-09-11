"""Source-native Lifecycle projections and complete at-instant membership."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.domains.lifecycle import PART_COLUMNS, ROLES, LifecycleSemantics
from marivo.analysis.domains.lifecycle_reducers import (
    FIELDS,
    REDUCER_TYPES,
    DistributionSemantics,
    DwellSemantics,
    LifecycleReducerPayload,
    LifecycleSelectionPayload,
    TransitionsSemantics,
    ViolationsSemantics,
    history_semantics,
)


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        "lifecycle." + name,
        bad.aggregate(violations=bad.count()),
        expected="exact retained history and complete at-instant subject authority",
        repair="Use an intact history Artifact and a checkpoint inside its proven coverage; repair source-origin coverage before creating new history.",
    )


def _sum(value: ir.BooleanValue) -> ir.IntegerValue:
    return value.cast("int64").sum().fill_null(0)


def _ratio(n: ir.Value, denominator: ir.Value) -> ir.Value:
    return (n.cast("float64") / denominator.nullif(0).cast("float64")).cast("float64")


def _states(history: LifecycleSemantics) -> ir.Table:
    rows = [ibis.literal(s).name("model_state").as_table() for s in history.states]
    return rows[0].union(*rows[1:], distinct=False) if len(rows) > 1 else rows[0]


def state_at(
    table: ir.Table,
    ledger: ir.Table,
    history: LifecycleSemantics,
    at: datetime,
    *,
    freeze: Callable[[ir.Table], ir.Table],
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Classify every retained subject; observed intervals never establish coverage."""
    end = datetime.fromisoformat(history.source.cohort_end)
    known = ledger.known_through.notnull() & (
        (ledger.known_through == ibis.literal(end))
        if at == end
        else (ibis.literal(at) < ledger.known_through)
    )
    seeded = known & ledger.inception_at.notnull() & (ledger.inception_at <= ibis.literal(at))
    coverage = freeze(ledger.select("entity_identity", __known=known, __seeded=seeded))
    intervals = table.filter(
        (table.valid_from < ibis.literal(at)) & (table.valid_to == ibis.literal(at))
        if at == end
        else (table.valid_from <= ibis.literal(at)) & (ibis.literal(at) < table.valid_to)
    )
    intervals = intervals.select(__subject=intervals.entity_identity, __state=intervals.model_state)
    result = coverage.left_join(intervals, coverage.entity_identity == intervals.__subject)
    counts = result.group_by("entity_identity").aggregate(__count=result.count())
    checks = (
        _check("at.unique", counts.filter(counts.__count != 1)),
        _check(
            "at.interval",
            result.filter(
                (result.__seeded & result.__state.isnull())
                | (result.__known & ~result.__seeded & result.__state.notnull())
            ),
        ),
    )
    return result.select("entity_identity", "__known", "__seeded", "__state"), checks


def reduce_lifecycle(
    table: ir.Table,
    parts: Mapping[str, ir.Table],
    payload: LifecycleReducerPayload | LifecycleSelectionPayload,
    *,
    freeze: Callable[[ir.Table], ir.Table],
    enrich: Callable[[ir.Table, datetime], ir.Table] | None = None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table | None]:
    """Consume registered exact roles without reopening the original trigger source."""
    from marivo.analysis.domains.lifecycle_reducers import consumed_roles

    if not consumed_roles(payload).issubset(parts):
        raise compilation_error(
            "the exact retained Lifecycle role", "missing required history part"
        )
    history = (
        payload.history
        if isinstance(payload, LifecycleSelectionPayload)
        else history_semantics(payload.semantics)
    )
    checks: list[CompiledValidation] = []
    if isinstance(payload, LifecycleSelectionPayload):
        states, validation = state_at(
            table, parts[ROLES[1]], history, payload.selection.at, freeze=freeze
        )
        checks.extend(validation)
        checks.append(_check("selection.complete_membership", states.filter(~states.__known)))
        result = states.filter(
            states.__seeded & (states.__state == payload.selection.state.name)
        ).select("entity_identity")
        proof = states.aggregate(
            input_subject_count=states.count(),
            unknown_subject_count=_sum(~states.__known),
            selected_subject_count=_sum(
                states.__seeded & (states.__state == payload.selection.state.name)
            ),
        )
        return result, tuple(checks), proof
    semantics = payload.semantics
    if isinstance(semantics, DistributionSemantics):
        outputs: list[ir.Table] = []
        axes = tuple(b.dimension.ref.path.rsplit(".", 1)[-1] for b in payload.axes)
        for point in semantics.at:
            at = datetime.fromisoformat(point)
            states, validation = state_at(table, parts[ROLES[1]], history, at, freeze=freeze)
            checks.extend(validation)
            if enrich is not None:
                states = enrich(states, at)
            cross = states.cross_join(_states(history))
            output = cross.group_by(*axes, "model_state").aggregate(
                subject_count=_sum(cross.__seeded & (cross.__state == cross.model_state)),
                known_subject_count=_sum(cross.__seeded),
                coverage_censored_subject_count=_sum(~cross.__known),
            )
            if not axes:
                dense = _states(history)
                output = dense.left_join(output, "model_state").select(
                    "model_state",
                    **{
                        n: ibis._[n].fill_null(0).cast("int64")
                        for n in (
                            "subject_count",
                            "known_subject_count",
                            "coverage_censored_subject_count",
                        )
                    },
                )
            output = output.mutate(
                as_of=ibis.literal(at),
                share=_ratio(output.subject_count, output.known_subject_count),
            )
            outputs.append(
                output.select(
                    *axes,
                    "as_of",
                    "model_state",
                    "subject_count",
                    "known_subject_count",
                    "coverage_censored_subject_count",
                    "share",
                )
            )
        result = outputs[0].union(*outputs[1:], distinct=False) if len(outputs) > 1 else outputs[0]
    elif isinstance(semantics, TransitionsSemantics):
        trace = parts[ROLES[0]]
        trace = trace.filter(
            (trace.occurred_at >= ibis.literal(datetime.fromisoformat(history.source.cohort_start)))
            & (trace.occurred_at < ibis.literal(datetime.fromisoformat(history.source.cohort_end)))
        )
        pairs = history.transition_pairs
        rows = [
            ibis.literal(a)
            .name("from_model_state")
            .as_table()
            .mutate(to_model_state=ibis.literal(b))
            for a, b in pairs
        ]
        dense = (
            rows[0].union(*rows[1:], distinct=False)
            if len(rows) > 1
            else rows[0]
            if rows
            else trace.select("from_model_state", "to_model_state").limit(0)
        )
        counts = trace.group_by("from_model_state", "to_model_state").aggregate(
            transition_count=trace.count()
        )
        result = dense.left_join(counts, ("from_model_state", "to_model_state"))
        result = result.mutate(transition_count=result.transition_count.fill_null(0).cast("int64"))
        result = result.mutate(
            share_of_modeled_transitions=_ratio(result.transition_count, trace.count())
        )
    elif isinstance(semantics, DwellSemantics):
        completed = table.interval_status == "completed"
        start, end = table.valid_from, table.valid_to
        if not isinstance(start, ir.TimestampValue) or not isinstance(end, ir.TimestampValue):
            raise compilation_error("typed history instants", "invalid interval types")
        duration = end.delta(start, unit="microsecond")
        values = table.group_by("model_state").aggregate(
            interval_count=table.count(),
            completed_count=_sum(completed),
            right_censored_count=_sum(table.interval_status == "right_censored"),
            coverage_censored_count=_sum(table.interval_status == "coverage_censored"),
            left_clipped_completed_count=_sum(completed & table.left_clipped),
            mean_duration=duration.mean(where=completed),
            median_duration=duration.quantile(0.5, where=completed),
            p90_duration=duration.quantile(0.9, where=completed),
        )
        result = _states(history).left_join(values, "model_state")
        count_names = (
            "interval_count",
            "completed_count",
            "right_censored_count",
            "coverage_censored_count",
            "left_clipped_completed_count",
        )
        result = result.mutate(
            **{n: result[n].fill_null(0).cast("int64") for n in count_names},
            **{
                n: result[n].cast("float64")
                for n in ("mean_duration", "median_duration", "p90_duration")
            },
        )
    else:
        result = parts[ROLES[2]].select(*PART_COLUMNS[2])
    shape = semantics.kind.split("/")[1].split("@")[0]
    prefix = (
        tuple(b.dimension.ref.path.rsplit(".", 1)[-1] for b in payload.axes)
        if isinstance(semantics, DistributionSemantics)
        else ("entity_identity",)
        if isinstance(semantics, ViolationsSemantics)
        else ()
    )
    return result.select(*prefix, *(f[0] for f in FIELDS[shape])), tuple(checks), None


def canonical_rows(table: ir.Table, row: DatasetRowContract) -> ir.Table:
    semantics = row.family_semantics
    if not isinstance(semantics, REDUCER_TYPES):
        return table.order_by("entity_identity", "valid_from")
    history = history_semantics(semantics)

    def ordinal(value: ir.Value) -> ir.Value:
        return ibis.cases(
            *((value == state, i) for i, state in enumerate(history.states)),
            else_=len(history.states),
        )

    if isinstance(semantics, DistributionSemantics):
        return table.order_by(
            "as_of",
            *(f.name for f in row.schema.columns if f.role_id == "dimension"),
            ordinal(table.model_state),
        )
    if isinstance(semantics, TransitionsSemantics):
        pairs = history.transition_pairs
        return (
            table.order_by(
                ibis.cases(
                    *(
                        ((table.from_model_state == a) & (table.to_model_state == b), i)
                        for i, (a, b) in enumerate(pairs)
                    ),
                    else_=len(pairs),
                )
            )
            if pairs
            else table
        )
    if isinstance(semantics, DwellSemantics):
        return table.order_by(ordinal(table.model_state))
    return table.order_by(
        "entity_identity", "occurred_at", "trigger_event_ref", "trigger_event_identity"
    )
