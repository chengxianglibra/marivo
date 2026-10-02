"""Positive static contract for the frozen Anchor overloads and windows."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    session = mv.session.get_or_create("typing-anchors", report_timezone="UTC")
    population = session.members(ms.ref.entity("commerce.subjects"))
    during = mv.time_scope(start="2026-02-01", end="2026-02-02")
    participant = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    anchors = session.anchors(participant, population=population, during=during)
    assert_type(anchors, mv.LogicalAnchorDomain)
    assert_type(anchors.execute(), mv.MaterializedAnchorDomain)
    span = mv.duration(hours=24)
    assert_type(span, mv.Duration)
    window = mv.elapsed(span)
    assert_type(window, mv.ElapsedWindow)
    civil = mv.calendar_days(1, ZoneInfo("America/New_York"))
    assert_type(civil, mv.CalendarWindow)
    observed = anchors.observe(
        ms.ref.metric("commerce.revenue"),
        within=window,
        via=ms.ref.relationship("commerce.participant"),
    )
    assert_type(observed, mv.LogicalNumericRelation)
    assert_type(observed.execute(), mv.MaterializedNumericRelation)
    journey = session.events.match(
        mv.sequence(mv.step(participant=participant, key="start")),
        population=population,
        cohort_window=during,
        completion_through=datetime(2026, 2, 3, tzinfo=timezone.utc),
        matching=mv.every_start(completion_assignment="shared"),
    )
    assert_type(
        session.anchors(journey, population=population, during=during), mv.LogicalAnchorDomain
    )
    assert_type(
        session.anchors(journey.execute(), population=population.execute(), during=during),
        mv.LogicalAnchorDomain,
    )
