"""Period-calendar declarations for the first temporal-semantics slice."""

from __future__ import annotations

import datetime as _datetime
from collections.abc import Mapping
from dataclasses import dataclass

from marivo._temporal import Grain, semantic_grain
from marivo.refs import (
    DimensionKind,
    DomainKind,
    PeriodCalendarKind,
    Ref,
    SemanticKind,
    TimeDimensionKind,
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
from marivo.semantic.ir import PeriodCalendarIR
from marivo.semantic.typing import AiContextValue


@dataclass(frozen=True, init=False)
class PeriodCorrespondence:
    """An authored same-level baseline-key mapping owned by one calendar."""

    level: str
    baseline_key: Ref[DimensionKind]

    @classmethod
    def _create(cls, *, level: str, baseline_key: Ref[DimensionKind]) -> PeriodCorrespondence:
        value = object.__new__(cls)
        object.__setattr__(value, "level", level)
        object.__setattr__(value, "baseline_key", baseline_key)
        return value


def period_correspondence(*, level: str, baseline_key: Ref[DimensionKind]) -> PeriodCorrespondence:
    """Declare the baseline-key field for one named calendar correspondence."""
    if type(level) is not str or not level or level == "day":
        _raise(
            ErrorKind.INVALID_REF,
            "correspondence level must be a non-empty declared calendar level.",
            cls=SemanticDecoratorError,
        )
    _require_ref_id(
        baseline_key,
        parameter="baseline_key",
        expected=(SemanticKind.DIMENSION,),
    )
    return PeriodCorrespondence._create(level=level, baseline_key=baseline_key)


def period_calendar(
    *,
    name: str,
    date: Ref[TimeDimensionKind],
    boundary_timezone: str,
    coverage: tuple[_datetime.date, _datetime.date],
    levels: Mapping[str, Ref[DimensionKind]],
    correspondences: Mapping[str, PeriodCorrespondence] | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[PeriodCalendarKind]:
    """Declare one finite governed calendar over an exhaustive civil-date spine.

    The declaration fixes source fields and business coverage. Certification of
    row values remains a separate query-free operation over one persisted
    datasource snapshot.
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
    normalized_correspondences: list[tuple[str, str, str]] = []
    if correspondences is not None:
        if not isinstance(correspondences, Mapping):
            _raise(
                ErrorKind.INVALID_REF,
                "correspondences= must be a mapping of names to period_correspondence(...).",
                cls=SemanticDecoratorError,
            )
        for correspondence_name, correspondence in correspondences.items():
            if type(correspondence_name) is not str or not correspondence_name:
                _raise(
                    ErrorKind.INVALID_REF,
                    "correspondence names must be non-empty strings.",
                    cls=SemanticDecoratorError,
                )
            if type(correspondence) is not PeriodCorrespondence:
                _raise(
                    ErrorKind.INVALID_REF,
                    "correspondence values must come from ms.period_correspondence(...).",
                    cls=SemanticDecoratorError,
                )
            if correspondence.level not in dict(normalized):
                _raise(
                    ErrorKind.INVALID_REF,
                    f"correspondence {correspondence_name!r} names undeclared level {correspondence.level!r}.",
                    cls=SemanticDecoratorError,
                )
            baseline_id = _require_ref_id(
                correspondence.baseline_key,
                parameter=f"correspondences[{correspondence_name!r}].baseline_key",
                expected=(SemanticKind.DIMENSION,),
            )
            normalized_correspondences.append(
                (correspondence_name, correspondence.level, baseline_id)
            )

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
            correspondences=tuple(normalized_correspondences),
        ),
        None,
    )
    return ref


def calendar_grain(*, calendar: Ref[PeriodCalendarKind], level: str) -> Grain:
    """Construct one semantic aggregation grain from a period-calendar level.

    The level is finally checked against the calendar's certified snapshot when
    it is consumed; model code can safely construct this value before loading.
    """
    _require_ref_id(calendar, parameter="calendar", expected=(SemanticKind.PERIOD_CALENDAR,))
    if type(level) is not str or not level:
        _raise(
            ErrorKind.INVALID_REF,
            "level= must be a non-empty declared level name.",
            cls=SemanticDecoratorError,
        )
    return semantic_grain(calendar=calendar, level=level)
