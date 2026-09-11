"""Native finite-state replay and lossless canonical history projections."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
from sqlglot import expressions as sge

from marivo.analysis.compiler.event import EventStepRelation
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.lifecycle import ROLES, LifecycleSemantics


def literal(value: str) -> str:
    return sge.Literal.string(value).sql(dialect="duckdb")


def known_through(
    semantics: LifecycleSemantics, coverage: EventCoverageResolution
) -> datetime | None:
    if not coverage.events or any(
        f.basis == "unknown" or f.complete_from is not None or f.complete_through is None
        for f in coverage.events
    ):
        return None
    return min(
        datetime.fromisoformat(semantics.source.cohort_end),
        *(
            datetime.fromisoformat(f.complete_through)
            for f in coverage.events
            if f.complete_through is not None
        ),
    )


def check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        f"lifecycle.{name}",
        bad.aggregate(violations=bad.count()),
        expected="complete inception authority and deterministic canonical replay",
        repair="Repair modeled Event history, source-origin coverage or occurrence ordering; retry the complete replay without reconstructing retained parts.",
    )


def _evaluation(semantics: LifecycleSemantics, state: str, trigger: str) -> tuple[str, str]:
    inception = f"{trigger} IN ({', '.join(literal(x) for x in semantics.inceptions)})"
    terminal = (
        f"{state} IN ({', '.join(literal(x) for x in semantics.terminals)})"
        if semantics.terminals
        else "FALSE"
    )
    rules = [
        (f"{state} = {literal(a)} AND {trigger} = {literal(b)}", literal(c))
        for a, b, c in semantics.transitions
    ]
    target = (
        "CASE "
        + " ".join(f"WHEN {condition} THEN {value}" for condition, value in rules)
        + " ELSE NULL END"
        if rules
        else "NULL::VARCHAR"
    )
    after = f"CASE WHEN {state} IS NULL THEN CASE WHEN {inception} THEN {literal(semantics.initial)} END WHEN {terminal} OR {inception} THEN {state} ELSE coalesce({target}, {state}) END"
    kind = f"CASE WHEN {state} IS NULL THEN CASE WHEN {inception} THEN 'inception' ELSE 'pre_inception' END WHEN {terminal} THEN 'transition_from_terminal' WHEN {inception} THEN 'illegal_transition' WHEN ({target}) IS NOT NULL THEN 'legal_transition' ELSE 'illegal_transition' END"
    return after, kind


def _ambiguity_check(
    combined: ir.Table, replay: ir.Table, semantics: LifecycleSemantics
) -> CompiledValidation:
    """Prove confluence of equal governed-order groups inside the source engine.

    UNION merges equivalent subset/state/outcome prefixes; only a tied group,
    never the complete subject history, is represented by each subset ledger.
    Only occurrences of the same Event share governed identity order; independent
    Event identities cannot order cross-Event interleavings.
    """
    source = sge.to_identifier(combined.get_name(), quoted=True).sql(dialect="duckdb")
    replay_name = sge.to_identifier(replay.get_name(), quoted=True).sql(dialect="duckdb")
    after, kind = _evaluation(semantics, "p.state", "o.trigger_key")
    query = f"""WITH RECURSIVE groups AS (
        SELECT entity_identity, occurred_at, min(ordinal) lo, max(ordinal) hi
        FROM {source} GROUP BY ALL HAVING count(*) > 1
    ), paths AS (
        SELECT g.entity_identity, g.lo, g.hi, range(g.lo, g.hi+1) remaining,
            r.state_before AS state,
            []::STRUCT(ordinal BIGINT, evaluation VARCHAR, violation_state VARCHAR)[] outcomes
        FROM groups g JOIN {replay_name} r ON r.entity_identity=g.entity_identity AND r.ordinal=g.lo
        UNION
        SELECT p.entity_identity, p.lo, p.hi,
            list_filter(p.remaining, x -> x <> o.ordinal), {after},
            list_sort(list_append(p.outcomes, struct_pack(ordinal := o.ordinal,
                evaluation := {kind}, violation_state := CASE WHEN ({kind}) IN ('illegal_transition','transition_from_terminal') THEN p.state END)))
        FROM paths p JOIN {source} o ON p.entity_identity=o.entity_identity AND list_contains(p.remaining,o.ordinal)
        WHERE NOT EXISTS (
            SELECT 1 FROM {source} preceding
            WHERE preceding.entity_identity=o.entity_identity
                AND preceding.trigger_event_ref=o.trigger_event_ref
                AND preceding.event_identity<o.event_identity
                AND list_contains(p.remaining,preceding.ordinal)
        )
    ) SELECT count(*)::BIGINT AS violations FROM (
        SELECT entity_identity, lo FROM paths WHERE len(remaining)=0
        GROUP BY ALL HAVING count(DISTINCT struct_pack(state := state, outcomes := outcomes)) > 1
    )"""
    expression = ops.SQLStringView(
        combined.op(),
        f"SELECT * FROM ({query}) AS lifecycle_confluence",
        ibis.schema({"violations": "int64"}),
    ).to_expr()
    return CompiledValidation(
        "lifecycle.ambiguous_event_order",
        expression,
        expected="one state and per-occurrence violation outcome for every compatible same-time order",
        repair="Model a more precise occurrence time or a compatible governed identity order; declaration and physical row order cannot repair ambiguous replay.",
    )


def compile_replay(
    occurrences: tuple[EventStepRelation, ...],
    membership: ir.Table,
    semantics: LifecycleSemantics,
    coverage: EventCoverageResolution,
    *,
    freeze: Callable[[ir.Table], ir.Table],
) -> tuple[ir.Table, tuple[tuple[str, ir.Table], ...], tuple[CompiledValidation, ...]]:
    """Replay ordered source-private rows without collecting identities in Python."""
    streams = tuple(
        item.expression.mutate(
            trigger_key=ibis.literal(item.step_key),
            trigger_event_ref=ibis.literal(f"event:{item.event_ref}"),
        )
        for item in occurrences
    )
    combined = ibis.union(*streams) if len(streams) > 1 else streams[0]
    order = [combined.occurred_at, combined.event_identity, combined.trigger_key]
    combined = combined.mutate(
        ordinal=(
            ibis.row_number().over(ibis.window(group_by=combined.entity_identity, order_by=order))
            + 1
        ).cast("int64")
    )
    combined = freeze(combined)
    name = sge.to_identifier(combined.get_name(), quoted=True).sql(dialect="duckdb")
    after, kind = _evaluation(semantics, "r.state_after", "o.trigger_key")
    query = f"""WITH RECURSIVE replay AS (
        SELECT DISTINCT entity_identity, 0::BIGINT AS ordinal, NULL::VARCHAR AS state_before, NULL::VARCHAR AS state_after, NULL::VARCHAR AS evaluation FROM {name}
        UNION ALL
        SELECT o.entity_identity, o.ordinal, r.state_after, {after}, {kind}
        FROM replay r JOIN {name} o ON r.entity_identity = o.entity_identity AND o.ordinal = r.ordinal + 1
    ) SELECT o.*, r.state_before, r.state_after, r.evaluation FROM replay r JOIN {name} o USING (entity_identity, ordinal)"""
    schema = ibis.schema(
        [
            *combined.schema().items(),
            ("state_before", "string"),
            ("state_after", "string"),
            ("evaluation", "string"),
        ]
    )
    replay = freeze(
        ops.SQLStringView(
            combined.op(), f"SELECT * FROM ({query}) AS lifecycle_replay", schema
        ).to_expr()
    )
    checks: list[CompiledValidation] = []
    checks.append(_ambiguity_check(combined, replay, semantics))
    start = datetime.fromisoformat(semantics.source.cohort_start)
    end = datetime.fromisoformat(semantics.source.cohort_end)
    known = known_through(semantics, coverage)
    time_type = replay.occurred_at.type()
    through = (
        ibis.literal(known, type=time_type) if known is not None else ibis.null().cast(time_type)
    )
    inception = (
        replay.filter(replay.evaluation == "inception")
        .group_by("entity_identity")
        .aggregate(inception_at=replay.occurred_at.min())
    )
    ledger = membership.select(
        entity_identity=ibis.struct(
            {k: membership[k] for k, _ in semantics.source.subject_identity_signature}
        )
    )
    ledger = ledger.left_join(inception, "entity_identity").select(
        ledger.entity_identity, inception.inception_at
    )
    if known == end:
        observed = replay.select("entity_identity").distinct()
        missing = observed.anti_join(inception, "entity_identity")
        checks.append(check("insufficient_state_history", missing))
    ledger = ledger.mutate(
        inception_at=(ledger.inception_at < through).ifelse(
            ledger.inception_at, ibis.null().cast(time_type)
        ),
        known_through=through,
    )
    ledger = ledger.mutate(
        classification=ibis.literal("coverage_censored")
        if known != end
        else ledger.inception_at.isnull().ifelse("not_incepted", "seeded")
    )
    ledger = ledger.select(
        "entity_identity", "classification", "inception_at", "known_through"
    ).order_by("entity_identity")
    legal = replay.filter(replay.evaluation.isin(("inception", "legal_transition")))
    window = ibis.window(group_by=legal.entity_identity, order_by=legal.ordinal)
    entries = legal.mutate(
        next_time=legal.occurred_at.lead().over(window),
        next_ref=legal.trigger_event_ref.lead().over(window),
        next_identity=legal.event_identity.lead().over(window),
    )
    entries = entries.mutate(
        valid_from=ibis.greatest(entries.occurred_at, ibis.literal(start)),
        valid_to=ibis.least(entries.next_time.fill_null(end), ibis.literal(end)),
    )
    entries = entries.filter(entries.valid_from < entries.valid_to)
    proved = (
        (entries.occurred_at < through)
        & (entries.valid_to <= through)
        & (entries.next_time.isnull() | (entries.next_time < through))
    )
    status = proved.fill_null(False).ifelse(
        entries.next_time.notnull().ifelse("completed", "right_censored"), "coverage_censored"
    )
    history = entries.select(
        "entity_identity",
        model_state=entries.state_after,
        valid_from=entries.valid_from,
        valid_to=entries.valid_to,
        entered_by_event_ref=entries.trigger_event_ref,
        entered_by_event_identity=entries.event_identity,
        exited_by_event_ref=entries.next_ref,
        exited_by_event_identity=entries.next_identity,
        interval_status=status,
        left_clipped=entries.occurred_at < start,
    ).order_by("entity_identity", "valid_from")
    transitions = replay.filter(
        (replay.evaluation == "legal_transition") & (replay.occurred_at >= start)
    )
    ordinal = (
        ibis.row_number().over(
            ibis.window(group_by=transitions.entity_identity, order_by=transitions.ordinal)
        )
        + 1
    ).cast("int64")
    transitions = transitions.select(
        "entity_identity",
        transition_ordinal=ordinal,
        occurred_at=transitions.occurred_at,
        from_model_state=transitions.state_before,
        to_model_state=transitions.state_after,
        trigger_event_ref=transitions.trigger_event_ref,
        trigger_event_identity=transitions.event_identity,
    ).order_by("entity_identity", "transition_ordinal")
    violations = replay.filter(
        replay.evaluation.isin(("illegal_transition", "transition_from_terminal"))
        & (replay.occurred_at >= start)
    )
    violations = violations.select(
        "entity_identity",
        "trigger_event_ref",
        trigger_event_identity=violations.event_identity,
        occurred_at=violations.occurred_at,
        model_state_at_event=violations.state_before,
        violation_kind=violations.evaluation,
    ).order_by("trigger_event_ref", "trigger_event_identity")
    return history, tuple(zip(ROLES, (transitions, ledger, violations), strict=True)), tuple(checks)
