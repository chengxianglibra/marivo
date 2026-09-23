"""Exact native integrity checks for complete Lifecycle history and retained parts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal

import ibis.expr.types as ir
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.domains.lifecycle import ROLES, LifecycleSemantics
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.lifecycle_codec import invalid


def _integrity_terms(
    backend: ExecutionAdapter,
    history: ir.Table,
    parts: Mapping[str, ir.Table],
    semantics: LifecycleSemantics,
    *,
    dialect: Literal["duckdb", "postgres", "trino", "clickhouse"] = "duckdb",
) -> tuple[str, list[str]]:
    from marivo.analysis.compiler.lifecycle import literal

    def micros(expression: str) -> str:
        if dialect == "postgres":
            return f"CAST(EXTRACT(EPOCH FROM {expression}) * 1000000 AS BIGINT)"
        if dialect == "clickhouse":
            return f"toUnixTimestamp64Micro({expression})"
        if dialect == "trino":
            return f"(date_diff('second', TIMESTAMP '1970-01-01 00:00:00 UTC', date_trunc('second',{expression}))*1000000 + CAST(rpad(coalesce(regexp_extract(CAST({expression} AS VARCHAR),'\\.([0-9]+)',1),''),6,'0') AS BIGINT))"
        return f"epoch_us({expression})"

    def component(column: str, key: str, index: int) -> str:
        if dialect == "postgres":
            return f"(to_jsonb({column})->>'f{index + 1}')"
        if dialect == "clickhouse":
            return f"tupleElement({column}, {literal(key)})"
        return f"{column}.{sge.to_identifier(key, quoted=True).sql(dialect='duckdb')}"

    if set(parts) != set(ROLES):
        raise invalid("missing required Lifecycle retained relation")
    start = f"TIMESTAMPTZ {literal(semantics.source.cohort_start)}"
    end = f"TIMESTAMPTZ {literal(semantics.source.cohort_end)}"
    if dialect == "trino":
        start = f"CAST({literal(semantics.source.cohort_start.replace('T', ' '))} AS TIMESTAMP(6) WITH TIME ZONE)"
        end = f"CAST({literal(semantics.source.cohort_end.replace('T', ' '))} AS TIMESTAMP(6) WITH TIME ZONE)"
    if dialect == "clickhouse":
        start_value = (
            datetime.fromisoformat(semantics.source.cohort_start)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
            .isoformat(sep=" ")
        )
        end_value = (
            datetime.fromisoformat(semantics.source.cohort_end)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
            .isoformat(sep=" ")
        )
        start = f"toDateTime64({literal(start_value)},6,'UTC')"
        end = f"toDateTime64({literal(end_value)},6,'UTC')"
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
        "SELECT count(*) FROM h WHERE NOT EXISTS (SELECT 1 FROM c WHERE c.entity_identity=h.entity_identity)",
        "SELECT count(*) FROM t WHERE NOT EXISTS (SELECT 1 FROM c WHERE c.entity_identity=t.entity_identity)",
        "SELECT count(*) FROM v WHERE NOT EXISTS (SELECT 1 FROM c WHERE c.entity_identity=v.entity_identity)",
        f"SELECT count(*) FROM c WHERE entity_identity IS NULL OR classification IS NULL OR classification NOT IN ('seeded','not_incepted','coverage_censored') OR known_through > {end} OR (inception_at IS NOT NULL AND (known_through IS NULL OR inception_at >= known_through)) OR (classification='seeded' AND (inception_at IS NULL OR known_through IS DISTINCT FROM {end})) OR (classification='not_incepted' AND (inception_at IS NOT NULL OR known_through IS DISTINCT FROM {end})) OR (classification='coverage_censored' AND known_through IS NOT DISTINCT FROM {end})",
        f"SELECT count(*) FROM t WHERE entity_identity IS NULL OR transition_ordinal IS NULL OR transition_ordinal < 1 OR occurred_at IS NULL OR occurred_at < {start} OR occurred_at >= {end} OR from_model_state IS NULL OR to_model_state IS NULL OR trigger_event_ref IS NULL OR trigger_event_identity IS NULL OR NOT ({rules})",
        "SELECT count(*) FROM (SELECT transition_ordinal, row_number() OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS n FROM t) WHERE transition_ordinal <> n",
        "SELECT count(*) FROM (SELECT *, lag(occurred_at) OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS previous_time, lag(to_model_state) OVER(PARTITION BY entity_identity ORDER BY transition_ordinal) AS previous_state FROM t) WHERE previous_time > occurred_at OR previous_state <> from_model_state",
        f"SELECT count(*) FROM v WHERE entity_identity IS NULL OR trigger_event_ref IS NULL OR trigger_event_ref NOT IN ({events}) OR trigger_event_identity IS NULL OR occurred_at IS NULL OR occurred_at < {start} OR occurred_at >= {end} OR model_state_at_event IS NULL OR model_state_at_event NOT IN ({states}) OR violation_kind IS NULL OR violation_kind NOT IN ('illegal_transition','transition_from_terminal')",
        "SELECT count(*) FROM h JOIN c USING(entity_identity) WHERE interval_status IN ('completed','right_censored') AND (known_through IS NULL OR valid_to > known_through)",
        "SELECT count(*) FROM h WHERE interval_status='completed' AND NOT EXISTS (SELECT 1 FROM t WHERE t.entity_identity=h.entity_identity AND t.occurred_at=h.valid_to AND t.from_model_state=h.model_state AND t.trigger_event_ref=h.exited_by_event_ref AND t.trigger_event_identity=h.exited_by_event_identity)",
        "SELECT count(*) FROM (SELECT entity_identity, valid_from FROM h GROUP BY entity_identity, valid_from HAVING count(*) <> 1) AS duplicate_intervals",
        "SELECT count(*) FROM (SELECT entity_identity FROM c GROUP BY entity_identity HAVING count(*) <> 1) AS duplicate_subjects",
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
            f"SELECT count(*) FROM c WHERE inception_at IS NOT NULL AND known_through > greatest({start},inception_at) AND coalesce((SELECT sum(greatest(0,{micros('least(h.valid_to,c.known_through)')}-{micros(f'greatest(h.valid_from,c.inception_at,{start})')})) FROM h WHERE h.entity_identity=c.entity_identity),0) <> {micros('known_through')}-{micros(f'greatest({start},inception_at)')}",
        )
    )
    if dialect in ("trino", "clickhouse"):
        for index, name in ((2, "h"), (3, "t"), (4, "v")):
            terms[index] = (
                f"SELECT count(*) FROM {name} LEFT JOIN c ON c.entity_identity={name}.entity_identity WHERE c.entity_identity IS NULL"
            )
        terms[11] = (
            "SELECT count(*) FROM h LEFT JOIN t ON t.entity_identity=h.entity_identity AND t.occurred_at=h.valid_to AND t.from_model_state=h.model_state AND t.trigger_event_ref=h.exited_by_event_ref AND t.trigger_event_identity=h.exited_by_event_identity WHERE h.interval_status='completed' AND t.entity_identity IS NULL"
        )
        terms[16] = (
            f"SELECT count(*) FROM h LEFT JOIN t ON t.entity_identity=h.entity_identity AND t.occurred_at=h.valid_from AND t.to_model_state=h.model_state AND t.trigger_event_ref=h.entered_by_event_ref AND t.trigger_event_identity=h.entered_by_event_identity WHERE NOT h.left_clipped AND t.entity_identity IS NULL AND NOT (h.model_state={literal(semantics.initial)} AND h.entered_by_event_ref IN ({inception_events}))"
        )
        terms[18] = (
            f"SELECT count(*) FROM c LEFT JOIN (SELECT c.entity_identity AS entity_identity, sum(greatest(0,{micros('least(h.valid_to,c.known_through)')}-{micros(f'greatest(h.valid_from,c.inception_at,{start})')})) covered FROM h JOIN c ON h.entity_identity=c.entity_identity GROUP BY c.entity_identity) coverage ON coverage.entity_identity=c.entity_identity WHERE c.inception_at IS NOT NULL AND c.known_through > greatest({start},c.inception_at) AND coalesce(covered,0) <> {micros('c.known_through')}-{micros(f'greatest({start},c.inception_at)')}"
        )
    for name in ("h", "t", "c", "v"):
        components = " OR ".join(
            f"{component('entity_identity', key, index)} IS NULL"
            for index, (key, _) in enumerate(semantics.source.subject_identity_signature)
        )
        terms.append(f"SELECT count(*) FROM {name} WHERE {components}")
    for table, column, nullable in (
        ("h", "entered_by_event_identity", False),
        ("h", "exited_by_event_identity", True),
        ("t", "trigger_event_identity", False),
        ("v", "trigger_event_identity", False),
    ):
        components = " OR ".join(
            f"{component(column, f'k{i}', i)} IS NULL"
            for i in range(len(semantics.source.occurrence_identity_types))
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
    if dialect == "clickhouse":
        identity_names = {
            "entity_identity": tuple(key for key, _ in semantics.source.subject_identity_signature),
            **{
                name: tuple(f"k{i}" for i in range(len(semantics.source.occurrence_identity_types)))
                for name in (
                    "trigger_event_identity",
                    "entered_by_event_identity",
                    "exited_by_event_identity",
                )
            },
        }
        rewritten = []
        for term in terms:
            tree = sqlglot.parse_one(term, read="clickhouse")
            for node in tuple(tree.find_all(sge.Is)):
                column = node.this
                if (
                    isinstance(column, sge.Column)
                    and column.name in identity_names
                    and isinstance(node.expression, sge.Null)
                ):
                    node.replace(
                        sge.Paren(
                            this=sge.and_(
                                *(
                                    sge.Is(
                                        this=sge.Anonymous(
                                            this="tupleElement",
                                            expressions=[column.copy(), sge.Literal.string(key)],
                                        ),
                                        expression=sge.Null(),
                                    )
                                    for key in identity_names[column.name]
                                )
                            )
                        )
                    )
            rewritten.append(tree.sql(dialect="clickhouse"))
        terms = rewritten
    return ctes, terms


def integrity_sql(
    backend: ExecutionAdapter,
    history: ir.Table,
    parts: Mapping[str, ir.Table],
    semantics: LifecycleSemantics,
    *,
    dialect: Literal["duckdb", "postgres", "trino", "clickhouse"] = "duckdb",
) -> str:
    ctes, terms = _integrity_terms(backend, history, parts, semantics, dialect=dialect)
    return f"WITH {ctes} SELECT " + " + ".join(f"({term})" for term in terms) + " AS violations"


def integrity_queries(
    backend: ExecutionAdapter,
    history: ir.Table,
    parts: Mapping[str, ir.Table],
    semantics: LifecycleSemantics,
) -> tuple[str, ...]:
    """Keep independent scalar assertions below Trino's query stage budget."""
    ctes, terms = _integrity_terms(backend, history, parts, semantics, dialect="trino")
    return tuple(f"WITH {ctes} {term}" for term in terms)
