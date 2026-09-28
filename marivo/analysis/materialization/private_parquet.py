"""Owner-declared independent private state in the common Parquet transaction."""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import Decimal

import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.materialization.storage import _integrity
from marivo.analysis.observation.distinct_contracts import (
    DISTINCT_KEY_COLUMN,
    membership_part_authorities,
)
from marivo.analysis.observation.distribution_contracts import (
    FREQUENCY,
    VALUE,
    distribution_part_authorities,
)


def independent_contracts(row: DatasetRowContract) -> dict[str, str]:
    """Derive exact role and protocol ownership from the admitted family semantics."""
    from marivo.analysis.domains.lifecycle import ROLES, LifecycleSemantics

    if isinstance(row.family_semantics, LifecycleSemantics):
        return dict(zip(ROLES, ROLES, strict=True))
    return {
        **{
            role: f"{row.shape_id.family_id}.distinct_membership"
            for role, _ in membership_part_authorities(row)
        },
        **{
            role: f"{row.shape_id.family_id}.distribution"
            for role, _ in distribution_part_authorities(row)
        },
    }


def checked_private_batches(
    batches: Iterable[pa.RecordBatch], row: DatasetRowContract, role: str
) -> Iterable[pa.RecordBatch]:
    """Bounded physical checks supplement native uniqueness and endpoint proofs."""
    from marivo.analysis.materialization.retained import component_schema

    distribution = role in dict(distribution_part_authorities(row))
    if role not in independent_contracts(row):
        _integrity("one registered independent role", "unknown private role")
    for batch in batches:
        component_schema(row, role, batch.schema)
        if distribution:
            for value, frequency in zip(batch[VALUE], batch[FREQUENCY], strict=True):
                numeric = value.as_py()
                count = frequency.as_py()
                if (
                    not isinstance(numeric, (int, float, Decimal))
                    or isinstance(numeric, bool)
                    or not math.isfinite(numeric)
                    or type(count) is not int
                    or count <= 0
                ):
                    _integrity(
                        "finite values with positive exact frequencies",
                        "invalid distribution support",
                    )
        else:
            members = batch[DISTINCT_KEY_COLUMN]
            if members.null_count or (
                pa.types.is_struct(members.type)
                and any(members.field(index).null_count for index in range(members.type.num_fields))
            ):
                _integrity("complete non-null private member identities", "null membership key")
        yield batch
