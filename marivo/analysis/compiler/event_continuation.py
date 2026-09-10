"""Shared native dispatch for exact retained Event continuations."""

from __future__ import annotations

from datetime import datetime

import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.event import canonical_event_rows
from marivo.analysis.compiler.event_reducers import (
    canonical_funnel_rows,
    canonical_time_to_event_rows,
    compile_event_funnel,
    compile_event_subject_selection,
    compile_event_time_to_event,
    funnel_output_proof,
    time_to_event_output_proof,
)
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventFunnelSemantics,
    EventJourneySemantics,
    EventSelectionPayload,
    EventTimeToEventPayload,
    EventTimeToEventSemantics,
)


def canonical_rows(table: ir.Table, row: DatasetRowContract) -> ir.Table:
    semantics = row.family_semantics
    if isinstance(semantics, EventJourneySemantics):
        return canonical_event_rows(table, tuple(step.key for step in semantics.pattern.steps))
    if isinstance(semantics, EventFunnelSemantics):
        return canonical_funnel_rows(
            table,
            step_keys=tuple(step.key for step in semantics.journey.pattern.steps),
            axis_columns=tuple(
                field.name for field in row.schema.columns if field.role_id == "dimension"
            ),
        )
    if isinstance(semantics, EventTimeToEventSemantics):
        return canonical_time_to_event_rows(table)
    raise compilation_error("exact Event semantics", "unsupported Event ordering shape")


def reduce_event(
    table: ir.Table,
    payload: EventFunnelPayload | EventTimeToEventPayload | EventSelectionPayload,
    coverage: EventCoverageResolution,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    if isinstance(payload, EventFunnelPayload):
        return compile_event_funnel(
            table,
            semantics=payload.semantics.journey,
            coverage=coverage,
            axis_columns=tuple(axis.dimension.ref.path.rsplit(".", 1)[-1] for axis in payload.axes),
        )
    if isinstance(payload, EventTimeToEventPayload):
        return compile_event_time_to_event(
            table,
            semantics=payload.semantics.journey,
            coverage=coverage,
            from_step=payload.semantics.from_step,
            to_step=payload.semantics.to_step,
        )
    return compile_event_subject_selection(
        table,
        semantics=payload.journey,
        coverage=coverage,
        selection=payload.selection,
    )


def result_proof(table: ir.Table, row: DatasetRowContract, *, filtered: bool) -> ir.Table:
    semantics = row.family_semantics
    if isinstance(semantics, EventFunnelSemantics):
        return funnel_output_proof(
            table,
            step_keys=tuple(step.key for step in semantics.journey.pattern.steps),
            axis_columns=tuple(
                field.name for field in row.schema.columns if field.role_id == "dimension"
            ),
            require_dense=not filtered,
        )
    if isinstance(semantics, EventTimeToEventSemantics):
        return time_to_event_output_proof(
            table,
            completion_through=datetime.fromisoformat(semantics.journey.completion_through),
        )
    raise compilation_error("an exact Event reducer result", "unsupported Event proof shape")
