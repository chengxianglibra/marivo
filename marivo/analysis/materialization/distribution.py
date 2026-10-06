"""Independently owned retained distribution schema validation."""

from __future__ import annotations

import ibis.expr.datatypes as dt
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, _EntityFieldIdentity
from marivo.analysis.materialization.storage import _integrity, _matches_type
from marivo.analysis.observation.distribution_contracts import (
    FREQUENCY,
    VALUE,
    distribution_part_authorities,
)


def distribution_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    """Validate the exact independently owned value-frequency Parquet schema."""
    authority = next(
        (item for name, item in distribution_part_authorities(row) if name == role), None
    )
    if authority is None or authority.distribution is None:
        _integrity("one exact distribution authority", "unknown distribution role")
    keys = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
    if tuple(schema.names) != (*(field.name for field in keys), VALUE, FREQUENCY):
        _integrity(
            "complete distribution coordinates, value and frequency", "distribution schema differs"
        )
    if (
        not _matches_type(authority.distribution.value_logical_type, schema.field(VALUE).type)
        or schema.field(FREQUENCY).type != dt.int64.to_pyarrow()
    ):
        _integrity("exact distribution value and frequency types", "distribution type differs")
    for field in keys:
        actual = schema.field(field.name).type
        if isinstance(field.identity, _EntityFieldIdentity) and (
            not pa.types.is_struct(actual)
            or tuple(actual.names) != tuple(name for name, _ in field.identity.identity_signature)
            or any(
                not _matches_type(kind, actual.field(name).type)
                for name, kind in field.identity.identity_signature
            )
        ):
            _integrity(
                "the complete declared Entity identity coordinate",
                "distribution coordinate signature differs",
            )
        if not _matches_type(field.logical_type_id, actual):
            _integrity("exact distribution coordinates", "distribution coordinate type differs")
    return tuple(field.name for field in keys)
