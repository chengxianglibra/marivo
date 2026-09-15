"""DuckDB-only SQL lowerings for native Runtime proofs and fences."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir
from sqlglot import expressions as sge

from marivo.analysis.datasets.descriptors import DatasetFieldId
from marivo.analysis.domains.lifecycle import ROLES, LifecycleSemantics
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.lifecycle_codec import invalid


def integrity_sql(
    backend: ExecutionAdapter,
    history: ir.Table,
    parts: Mapping[str, ir.Table],
    semantics: LifecycleSemantics,
) -> str:
    from marivo.analysis.compiler.lifecycle import literal

    if set(parts) != set(ROLES):
        raise invalid("missing required Lifecycle retained relation")
    start = f"TIMESTAMPTZ {literal(semantics.source.cohort_start)}"
    end = f"TIMESTAMPTZ {literal(semantics.source.cohort_end)}"
    states = ", ".join(literal(x) for x in semantics.states)
    events = ", ".join(literal(s.event.key) for s in semantics.source.pattern.steps)
    rules = (
        " OR ".join(
            f"(t.from_model_state={literal(a)} AND t.to_model_state={literal(c)} AND t.trigger_event_ref={literal(next(s.event.key for s in semantics.source.pattern.steps if s.key == b))})"
            for a, b, c in semantics.transitions
        )
        or "FALSE"
    )
    terms = [
        f"SELECT count(*) FROM h WHERE entity_identity IS NULL OR model_state IS NULL OR interval_status IS NULL OR entered_by_event_ref IS NULL OR model_state NOT IN ({states}) OR valid_from IS NULL OR valid_to IS NULL OR valid_from >= valid_to OR valid_from < {start} OR valid_to > {end} OR interval_status NOT IN ('completed','right_censored','coverage_censored') OR entered_by_event_ref NOT IN ({events}) OR entered_by_event_identity IS NULL OR left_clipped IS NULL OR (left_clipped AND valid_from <> {start}) OR ((exited_by_event_ref IS NULL) <> (exited_by_event_identity IS NULL)) OR (interval_status='completed' AND exited_by_event_ref IS NULL) OR (interval_status='right_censored' AND valid_to <> {end})",
        "SELECT count(*) FROM (SELECT *, lag(valid_to) OVER(PARTITION BY entity_identity ORDER BY valid_from) AS previous_end FROM h) WHERE previous_end > valid_from",
        "SELECT count(*) FROM h ANTI JOIN c USING(entity_identity)",
        "SELECT count(*) FROM t ANTI JOIN c USING(entity_identity)",
        "SELECT count(*) FROM v ANTI JOIN c USING(entity_identity)",
        f"SELECT count(*) FROM c WHERE entity_identity IS NULL OR classification IS NULL OR classification NOT IN ('seeded','not_incepted','coverage_censored') OR known_through > {end} OR (inception_at IS NOT NULL AND (known_through IS NULL OR inception_at >= known_through)) OR (classification='seeded' AND (inception_at IS NULL OR known_through IS DISTINCT FROM {end})) OR (classification='not_incepted' AND (inception_at IS NOT NULL OR known_through IS DISTINCT FROM {end})) OR (classification='coverage_censored' AND known_through IS NOT DISTINCT FROM {end})",
        f"SELECT count(*) FROM t WHERE entity_identity IS NULL OR transition_ordinal IS NULL OR transition_ordinal < 1 OR occurred_at IS NULL OR occurred_at < {start} OR occurred_at >= {end} OR from_model_state IS NULL OR to_model_state IS NULL OR trigger_event_ref IS NULL OR trigger_event_identity IS NULL OR NOT ({rules})",
        "SELECT count(*) FROM (SELECT transition_ordinal, row_number() OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS n FROM t) WHERE transition_ordinal <> n",
        "SELECT count(*) FROM (SELECT *, lag(occurred_at) OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS previous_time, lag(to_model_state) OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS previous_state FROM t) WHERE previous_time > occurred_at OR previous_state <> from_model_state",
        f"SELECT count(*) FROM v WHERE entity_identity IS NULL OR trigger_event_ref IS NULL OR trigger_event_ref NOT IN ({events}) OR trigger_event_identity IS NULL OR occurred_at IS NULL OR occurred_at < {start} OR occurred_at >= {end} OR model_state_at_event IS NULL OR model_state_at_event NOT IN ({states}) OR violation_kind IS NULL OR violation_kind NOT IN ('illegal_transition','transition_from_terminal')",
        "SELECT count(*) FROM h JOIN c USING(entity_identity) WHERE interval_status IN ('completed','right_censored') AND (known_through IS NULL OR valid_to > known_through)",
        "SELECT count(*) FROM h WHERE interval_status='completed' AND NOT EXISTS (SELECT 1 FROM t WHERE t.entity_identity=h.entity_identity AND t.occurred_at=h.valid_to AND t.from_model_state=h.model_state AND t.trigger_event_ref=h.exited_by_event_ref AND t.trigger_event_identity=h.exited_by_event_identity)",
        "SELECT count(*) FROM (SELECT entity_identity, valid_from FROM h GROUP BY ALL HAVING count(*) <> 1)",
        "SELECT count(*) FROM (SELECT entity_identity FROM c GROUP BY ALL HAVING count(*) <> 1)",
        "SELECT count(*) FROM (SELECT trigger_event_ref, trigger_event_identity FROM (SELECT trigger_event_ref, trigger_event_identity FROM t UNION ALL SELECT trigger_event_ref, trigger_event_identity FROM v) GROUP BY trigger_event_ref, trigger_event_identity HAVING count(*) > 1)",
    ]
    inception_events = ", ".join(
        literal(step.event.key)
        for step in semantics.source.pattern.steps
        if step.key in semantics.inceptions
    )
    terms.extend(
        (
            "SELECT count(*) FROM h JOIN c USING(entity_identity) WHERE c.classification='not_incepted'",
            f"SELECT count(*) FROM h WHERE NOT left_clipped AND NOT EXISTS (SELECT 1 FROM t WHERE t.entity_identity=h.entity_identity AND t.occurred_at=h.valid_from AND t.to_model_state=h.model_state AND t.trigger_event_ref=h.entered_by_event_ref AND t.trigger_event_identity=h.entered_by_event_identity) AND NOT (h.model_state={literal(semantics.initial)} AND h.entered_by_event_ref IN ({inception_events}))",
            "SELECT count(*) FROM h JOIN t ON h.entity_identity=t.entity_identity AND h.entered_by_event_ref=t.trigger_event_ref AND h.entered_by_event_identity=t.trigger_event_identity WHERE NOT h.left_clipped AND (h.model_state<>t.to_model_state OR h.valid_from<>t.occurred_at)",
            f"SELECT count(*) FROM c WHERE inception_at IS NOT NULL AND known_through > greatest({start},inception_at) AND coalesce((SELECT sum(greatest(0,epoch_us(least(h.valid_to,c.known_through))-epoch_us(greatest(h.valid_from,c.inception_at,{start})))) FROM h WHERE h.entity_identity=c.entity_identity),0) <> epoch_us(known_through)-epoch_us(greatest({start},inception_at))",
        )
    )
    for name in ("h", "t", "c", "v"):
        components = " OR ".join(
            f"entity_identity.{sge.to_identifier(key, quoted=True).sql(dialect='duckdb')} IS NULL"
            for key, _ in semantics.source.subject_identity_signature
        )
        terms.append(f"SELECT count(*) FROM {name} WHERE {components}")
    for table, column, nullable in (
        ("h", "entered_by_event_identity", False),
        ("h", "exited_by_event_identity", True),
        ("t", "trigger_event_identity", False),
        ("v", "trigger_event_identity", False),
    ):
        components = " OR ".join(
            f"{column}.k{i} IS NULL" for i in range(len(semantics.source.occurrence_identity_types))
        )
        terms.append(
            f"SELECT count(*) FROM {table} WHERE "
            + (f"{column} IS NOT NULL AND ({components})" if nullable else components)
        )

    ctes = ", ".join(
        f"{name} AS ({backend.compile(table)})"
        for name, table in zip(
            ("h", "t", "c", "v"), (history, *(parts[r] for r in ROLES)), strict=True
        )
    )
    return f"WITH {ctes} SELECT " + " + ".join(f"({term})" for term in terms) + " AS violations"


def attribution_summary_sql(
    source_sql: str,
    names: Mapping[DatasetFieldId, str],
    scope_ids: tuple[DatasetFieldId, ...],
    key_ids: tuple[DatasetFieldId, ...],
) -> str:
    def quote(name: str) -> str:
        return sge.to_identifier(name, quoted=True).sql(dialect="duckdb")

    scope = tuple(quote(names[key]) for key in scope_ids)
    keys = tuple(quote(names[key]) for key in key_ids)
    resolution = (*scope, quote("active_axis_mask"))
    identity = "struct_pack(" + ", ".join(f"{name} := {name}" for name in keys) + ")"
    grouping = ", ".join(resolution)
    scoped = (
        "struct_pack(" + ", ".join(f"{name} := {name}" for name in scope) + ")" if scope else "1"
    )
    sql = (
        f"WITH attributed AS ({source_sql}), reconciled AS ("
        f"SELECT sum(contribution) AS total, max(overall_delta) AS delta FROM attributed GROUP BY {grouping}) "
        f"SELECT count(DISTINCT {scoped}), (SELECT count(*) FROM reconciled), "
        "count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status = 'zero_total_delta'), "
        "CAST(coalesce((SELECT max(abs(total - delta)) FROM reconciled), 0) AS DOUBLE), "
        f"sha256(coalesce(string_agg(sha256(to_json({identity})), '' ORDER BY {', '.join(keys)}), '')) FROM attributed"
    )
    return sql


def membership_integrity_sql(
    member_sql: str,
    primary_sql: str,
    keys: tuple[str, ...],
    endpoint: str,
    signature: tuple[tuple[str, str], ...],
) -> str:
    from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN

    def quoted(name: str) -> str:
        return sge.to_identifier(name, quoted=True).sql(dialect="duckdb")

    member = quoted(DISTINCT_KEY_COLUMN)
    null_member = " OR ".join(
        [f"{member} IS NULL"]
        + [
            f"struct_extract({member}, {sge.Literal.string(name).sql(dialect='duckdb')}) IS NULL"
            for name, _ in signature
        ]
    )
    coordinates = ", ".join(quoted(name) for name in keys)
    equality = (
        " AND ".join(f"m.{quoted(name)} IS NOT DISTINCT FROM p.{quoted(name)}" for name in keys)
        or "TRUE"
    )
    grouped = f"{coordinates}, " if coordinates else ""
    grouping = f" GROUP BY {coordinates}" if coordinates else ""
    sql = (
        f"WITH membership AS ({member_sql}), primary_rows AS ({primary_sql}), "
        f"counts AS (SELECT {grouped}count(*) AS __mv_members FROM membership{grouping}) "
        "SELECT "
        f"(SELECT count(*) FROM membership WHERE {null_member}) + "
        f"(SELECT count(*) FROM (SELECT {grouped}{member} FROM membership "
        f"GROUP BY {grouped}{member} HAVING count(*) <> 1)) + "
        f"(SELECT count(*) FROM membership m WHERE NOT EXISTS (SELECT 1 FROM primary_rows p WHERE {equality})) + "
        f"(SELECT count(*) FROM primary_rows p LEFT JOIN counts m ON {equality} "
        f"WHERE p.{quoted(endpoint)} IS NULL OR p.{quoted(endpoint)} <> coalesce(m.__mv_members, 0))"
    )
    return sql


def reservoir_statement(
    source_sql: str, relation_name: str, target_rows: int, seed: int | None
) -> str:
    name = sge.to_identifier(relation_name, quoted=True).sql(dialect="duckdb")
    sql = (
        f"CREATE TEMPORARY TABLE {name} AS SELECT * FROM ({source_sql}) "
        f"AS __mv_eligible USING SAMPLE reservoir({target_rows} ROWS)"
    )
    if seed is not None:
        sql += f" REPEATABLE({seed})"
    return sql


def reservoir_validation(relation_name: str, identity_columns: tuple[str, ...]) -> str:
    names = tuple(
        sge.to_identifier(name, quoted=True).sql(dialect="duckdb") for name in identity_columns
    )
    relation = sge.to_identifier(relation_name, quoted=True).sql(dialect="duckdb")
    identity = "struct_pack(" + ", ".join(f"{name} := {name}" for name in names) + ")"
    nulls = " OR ".join(f"{name} IS NULL" for name in names)
    order = ", ".join(names)
    validation_sql = (
        f"SELECT count(*) AS realized_entity_count, "
        f"count(*) - count(DISTINCT {identity}) AS duplicate_keys, "
        f"count(*) FILTER (WHERE {nulls}) AS null_keys, "
        f"sha256(coalesce(string_agg(sha256(to_json({identity})), '' ORDER BY {order}), '')) "
        f"AS membership_digest FROM {relation}"
    )
    return validation_sql
