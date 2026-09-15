"""Native Lifecycle integrity proofs and bounded, identity-free publication."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import CompiledDataset, RetainedRelationSpec
from marivo.analysis.domains.lifecycle import PART_COLUMNS, PART_KEYS, ROLES, LifecycleSemantics
from marivo.analysis.materialization.duckdb_statements import integrity_sql
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary, invalid
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import ObjectBinding

if TYPE_CHECKING:
    from marivo.analysis.datasets.descriptors import DatasetRowContract
    from marivo.analysis.materialization.contracts import ArtifactDescriptor
    from marivo.analysis.materialization.execution import ExecutionAdapter


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
    backend: ExecutionAdapter,
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
    if backend.read_scalar(backend.prepare(bad, role="lifecycle.part_key")) != 0:
        raise invalid("duplicate Lifecycle retained role keys")


def native_summary(
    backend: ExecutionAdapter,
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
    if backend.read_scalar(backend.prepare(wrong_coverage, role="lifecycle.coverage_ledger")) != 0:
        raise invalid("Lifecycle coverage ledger differs from its retained source-origin prefix")
    proof = integrity_sql(backend, recipe.expression, parts, semantics)
    record("lifecycle.history_integrity", proof)
    if (
        backend.read_scalar(
            backend.statement(
                proof,
                role="lifecycle.history_integrity",
                inputs=tuple(backend.prepare(t) for t in (recipe.expression, *parts.values())),
            )
        )
        != 0
    ):
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
    result = backend.submit(
        backend.statement(
            query,
            role="lifecycle.summary",
            inputs=tuple(backend.prepare(expr) for expr in statements),
        )
    ).fetchone()
    if (
        result is None
        or len(result) != 8
        or any(type(value) is not int or value < 0 for value in result)
    ):
        raise invalid("invalid native Lifecycle scalar summary")
    return LifecycleEvidenceSummary(
        recipe.lifecycle_coverage, *(value for value in result if isinstance(value, int))
    )


def inspect_history(
    root: Path,
    descriptor: ArtifactDescriptor,
    bindings: tuple[ObjectBinding, ...],
    policy: ReadPolicy,
) -> None:
    """Inspect all immutable replay rows in the calling process."""
    from itertools import chain

    import ibis

    from marivo.analysis.materialization.reads import payload_batches

    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, LifecycleSemantics):
        raise invalid("missing retained Lifecycle meaning")
    from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter

    backend = DuckDBExecutionAdapter(ibis.duckdb.connect())
    try:
        backend.configure()
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
                relations.append(backend.freeze_reader(f"lifecycle_part_{index}", reader))
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
