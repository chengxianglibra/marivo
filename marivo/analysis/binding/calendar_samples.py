"""Bind internal sample clocks independently of observation selection."""

from __future__ import annotations

from marivo._definition.expressions import BuiltinSampleCoordinate
from marivo._definition.types import CalendarCoordinate
from marivo._temporal import grain_nests
from marivo._timezones import same_timezone
from marivo.analysis.core.model import reject


def validate_sample_parent(
    inner: BuiltinSampleCoordinate, outer: BuiltinSampleCoordinate | CalendarCoordinate
) -> None:
    """Qualify same-field hierarchies after each contribution's clocks are known."""
    if not same_timezone(inner.timezone, outer.timezone):
        reject(
            "matching timezones for calendar layers on the same field",
            f"Session {inner.timezone!r}, parent axis {outer.timezone!r}",
            "Align the CalendarAxis with the Session report timezone, or use TimeScope "
            "to select an observation period without an enclosing calendar axis.",
            "Metric.per",
            canonical_id="session.observe",
        )
    if not grain_nests(inner.grain_spec, outer.grain_spec):
        reject(
            "a supported static child-to-parent calendar grain combination on the same field",
            f"{inner.grain_spec._to_token()} within {outer.grain_spec._to_token()}",
            'Choose m.per(mv.grain("day")) or a finer grain nested in the enclosing calendar.',
            "Metric.per",
            canonical_id="session.observe",
        )
