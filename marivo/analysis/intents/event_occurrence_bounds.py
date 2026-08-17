"""Observed occurrence-time bounds for exact Event and StateModel inputs."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo._compat import UTC
from marivo.analysis.errors import (
    AnalysisRepair,
    EventIdentityError,
    SemanticKindMismatchError,
)
from marivo.analysis.event import EventOccurrenceBounds
from marivo.analysis.executor.runner import execute
from marivo.analysis.executor.windowing import (
    datasource_read_timezone,
    effective_time_context,
)
from marivo.analysis.intents._event_occurrences import (
    _normalize_timestamp,
    _time_adapter,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, SemanticKind, StateModelKind
from marivo.semantic.catalog import (
    CatalogEntry,
    EventEntry,
    StateModelEntry,
    _normalize_semantic_input,
    _SemanticInput,
)
from marivo.semantic.errors import SemanticRuntimeError

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

_HELP_TARGET = "events.occurrence_bounds"
_BoundsTargetKind = EventKind | StateModelKind


def _repair(*, action: str, candidates: tuple[str, ...] = ()) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=_HELP_TARGET),
        candidates=candidates,
    )


def _resolve_target(
    *,
    session: Session,
    event_or_model: _SemanticInput[_BoundsTargetKind],
) -> EventEntry | StateModelEntry:
    catalog = session.catalog
    location = "session.events.occurrence_bounds.event_or_model"
    candidates = tuple(
        item.ref.key for item in (*catalog.events.items[:3], *catalog.state_models.items[:3])
    )
    try:
        normalized = _normalize_semantic_input(
            catalog,
            cast("Any", event_or_model),
            allowed_kinds=frozenset({SemanticKind.EVENT, SemanticKind.STATE_MODEL}),
            location=location,
        )
    except SemanticRuntimeError as exc:
        received = (
            event_or_model.ref if isinstance(event_or_model, CatalogEntry) else event_or_model
        )
        raise SemanticKindMismatchError(
            message="occurrence_bounds requires one exact current-catalog Event or StateModel",
            expected="EventEntry | Ref[event] | StateModelEntry | Ref[state_model]",
            received=(received.key if type(received) is Ref else type(event_or_model).__name__),
            location=location,
            repair=_repair(
                action="Inspect current catalog Events or StateModels and choose one exact input.",
                candidates=candidates,
            ),
        ) from exc
    entry = catalog.require(normalized)
    if not isinstance(entry, (EventEntry, StateModelEntry)):
        raise AssertionError(f"occurrence bounds target resolved to {type(entry).__name__}")
    return entry


def _target_events(entry: EventEntry | StateModelEntry) -> tuple[Ref[EventKind], ...]:
    if isinstance(entry, EventEntry):
        return (entry.ref,)
    details = entry.details()
    ordered = (
        *(event for event, _role, _path in details.inceptions),
        *(event for _source, event, _role, _path, _target in details.transitions),
    )
    return tuple(dict.fromkeys(ordered))


def _bounds_for_event(
    *,
    session: Session,
    event_ref: Ref[EventKind],
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    registry = session.catalog._require_index().registry
    resolver = session.catalog._semantic_resolver(connections=session._connection_runtime)
    event_ir = registry.events[event_ref.path]
    source_ir = registry.entities[event_ir.source_entity]
    table = resolver.event_occurrences(event_ref)
    identity_columns = tuple(
        table[f"__event_identity_{index}"] for index in range(len(event_ir.identity))
    )
    invalid_identity = identity_columns[0].isnull()
    if identity_columns[0].type().is_string():
        invalid_identity = invalid_identity | (identity_columns[0] == "")
    for column in identity_columns[1:]:
        component_invalid = column.isnull()
        if column.type().is_string():
            component_invalid = component_invalid | (column == "")
        invalid_identity = invalid_identity | component_invalid
    result = execute(
        table.aggregate(
            __earliest_occurrence_at=table["__occurred_at"].min(),
            __latest_occurrence_at=table["__occurred_at"].max(),
            __row_count=table.count(),
            __occurred_at_count=table["__occurred_at"].count(),
            __invalid_identity_count=invalid_identity.ifelse(1, 0).sum().fill_null(0),
            __max_source_identity_count=table["__source_identity_count"].max().fill_null(0),
        ),
        datasource_name=source_ir.datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    required = {
        "__earliest_occurrence_at",
        "__latest_occurrence_at",
        "__row_count",
        "__occurred_at_count",
        "__invalid_identity_count",
        "__max_source_identity_count",
    }
    if len(result.df) != 1 or not required.issubset(result.df.columns):
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} occurrence bounds query returned an invalid aggregate",
            expected="one row with earliest and latest occurrence timestamps",
            received=f"rows={len(result.df)}, columns={sorted(result.df.columns)!r}",
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}]",
            repair=_repair(action="Inspect the Event occurred_at definition and backend query."),
        )
    _adapter, occurred_adapter = _time_adapter(
        session=session,
        resolver=resolver,
        source_entity=event_ir.source_entity,
        occurred_at=event_ir.occurred_at,
        help_target=_HELP_TARGET,
        occurred_at_location=f"session.{_HELP_TARGET}.Event.occurred_at",
    )
    read_tz = datasource_read_timezone(session._connection_runtime, source_ir.datasource)
    context = effective_time_context(
        occurred_adapter.time_meta,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        field_expr=None,
        backend_policy=cast("Any", result.backend_datetime_decode_policy),
    )
    column_tz = context.effective_column_tz or read_tz
    row = result.df.iloc[0]
    try:
        row_count = int(row["__row_count"])
        occurred_at_count = int(row["__occurred_at_count"])
        invalid_identity_count = int(row["__invalid_identity_count"])
        max_source_identity_count = int(row["__max_source_identity_count"])
    except (TypeError, ValueError) as exc:
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} occurrence bounds query returned invalid counts",
            expected="non-negative integer occurrence validation counts",
            received=repr(
                {
                    "row_count": row["__row_count"],
                    "occurred_at_count": row["__occurred_at_count"],
                    "invalid_identity_count": row["__invalid_identity_count"],
                    "max_source_identity_count": row["__max_source_identity_count"],
                }
            ),
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}]",
            repair=_repair(action="Inspect the Event identity and occurred_at definitions."),
        ) from exc
    if any(
        count < 0
        for count in (
            row_count,
            occurred_at_count,
            invalid_identity_count,
            max_source_identity_count,
        )
    ):
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} occurrence bounds query returned invalid counts",
            expected="non-negative occurrence validation counts",
            received=repr(
                {
                    "row_count": row_count,
                    "occurred_at_count": occurred_at_count,
                    "invalid_identity_count": invalid_identity_count,
                    "max_source_identity_count": max_source_identity_count,
                }
            ),
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}]",
            repair=_repair(action="Inspect the Event identity and occurred_at definitions."),
        )
    if row_count and max_source_identity_count != 1:
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} declared identity is not unique",
            expected="one source occurrence per declared Event identity",
            received=f"max_source_identity_count={max_source_identity_count}",
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}].identity",
            repair=_repair(action="Inspect duplicate Event identities and their source rows."),
        )
    if invalid_identity_count:
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} produced an empty identity component",
            expected="a non-null, non-empty declared Event identity tuple",
            received=f"invalid_identity_count={invalid_identity_count}",
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}].identity",
            repair=_repair(action="Inspect null Event identity components and source rows."),
        )
    if occurred_at_count != row_count:
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} produced an invalid occurred_at value",
            expected="a non-null governed timestamp for every Event occurrence",
            received=f"row_count={row_count}, occurred_at_count={occurred_at_count}",
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}].occurred_at",
            repair=_repair(action="Inspect null Event occurred_at source values."),
        )
    earliest = _normalize_timestamp(
        row["__earliest_occurrence_at"],
        column_tz=column_tz,
        decode_policy=result.backend_datetime_decode_policy,
    )
    latest = _normalize_timestamp(
        row["__latest_occurrence_at"],
        column_tz=column_tz,
        decode_policy=result.backend_datetime_decode_policy,
    )
    if row_count and (earliest is None or latest is None):
        raise EventIdentityError(
            message=f"Event {event_ref.key!r} produced an invalid occurred_at value",
            expected="UTC-normalizable earliest and latest occurrence timestamps",
            received=f"earliest={earliest!r}, latest={latest!r}",
            location=f"session.{_HELP_TARGET}.materialized[{event_ref.key!r}].occurred_at",
            repair=_repair(action="Inspect invalid Event occurred_at source values."),
        )
    return earliest, latest


def occurrence_bounds(
    event_or_model: _SemanticInput[_BoundsTargetKind],
    *,
    session: Session,
) -> EventOccurrenceBounds:
    """Return observed occurrence-time bounds for one Event or StateModel.

    Args:
        event_or_model: Current-catalog ``EventEntry`` / ``Ref[event]`` or
            ``StateModelEntry`` / ``Ref[state_model]``. StateModel inputs infer
            their exact inception and transition Events.
        session: The active analysis session.

    Returns:
        ``EventOccurrenceBounds`` with UTC-normalized earliest and latest
        occurrence instants. An empty Event set returns ``None`` for both bounds.

    Guidance:
        The query evaluates the exact Event predicates, never a Datasource-wide
        maximum. The result helps choose a candidate replay or matching window;
        it does not prove completeness. Use ``session.events.watermark(...)``
        or the operation's coverage contract before classifying missing events.

    Example:
        >>> lifecycle = session.catalog.state_models.get("commerce.order_lifecycle")
        >>> bounds = session.events.occurrence_bounds(lifecycle)
        >>> print(bounds.latest_occurrence_at)
    """
    entry = _resolve_target(session=session, event_or_model=event_or_model)
    event_refs = _target_events(entry)
    bounds = tuple(_bounds_for_event(session=session, event_ref=ref) for ref in event_refs)
    earliest_values = tuple(value for value, _latest in bounds if value is not None)
    latest_values = tuple(value for _earliest, value in bounds if value is not None)
    earliest = min(earliest_values) if earliest_values else None
    latest = max(latest_values) if latest_values else None
    return EventOccurrenceBounds(
        target_ref=cast("Ref[EventKind | StateModelKind]", entry.ref),
        event_refs=event_refs,
        earliest_occurrence_at=(earliest.to_pydatetime() if earliest is not None else None),
        latest_occurrence_at=(latest.to_pydatetime() if latest is not None else None),
        observed_at=datetime.now(UTC),
    )


__all__ = ["occurrence_bounds"]
