"""Native distribution integrity inspection without numeric sample transfer."""

from __future__ import annotations

from collections.abc import Callable

import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.distribution import distribution_validations
from marivo.analysis.datasets.descriptors import DatasetRowContract, _EntityFieldIdentity
from marivo.analysis.materialization.storage import _integrity, _matches_type
from marivo.analysis.observation.distribution_contracts import (
    FREQUENCY,
    VALUE,
    distribution_part_authorities,
)


def validate_distribution_relation(
    backend: Backend,
    table: ir.Table,
    primary: ir.Table,
    row: DatasetRowContract,
    role: str,
    record: Callable[[str, str], None],
) -> None:
    authority = next(
        (item for name, item in distribution_part_authorities(row) if name == role), None
    )
    if authority is None or authority.distribution is None:
        _integrity("one exact distribution authority", "unknown distribution role")
    schema = table.schema().to_pyarrow()
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
    checks = distribution_validations(row, primary, {role: table}, required=False)
    for check in checks:
        sql = backend.compile(check.expression)
        record("engine_check." + check.name, sql)
        if backend.raw_sql(sql).fetchone()[0] != 0:
            _integrity(
                "complete exact distribution and independent endpoint",
                "distribution integrity failed",
            )
