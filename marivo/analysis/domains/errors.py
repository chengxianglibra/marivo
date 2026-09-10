"""Structured errors owned by private Event construction and coverage."""

from marivo.analysis.datasets.errors import DatasetConstructionError


class EventConstructionError(DatasetConstructionError):
    """An Event definition lacks exact subject or occurrence authority."""


class EventCompletenessError(EventConstructionError):
    """A declaration or receipt lacks the requested exact coverage authority."""


def event_error(
    expected: str, received: str, *, location: str = "events.match"
) -> EventConstructionError:
    return EventConstructionError(
        expected=expected,
        received=received,
        location=location,
        repair="Rebuild the Event source with exact current Event roles, subject membership, and explicit aware temporal bounds.",
    )


def completeness_error(
    expected: str, received: str, *, location: str = "events.completeness"
) -> EventCompletenessError:
    return EventCompletenessError(
        expected=expected,
        received=received,
        location=location,
        repair="Provide exact consumed Event refs and sufficient aware coverage bounds with the bound datasource authority; declarations remain explicit assumptions.",
    )
