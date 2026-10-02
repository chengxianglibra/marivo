"""Internal retained schema declarations consumed by R7.6 reducer lowering."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.domains.lifecycle import PART_COLUMNS, PART_KEYS, ROLES, LifecycleSemantics
from marivo.analysis.materialization.lifecycle_codec import invalid

if TYPE_CHECKING:
    from marivo.analysis.datasets.descriptors import DatasetRowContract
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
) -> None:
    part_schema(row, role, table.schema().to_pyarrow())
    keys = PART_KEYS[ROLES.index(role)]
    query = table.group_by(*keys).aggregate(n=table.count())
    bad = query.filter(query.n != 1).count()
    if backend.read_scalar(backend.prepare(bad, role="lifecycle.part_key")) != 0:
        raise invalid("duplicate Lifecycle retained role keys")
