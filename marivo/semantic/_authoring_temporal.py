"""Authoring helpers for governed calendar and named temporal-set semantics."""

from __future__ import annotations

import datetime as _datetime
from collections.abc import Mapping

from ibis.common.deferred import Deferred

from marivo._symbolic import (
    DimensionSymbol,
    DomainSymbol,
    PeriodCalendarSymbol,
    TemporalSetSymbol,
    TimeDimensionSymbol,
    symbol_factory,
)
from marivo._temporal import Grain, semantic_grain
from marivo.refs import (
    SemanticKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _require_ctx,
    _require_ref_id,
    _resolve_domain,
)
from marivo.semantic._authoring_validation import _validate_timezone
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import PeriodCalendarIR, TemporalSetIR
from marivo.semantic.typing import AiContextValue


def period_calendar(
    *,
    name: str,
    date: Deferred | TimeDimensionSymbol,
    boundary_timezone: str,
    coverage: tuple[_datetime.date, _datetime.date],
    levels: Mapping[str, Deferred | DimensionSymbol],
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> PeriodCalendarSymbol:
    """Declare one finite governed calendar over an exhaustive civil-date spine.

    The declaration fixes source fields and business coverage without reading
    data. ``catalog.preview(..., scope=...)`` reads the current source over the
    complete declared coverage and publishes a certified semantic snapshot.

    Args:
        name: Calendar name.
        date: Native day TimeField or explicit lookup symbol.
        boundary_timezone: IANA boundary zone.
        coverage: Half-open civil-date coverage tuple.
        levels: Level names mapped to native Dimensions or lookup symbols.
        domain: Optional domain lookup symbol.
        ai_context: Optional business context.
    Returns: A typed calendar lookup symbol, validated with the physical closure.
    Example: ``ms.period_calendar(name="fiscal", date=day, boundary_timezone="UTC", coverage=coverage, levels={"week": week})``.
    Constraints: Fields resolve by kind/path in the completed calendar scope; lookup symbols support forward references.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    if type(name) is not str or not name:
        _raise(
            ErrorKind.INVALID_REF,
            "period calendar name must be a non-empty string.",
            cls=SemanticDecoratorError,
        )
    date_id = _require_ref_id(date, parameter="date", expected=(SemanticKind.TIME_DIMENSION,))
    _validate_timezone(boundary_timezone)
    if (
        type(coverage) is not tuple
        or len(coverage) != 2
        or type(coverage[0]) is not _datetime.date
        or type(coverage[1]) is not _datetime.date
        or coverage[0] >= coverage[1]
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "coverage= must be a non-empty half-open tuple of exact civil dates.",
            cls=SemanticDecoratorError,
        )
    if not isinstance(levels, Mapping) or not levels or "day" in levels:
        _raise(
            ErrorKind.INVALID_REF,
            "levels= must be a non-empty mapping and cannot declare reserved level 'day'.",
            cls=SemanticDecoratorError,
        )
    normalized: list[tuple[str, str]] = []
    for level, value in levels.items():
        if type(level) is not str or not level:
            _raise(
                ErrorKind.INVALID_REF,
                "calendar level names must be non-empty strings.",
                cls=SemanticDecoratorError,
            )
        level_ref = _require_ref_id(
            value, parameter=f"levels[{level!r}]", expected=(SemanticKind.DIMENSION,)
        )
        normalized.append((level, level_ref))
    semantic_id = f"{resolved_domain}.{name}"
    ref = ref_factory.period_calendar(semantic_id)
    _check_duplicate(ctx, semantic_id, PeriodCalendarIR)
    _push_ir(
        ctx,
        ref,
        PeriodCalendarIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=name,
            date=date_id,
            boundary_timezone=boundary_timezone,
            coverage=(coverage[0].isoformat(), coverage[1].isoformat()),
            levels=tuple(normalized),
            ai_context=_build_ai_context(ai_context),
            python_symbol=name,
            location=_caller_location(),
        ),
        None,
    )
    return symbol_factory.period_calendar(ref.path)


def calendar_grain(*, calendar: PeriodCalendarSymbol, level: str) -> Grain:
    """Construct one semantic aggregation grain from a period-calendar level.

    Args:
        calendar: Typed lookup key of the governed period calendar.
        level: Exact declared level name, or the reserved ``day`` level.
    Returns: An immutable semantic Grain without certification or source reads.
    Example: ``ms.calendar_grain(calendar=ms.ref.period_calendar('sales.fiscal'), level='quarter')``.
    Constraints: A Metric window resolves the calendar by kind/path and checks the level in the consuming Session's frozen certified scope. Model code can construct this value before loading or certification.
    """
    _require_ref_id(calendar, parameter="calendar", expected=(SemanticKind.PERIOD_CALENDAR,))
    if type(level) is not str or not level:
        _raise(
            ErrorKind.INVALID_REF,
            "level= must be a non-empty declared level name.",
            cls=SemanticDecoratorError,
        )
    return semantic_grain(
        calendar=ref_factory.period_calendar(
            _require_ref_id(
                calendar, parameter="calendar", expected=(SemanticKind.PERIOD_CALENDAR,)
            )
        ),
        level=level,
    )


def temporal_set(
    *,
    name: str,
    occurrence_id: Deferred | DimensionSymbol,
    start: Deferred | TimeDimensionSymbol,
    end: Deferred | TimeDimensionSymbol,
    boundary_timezone: str,
    coverage: tuple[_datetime.date, _datetime.date],
    category: Deferred | DimensionSymbol | None = None,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> TemporalSetSymbol:
    """Declare one finite governed set of named temporal occurrences.

    Args:
        name: Set name.
        occurrence_id: Native identity Dimension or lookup symbol.
        start: Native start TimeField or lookup symbol.
        end: Native end TimeField or lookup symbol.
        boundary_timezone: IANA boundary zone.
        coverage: Half-open civil-date coverage tuple.
        category: Optional native category Dimension or lookup symbol.
        domain: Optional domain lookup symbol.
        ai_context: Optional business context.
    Returns: A typed temporal-set lookup symbol.
    Example: ``ms.temporal_set(name="campaigns", occurrence_id=identifier, start=start, end=end, boundary_timezone="UTC", coverage=coverage)``.
    Constraints: The completed scope resolves input fields and checks kind, ownership and time roles; Symbols retain forward-reference semantics.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    if type(name) is not str or not name:
        _raise(
            ErrorKind.INVALID_REF,
            "temporal set name must be a non-empty string.",
            cls=SemanticDecoratorError,
        )
    occurrence_id_value = _require_ref_id(
        occurrence_id,
        parameter="occurrence_id",
        expected=(SemanticKind.DIMENSION,),
    )
    start_value = _require_ref_id(
        start,
        parameter="start",
        expected=(SemanticKind.TIME_DIMENSION,),
    )
    end_value = _require_ref_id(
        end,
        parameter="end",
        expected=(SemanticKind.TIME_DIMENSION,),
    )
    category_value = (
        None
        if category is None
        else _require_ref_id(category, parameter="category", expected=(SemanticKind.DIMENSION,))
    )
    _validate_timezone(boundary_timezone)
    if (
        type(coverage) is not tuple
        or len(coverage) != 2
        or type(coverage[0]) is not _datetime.date
        or type(coverage[1]) is not _datetime.date
        or coverage[0] >= coverage[1]
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "coverage= must be a non-empty half-open tuple of exact civil dates.",
            cls=SemanticDecoratorError,
        )
    semantic_id = f"{resolved_domain}.{name}"
    ref = ref_factory.temporal_set(semantic_id)
    _check_duplicate(ctx, semantic_id, TemporalSetIR)
    _push_ir(
        ctx,
        ref,
        TemporalSetIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=name,
            occurrence_id=occurrence_id_value,
            start=start_value,
            end=end_value,
            boundary_timezone=boundary_timezone,
            coverage=(coverage[0].isoformat(), coverage[1].isoformat()),
            category=category_value,
            ai_context=_build_ai_context(ai_context),
            python_symbol=name,
            location=_caller_location(),
        ),
        None,
    )
    return symbol_factory.temporal_set(ref.path)
