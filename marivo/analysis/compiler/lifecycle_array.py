"""Complete source-side finite-state folds without recursive query depth limits."""

from __future__ import annotations

from marivo.analysis.compiler.lifecycle import literal
from marivo.analysis.domains.lifecycle import LifecycleSemantics


def _step(semantics: LifecycleSemantics, state: str, trigger: str) -> tuple[str, str]:
    states = {value: index + 1 for index, value in enumerate(semantics.states)}
    inception = f"{trigger} IN ({', '.join(literal(x) for x in semantics.inceptions)})"
    terminal = (
        f"{state} IN ({', '.join(str(states[x]) for x in semantics.terminals)})"
        if semantics.terminals
        else "FALSE"
    )
    target = (
        "CASE "
        + " ".join(
            f"WHEN {state}={states[a]} AND {trigger}={literal(b)} THEN {states[c]}"
            for a, b, c in semantics.transitions
        )
        + " ELSE 0 END"
        if semantics.transitions
        else "0"
    )
    after = f"CASE WHEN {state}=0 THEN CASE WHEN {inception} THEN {states[semantics.initial]} ELSE 0 END WHEN {terminal} OR {inception} THEN {state} WHEN ({target})<>0 THEN ({target}) ELSE {state} END"
    kind = f"CASE WHEN {state}=0 THEN CASE WHEN {inception} THEN 1 ELSE 2 END WHEN {terminal} THEN 3 WHEN {inception} THEN 4 WHEN ({target})<>0 THEN 5 ELSE 4 END"
    return after, kind


def trino_replay(source: str, semantics: LifecycleSemantics) -> str:
    """Fold every ordered occurrence and retain its before/after state in Trino."""
    after, kind = _step(semantics, "a.state", "e.trigger")
    entry = "ROW(ordinal BIGINT, before_state INTEGER, after_state INTEGER, kind INTEGER)"
    accumulator = f"ROW(state INTEGER, entries ARRAY({entry}))"
    states = "ARRAY[" + ", ".join(literal(x) for x in semantics.states) + "]"
    kinds = "ARRAY['inception','pre_inception','transition_from_terminal','illegal_transition','legal_transition']"
    return f"""WITH sequences AS (
        SELECT entity_identity,
            array_agg(CAST(ROW(ordinal,trigger_key) AS ROW(ordinal BIGINT, trigger VARCHAR)) ORDER BY ordinal) events
        FROM {source} GROUP BY entity_identity
    ), folded AS (
        SELECT entity_identity, reduce(events,
            CAST(ROW(0,CAST(ARRAY[] AS ARRAY({entry}))) AS {accumulator}),
            (a,e) -> CAST(ROW({after}, concat(a.entries, ARRAY[CAST(ROW(e.ordinal,a.state,{after},{kind}) AS {entry})])) AS {accumulator}),
            a -> a.entries) entries FROM sequences
    ), outcomes AS (
        SELECT entity_identity, ordinal,
            element_at({states},nullif(before_state,0)) state_before,
            element_at({states},nullif(after_state,0)) state_after,
            element_at({kinds},kind) evaluation
        FROM folded CROSS JOIN UNNEST(entries) AS u(ordinal,before_state,after_state,kind)
    ) SELECT o.*, r.state_before,r.state_after,r.evaluation
        FROM {source} o JOIN outcomes r ON o.entity_identity=r.entity_identity AND o.ordinal=r.ordinal"""


