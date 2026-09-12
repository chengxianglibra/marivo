"""Canonical native ordering for exact retained Dataset row contracts."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
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
    from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics

    if isinstance(row.family_semantics, FunnelDeltaSemantics):
        from marivo.analysis.compiler.event_reducers import canonical_funnel_rows

        return canonical_funnel_rows(
            table,
            step_keys=tuple(
                step.key for step in row.family_semantics.current.journey.pattern.steps
            ),
            axis_columns=tuple(
                f.name for f in row.schema.columns[: len(row.family_semantics.current.axis_refs)]
            ),
        )
    if row.shape_id.family_id == "lifecycle":
        from marivo.analysis.compiler.lifecycle_reducers import canonical_rows

        return canonical_rows(table, row)
    if row.shape_id.family_id == "event":
        from marivo.analysis.compiler.event import canonical_event_rows
        from marivo.analysis.compiler.event_reducers import (
            canonical_funnel_rows,
            canonical_time_to_event_rows,
        )
        from marivo.analysis.domains.contracts import (
            EventFunnelSemantics,
            EventJourneySemantics,
            EventTimeToEventSemantics,
        )

        semantics = row.family_semantics
        if isinstance(semantics, EventTimeToEventSemantics):
            return canonical_time_to_event_rows(table)
        if isinstance(semantics, EventFunnelSemantics):
            return canonical_funnel_rows(
                table,
                step_keys=tuple(step.key for step in semantics.journey.pattern.steps),
                axis_columns=tuple(
                    field.name for field in row.schema.columns[: len(semantics.axis_refs)]
                ),
            )
        if not isinstance(semantics, EventJourneySemantics):
            raise compilation_error("Event journey ordering authority", "missing journey semantics")
        return canonical_event_rows(table, tuple(step.key for step in semantics.pattern.steps))
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
