"""Public governed observed Event completeness watermark lookup.

``lifecycle.replay`` and ``events.match`` teach callers to prefer an observed
watermark over ``mv.declared_complete_through(...)``, but historically that
observed watermark had no public SDK entry — it was only resolved internally by
``resolve_event_coverage``.  This module exposes the same catalog-fact
resolution as a session-bound read under the ``session.events`` namespace: given
one exact current-catalog Event, it builds the backend
``EventWatermarkRequest`` and returns the provider's authoritative
``EventWatermarkReceipt`` (or ``None`` when no provider exists or the provider
has no authoritative watermark for that Event).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from marivo.analysis.errors import (
    AnalysisRepair,
    InvalidCompletenessDeclarationError,
    SemanticKindMismatchError,
)
from marivo.analysis.event import EventWatermarkReceipt, EventWatermarkRequest
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, SemanticKind
from marivo.semantic.catalog import (
    CatalogEntry,
    EventEntry,
    _normalize_semantic_input,
    _SemanticInput,
)
from marivo.semantic.errors import SemanticRuntimeError

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

_HELP_TARGET = "events.watermark"


def _repair(*, action: str, candidates: tuple[str, ...] = ()) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=_HELP_TARGET),
        candidates=candidates,
    )


def _resolve_event_ref(
    *,
    session: Session,
    event: _SemanticInput[EventKind],
) -> Ref[EventKind]:
    """Normalize one exact current-catalog Event entry or ref."""
    catalog = session.catalog
    location = "session.events.watermark.event"
    candidates = tuple(item.ref.key for item in catalog.events.items[:5])
    try:
        normalized = _normalize_semantic_input(
            catalog,
            cast("Any", event),
            allowed_kinds=frozenset({SemanticKind.EVENT}),
            location=location,
        )
    except SemanticRuntimeError as exc:
        received = event.ref if isinstance(event, CatalogEntry) else event
        raise SemanticKindMismatchError(
            message="events.watermark requires one exact current-catalog Event",
            expected="EventEntry | Ref[event]",
            received=(received.key if type(received) is Ref else type(event).__name__),
            location=location,
            repair=_repair(
                action="Inspect current catalog Events and choose an exact Event.",
                candidates=candidates,
            ),
        ) from exc
    return cast("Ref[EventKind]", normalized)


def watermark(
    event: _SemanticInput[EventKind],
    *,
    through: str,
    session: Session,
) -> EventWatermarkReceipt | None:
    """Return the authoritative observed completeness watermark for one Event.

    Args:
        event: Current-catalog ``EventEntry`` or exact ``Ref[event]``.
        through: Inclusive completeness bound the caller requires the Event to
            be complete through.
        session: The active analysis session.

    Returns:
        The provider's authoritative ``EventWatermarkReceipt`` when one exists,
        otherwise ``None``. ``None`` means the session has no watermark provider
        for the Event's datasource, or the provider returned no authoritative
        watermark for this exact Event.

    Guidance:
        This is an observed fact from a backend completeness provider, not a
        caller assumption. It is strictly stronger than
        ``mv.declared_complete_through(...)``; replay and events.match consume
        this same receipt through their authoritative coverage resolution. When
        ``None`` is returned, fall back to an explicit governed declaration only
        when you can supply a rationale.

    Example:
        >>> order_created = session.catalog.events.get("commerce.order_created")
        >>> watermark = session.events.watermark(
        ...     order_created,
        ...     through="2026-08-01T00:00:00Z",
        ... )
        >>> if watermark is None:
        ...     # No observed watermark; choose a governed declaration instead.
        ...     coverage = mv.declared_complete_through(
        ...         inputs=(order_created.ref,),
        ...         through="2026-08-01T00:00:00Z",
        ...         rationale="Reconciled through the follow-up bound.",
        ...     )
        ... else:
        ...     print(watermark.complete_through)
    """
    if not through.strip():
        raise InvalidCompletenessDeclarationError(
            message="events.watermark requires a non-empty through bound",
            expected="a non-empty completeness bound",
            received=repr(through),
            location="session.events.watermark.through",
            repair=_repair(action="Provide a non-empty completeness bound."),
        )
    event_ref = _resolve_event_ref(session=session, event=event)
    entry = session.catalog.require(event_ref)
    if not isinstance(entry, EventEntry):
        raise AssertionError(f"Event ref resolved to {type(entry).__name__}")
    details = entry.details()
    registry = session.catalog._require_index().registry
    event_ir = registry.events[event_ref.path]
    source_ir = registry.entities[event_ir.source_entity]

    request = EventWatermarkRequest(
        event_ref=event_ref,
        event_fingerprint=details.definition_fingerprint,
        source_entity_ref=event_ir.source_entity,
        occurred_at_ref=event_ir.occurred_at,
        required_through=through,
    )
    return cast(
        "EventWatermarkReceipt | None",
        session._connection_runtime.event_watermark(source_ir.datasource, request),
    )


__all__ = ["watermark"]