def trino_confluence(source: str, replay: str, semantics: LifecycleSemantics) -> str:
    """Enumerate every compatible tied-group interleaving wholly in the source."""
    after, kind = _step(semantics, "p.state", "e.trigger")
    outcome = "ROW(ordinal BIGINT,kind INTEGER,violation_state INTEGER)"
    path = f"ROW(state INTEGER,remaining ARRAY(BIGINT),outcomes ARRAY({outcome}))"
    identity_type = (
        "ROW("
        + ",".join(f"k{i} BIGINT" for i in range(len(semantics.source.occurrence_identity_types)))
        + ")"
    )
    states = "ARRAY[" + ", ".join(literal(x) for x in semantics.states) + "]"
    candidates = "filter(events,e -> contains(p.remaining,e.ordinal) AND NOT any_match(events,q -> q.event_ref=e.event_ref AND q.event_identity<e.event_identity AND contains(p.remaining,q.ordinal)))"
    advance = f"CAST(ROW({after},filter(p.remaining,x -> x<>e.ordinal),array_sort(concat(p.outcomes,ARRAY[CAST(ROW(e.ordinal,{kind},CASE WHEN ({kind}) IN (3,4) THEN p.state ELSE 0 END) AS {outcome})]))) AS {path})"
    return f"""WITH tied AS (
        SELECT entity_identity, occurred_at, min(ordinal) lo,
            array_agg(CAST(ROW(ordinal,trigger_key,trigger_event_ref,event_identity) AS ROW(ordinal BIGINT,trigger VARCHAR,event_ref VARCHAR,event_identity {identity_type})) ORDER BY ordinal) events
        FROM {source} GROUP BY entity_identity,occurred_at HAVING count(*)>1
    ), seeds AS (
        SELECT g.*,coalesce(array_position({states},r.state_before),0) initial
        FROM tied g JOIN {replay} r ON g.entity_identity=r.entity_identity AND g.lo=r.ordinal
    ), explored AS (
        SELECT reduce(events,
            ARRAY[CAST(ROW(CAST(initial AS INTEGER),transform(events,e -> e.ordinal),CAST(ARRAY[] AS ARRAY({outcome}))) AS {path})],
            (paths,iteration) -> array_distinct(flatten(transform(paths,p -> transform({candidates},e -> {advance})))),
            paths -> cardinality(array_distinct(transform(paths,p -> ROW(p.state,p.outcomes))))) variants
        FROM seeds
    ) SELECT count(*) AS violations FROM explored WHERE variants>1"""


def clickhouse_replay(source: str, semantics: LifecycleSemantics) -> str:
    """Fold the complete ordered subject sequence using native ClickHouse arrays."""
    after, kind = _step(semantics, "a.1", "e.2")
    states = "[" + ", ".join(literal(x) for x in semantics.states) + "]"
    kinds = "['inception','pre_inception','transition_from_terminal','illegal_transition','legal_transition']"
    return f"""WITH sequences AS (
        SELECT entity_identity, arraySort(e -> e.1,groupArray(tuple(ordinal,trigger_key))) events
        FROM {source} GROUP BY entity_identity
    ), folded AS (
        SELECT entity_identity, arrayFold((a,e) ->
            tuple(toInt32({after}),arrayPushBack(a.2,tuple(e.1,a.1,toInt32({after}),toInt32({kind})))),
            events,tuple(toInt32(0),CAST([],'Array(Tuple(Int64,Int32,Int32,Int32))'))).2 entries
        FROM sequences
    ), outcomes AS (
        SELECT entity_identity,e.1 ordinal,
            arrayElementOrNull({states},e.2) state_before,
            arrayElementOrNull({states},e.3) state_after,
            arrayElement({kinds},e.4) evaluation
        FROM folded ARRAY JOIN entries AS e
    ) SELECT o.*,r.state_before,r.state_after,r.evaluation
        FROM {source} o JOIN outcomes r ON o.entity_identity=r.entity_identity AND o.ordinal=r.ordinal"""


def clickhouse_confluence(source: str, replay: str, semantics: LifecycleSemantics) -> str:
    """Explore complete compatible tied-group paths in native ClickHouse arrays."""
    after, kind = _step(semantics, "p.1", "e.2")
    states = "[" + ", ".join(literal(x) for x in semantics.states) + "]"
    candidates = "arrayFilter(e -> has(p.2,e.1) AND NOT arrayExists(q -> q.3=e.3 AND q.4<e.4 AND has(p.2,q.1),events),events)"
    advance = f"tuple(toInt32({after}),arrayFilter(x -> x<>e.1,p.2),arraySort(arrayPushBack(p.3,tuple(e.1,toInt32({kind}),if(({kind}) IN (3,4),p.1,toInt32(0))))))"
    return f"""WITH tied AS (
        SELECT entity_identity,occurred_at,min(ordinal) lo,
            arraySort(e -> e.1,groupArray(tuple(ordinal,trigger_key,trigger_event_ref,event_identity))) events
        FROM {source} GROUP BY entity_identity,occurred_at HAVING count(*)>1
    ), seeds AS (
        SELECT g.*,toInt32(if(isNull(r.state_before),0,indexOf({states},r.state_before))) initial
        FROM tied g JOIN {replay} r ON g.entity_identity=r.entity_identity AND g.lo=r.ordinal
    ), explored AS (
        SELECT arrayFold((paths,iteration) -> arrayDistinct(arrayFlatten(arrayMap(p ->
            arrayMap(e -> {advance},{candidates}),paths))),
            events,
            [tuple(initial,arrayMap(e -> e.1,events),CAST([],'Array(Tuple(Int64,Int32,Int32))'))]) paths
        FROM seeds
    ) SELECT toInt64(count(*)) violations FROM explored
        WHERE length(arrayDistinct(arrayMap(p -> tuple(p.1,p.3),paths)))>1"""
