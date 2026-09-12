"""Native Lifecycle integrity proofs and bounded, identity-free publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir
import pyarrow as pa
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import CompiledDataset, RetainedRelationSpec
from marivo.analysis.domains.lifecycle import PART_COLUMNS, PART_KEYS, ROLES, LifecycleSemantics
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary, invalid
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import ObjectBinding

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend

    from marivo.analysis.datasets.descriptors import DatasetRowContract
    from marivo.analysis.materialization.contracts import ArtifactDescriptor


def part_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    semantics = row.family_semantics
    if not isinstance(semantics, LifecycleSemantics) or role not in ROLES:
        raise invalid("unknown Lifecycle retained role")
    index = ROLES.index(role)
    if tuple(schema.names) != PART_COLUMNS[index]:
        raise invalid("Lifecycle retained role schema differs")
    from marivo.analysis.materialization.event_publication import _identity_type

    for field in schema:
        name, physical = field.name, field.type
        if name in ("entity_identity", "trigger_event_identity"):
            signature = (
                semantics.source.subject_identity_signature
                if name == "entity_identity"
                else tuple(
                    (f"k{i}", kind)
                    for i, kind in enumerate(semantics.source.occurrence_identity_types)
                )
            )
            if (
                not pa.types.is_struct(physical)
                or len(physical) != len(signature)
                or any(
                    f.name != key or not _identity_type(kind, f.type)
                    for f, (key, kind) in zip(physical, signature, strict=True)
                )
            ):
                raise invalid("Lifecycle retained identity type differs")
        elif name in ("occurred_at", "inception_at", "known_through"):
            if not pa.types.is_timestamp(physical) or physical.tz not in (
                "UTC",
                "Etc/UTC",
                "+00:00",
            ):
                raise invalid("Lifecycle retained instant is not UTC")
        elif name == "transition_ordinal":
            if not pa.types.is_int64(physical):
                raise invalid("Lifecycle transition ordinal is not int64")
        elif not pa.types.is_string(physical) and not pa.types.is_large_string(physical):
            raise invalid("Lifecycle retained state or ref is not text")
    return PART_KEYS[index]


def validate_relation(
    backend: Backend,
    table: ir.Table,
    row: DatasetRowContract,
    role: str,
    record: Callable[[str, str], None],
) -> None:
    part_schema(row, role, table.schema().to_pyarrow())
    keys = PART_KEYS[ROLES.index(role)]
    query = table.group_by(*keys).aggregate(n=table.count())
    bad = query.filter(query.n != 1).count()
    record("lifecycle.part_key", backend.compile(bad))
    if backend.execute(bad) != 0:
        raise invalid("duplicate Lifecycle retained role keys")


def integrity_sql(
    backend: Backend,
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


def native_summary(
    backend: Backend,
    recipe: CompiledDataset,
    row: DatasetRowContract,
    record: Callable[[str, str], None],
) -> LifecycleEvidenceSummary:
    parts = {
        p.role: p.expression for p in recipe.retained_parts if isinstance(p, RetainedRelationSpec)
    }
    if recipe.lifecycle_coverage is None:
        raise invalid("missing Lifecycle coverage proof")
    semantics = row.family_semantics
    if not isinstance(semantics, LifecycleSemantics):
        raise invalid("missing Lifecycle row semantics")
    from marivo.analysis.compiler.lifecycle import known_through

    through = known_through(semantics, recipe.lifecycle_coverage)
    ledger = parts[ROLES[1]]
    boundary = (
        ibis.literal(through, type=ledger.known_through.type())
        if through is not None
        else ibis.null().cast(ledger.known_through.type())
    )
    wrong_coverage = ledger.filter(~ledger.known_through.identical_to(boundary)).count()
    record("lifecycle.coverage_ledger", backend.compile(wrong_coverage))
    if backend.execute(wrong_coverage) != 0:
        raise invalid("Lifecycle coverage ledger differs from its retained source-origin prefix")
    proof = integrity_sql(backend, recipe.expression, parts, semantics)
    record("lifecycle.history_integrity", proof)
    if backend.raw_sql(proof).fetchone()[0] != 0:
        raise invalid("canonical Lifecycle replay integrity failed")
    h, t, c, v = recipe.expression, *(parts[r] for r in ROLES)
    # Return one scalar row; source identity rows remain inside DuckDB.
    statements = (
        h.count(),
        c.count(),
        c.filter(c.classification == "seeded").count(),
        c.filter(c.classification == "not_incepted").count(),
        c.filter(c.classification == "coverage_censored").count(),
        t.count(),
        v.count(),
        h.filter(h.left_clipped).count(),
    )
    query = "SELECT " + ", ".join(
        f"({backend.compile(expr)}) AS n{i}" for i, expr in enumerate(statements)
    )
    record("lifecycle.summary", query)
    result = backend.raw_sql(query).fetchone()
    if (
        result is None
        or len(result) != 8
        or any(type(value) is not int or value < 0 for value in result)
    ):
        raise invalid("invalid native Lifecycle scalar summary")
    return LifecycleEvidenceSummary(recipe.lifecycle_coverage, *result)


def inspect_history(
    root: Path,
    descriptor: ArtifactDescriptor,
    bindings: tuple[ObjectBinding, ...],
    policy: ReadPolicy,
) -> None:
    """Inspect all immutable replay rows inside the bounded inspection worker."""
    from itertools import chain

    import ibis

    from marivo.analysis.materialization.reads import payload_batches

    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, LifecycleSemantics):
        raise invalid("missing retained Lifecycle meaning")
    backend = ibis.duckdb.connect()
    try:
        backend.raw_sql("SET threads=1")
        backend.raw_sql("SET memory_limit='256MiB'")
        backend.raw_sql("SET max_temp_directory_size='0B'")
        relations: list[ir.Table] = []
        receipts = (
            descriptor.storage_receipt,
            *(
                next(p.storage_receipt for p in descriptor.retained_parts if p.role == role)
                for role in ROLES
            ),
        )
        for index, receipt in enumerate(receipts):
            stream = payload_batches(
                root,
                receipt,
                policy=policy,
                bindings=bindings,
                row=descriptor.row_contract if index == 0 else None,
                rows=descriptor.row_set_contract if index == 0 else None,
                audit=True,
            )
            try:
                first = next(stream, None)
                if first is None:
                    raise invalid("missing Lifecycle schema header")
                reader = pa.RecordBatchReader.from_batches(first.schema, chain((first,), stream))
                backend.con.register("lifecycle_incoming", reader)
                backend.raw_sql(
                    f"CREATE TEMPORARY TABLE lifecycle_part_{index} AS SELECT * FROM lifecycle_incoming"
                )
                backend.con.unregister("lifecycle_incoming")
                relations.append(backend.table(f"lifecycle_part_{index}"))
            finally:
                stream.close()
        if descriptor.lifecycle_evidence is None:
            raise invalid("missing retained Lifecycle Evidence")
        recipe = CompiledDataset(
            relations[0],
            (),
            tuple(relations[0].columns),
            tuple(
                RetainedRelationSpec(role, role, 1, table)
                for role, table in zip(ROLES, relations[1:], strict=True)
            ),
            lifecycle_coverage=descriptor.lifecycle_evidence.coverage,
        )
        if (
            native_summary(backend, recipe, descriptor.row_contract, lambda *_: None)
            != descriptor.lifecycle_evidence
        ):
            raise invalid("retained Lifecycle parts contradict their bounded Evidence")
    finally:
        backend.disconnect()
