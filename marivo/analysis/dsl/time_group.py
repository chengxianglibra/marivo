"""Capture a finite output calendar on an explicit source time field."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.grouping import FieldCalendarBinding, _FieldCalendarGrouping
from marivo._definition.types import FieldRead, field_read_key
from marivo._grouping import Grouping
from marivo._temporal import CalendarAxis
from marivo.analysis.core.model import reject
from marivo.semantic._deferred_semantics import consume_field
from marivo.semantic._expression_values import RowExpression, TimeExpression
from marivo.semantic.errors import SemanticError


def time_group(field: TimeExpression, *, axis: CalendarAxis) -> Grouping:
    """Filter contributions and group their field values on a finite calendar.

    Args:
        field: A resolved TimeDimension, optionally through a single-valued path.
        axis: A scoped CalendarAxis supplying the range, grain and timezone.

    Returns:
        An immutable field calendar Grouping for Session.observe.by.

    Example:
        ``session.observe({"amount": amount}, by=(mv.time_group(created_at, axis=days),))``.

    Constraints: Captures without source reads. The half-open field range
    intersects other conditions before reduction; empty output buckets are
    completed. This grouping does not provide Metric window endpoints.
    """
    if not isinstance(field, RowExpression) or field._role != "time":
        reject(
            "a resolved source TimeDimension",
            type(field).__name__,
            "Select a concrete time field from the Catalog.",
            "time_group.field",
            canonical_id="time_group",
        )
    if not isinstance(axis, CalendarAxis) or axis.scope is None:
        reject(
            "a CalendarAxis with an explicit scope",
            type(axis).__name__,
            "Construct the axis with scope.by(grain).",
            "time_group.axis",
            canonical_id="time_group",
        )
    try:
        facts = consume_field(field)
    except SemanticError as error:
        reject(
            "a resolved source TimeDimension",
            str(error),
            "Use a named TimeDimension or its single-valued via path.",
            "time_group.field",
            canonical_id="time_group",
        )
    read = facts.field_read or FieldRead(facts.reference, facts.root)
    calendar = axis._binding
    if calendar.grain_spec.kind == "semantic" and (
        calendar.snapshot is None
        or len(calendar.cells) != 1
        or calendar.cells[0][:2] != calendar.cells[0][2:]
    ):
        reject(
            "one complete certified custom calendar period",
            calendar.grain,
            "Build the axis from one full period in a certified calendar.",
            "time_group.axis",
            canonical_id="time_group",
        )
    calendar = replace(
        calendar, identity=f"field_calendar:{field_read_key(read)}:{calendar.identity}"
    )
    return _FieldCalendarGrouping(FieldCalendarBinding(read, calendar), field._context)
