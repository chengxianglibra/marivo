"""Canonical native ordering for exact retained Dataset row contracts."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _OrderedOrdering,
)


def ordered_relation(
    table: ir.Table,
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
) -> ir.Table:

    fields = {str(field.field_id): field.name for field in row.schema.columns}
    if not isinstance(rows.ordering, _OrderedOrdering):
        if not row.key_field_ids:
            return table
        return table.order_by(
            [ibis.asc(table[fields[str(key)]], nulls_first=False) for key in row.key_field_ids]
        )
    return table.order_by(
        [
            ibis.asc(table[fields[str(term.field_id)]], nulls_first=term.nulls == "first")
            if term.direction == "ascending"
            else ibis.desc(table[fields[str(term.field_id)]], nulls_first=term.nulls == "first")
            for term in rows.ordering.terms
        ]
    )
