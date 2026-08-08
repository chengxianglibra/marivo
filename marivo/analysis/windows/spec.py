"""Typed analysis time-window specifications."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)

from marivo._temporal import Grain as TemporalGrain
from marivo._temporal import PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.windows.grain import (
    Grain,
    GrainInput,
    normalize_grain,
)

__all__ = [
    "AbsoluteWindow",
    "Grain",
    "GrainInput",
    "TimeScope",
    "TimeScopeInput",
    "bind_temporal_window",
    "dump_window",
    "is_date_only",
    "make_absolute_window",
    "normalize_absolute_window_input",
    "normalize_grain",
    "normalize_timescope_input",
]


def is_date_only(value: str) -> bool:
    """Return True if *value* is a bare date string like ``"2026-07-01"``."""
    if len(value) != 10 or "T" in value:
        return False
    try:
        from datetime import date as _date

        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


class AbsoluteWindow(BaseModel):
    """Call marivo.help(AbsoluteWindow) for its public consumption contract.

    Half-open time interval [start, end) with optional grain and time
    dimension.  For date-only strings like ``"2026-07-31"``, the exclusive
    end means data from that date is **not** included.  To include all of
    July, use ``end="2026-08-01"``.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    kind: Literal["absolute"] = "absolute"
    start: str
    end: str
    grain: Grain | TemporalGrain | None = None
    time_dimension: str | None = None
    # These fields are execution-only bindings. They are excluded from the
    # public window payload and are populated after the catalog has resolved a
    # semantic grain/scope to its immutable certified snapshot.
    semantic_scope: TimeScope | None = Field(default=None, exclude=True)
    temporal_snapshot: PeriodCalendarSnapshotV1 | None = Field(default=None, exclude=True)

    @field_validator("grain", mode="before")
    @classmethod
    def _normalize_grain(cls, value: Any) -> Grain | None:
        return cast("Grain | None", normalize_grain(value))

    @field_serializer("grain")
    def _serialize_grain(self, value: Grain | TemporalGrain | None) -> object:
        if value is None:
            return None
        if isinstance(value, TemporalGrain) and value.kind == "semantic":
            assert value.calendar is not None and value.level is not None
            return {
                "kind": "semantic",
                "calendar_ref": value.calendar.path,
                "level": value.level,
            }
        return value.to_token()


TimeScopeInput = TimeScope | dict[str, Any] | None


def _raise_timescope_model_invalid(
    *,
    raw: dict[str, Any],
    error: ValidationError,
) -> None:
    misplaced = [key for key in ("grain", "time_dimension") if key in raw]
    hint = None
    if misplaced:
        hint = (
            f"time_scope holds only start/end; pass {', '.join(misplaced)} as "
            "observe(..., grain=..., time_dimension=...) arguments, not inside time_scope."
        )
    raise WindowInvalidError(
        message="time_scope form is invalid",
        hint=hint,
        context={
            "kind": "TimeScopeModelInvalid",
            "time_scope": dict(raw),
            "validation_errors": error.errors(),
        },
    ) from error


def normalize_timescope_input(raw: object) -> TimeScope | None:
    if raw is None:
        return None
    if isinstance(raw, TimeScope):
        return raw
    if isinstance(raw, AbsoluteWindow):
        # Internal callers (e.g. discover window candidates fed to
        # transform.window) still pass a resolved AbsoluteWindow; reduce it to
        # its period. AbsoluteWindow is intentionally absent from the public
        # TimeScopeInput type so observe callers use time_scope + grain/time_dimension.
        return TimeScope(start=raw.start, end=raw.end)
    if isinstance(raw, dict):
        try:
            return TimeScope.model_validate(raw)
        except ValidationError as exc:
            _raise_timescope_model_invalid(raw=raw, error=exc)
    raise WindowInvalidError(
        message=f"unsupported time_scope input type {type(raw).__name__}",
        context={"kind": "TimeScopeTypeInvalid", "time_scope": repr(raw)},
    )


def normalize_absolute_window_input(raw: object) -> AbsoluteWindow | None:
    if raw is None:
        return None
    if isinstance(raw, AbsoluteWindow):
        return raw
    if isinstance(raw, TimeScope):
        return AbsoluteWindow(
            start=raw.start.isoformat() if not isinstance(raw.start, str) else raw.start,
            end=raw.end.isoformat() if not isinstance(raw.end, str) else raw.end,
        )
    if isinstance(raw, dict):
        try:
            return AbsoluteWindow.model_validate(raw)
        except ValidationError as exc:
            raise WindowInvalidError(
                message="absolute window form is invalid",
                context={
                    "kind": "AbsoluteWindowModelInvalid",
                    "window": dict(raw),
                    "validation_errors": exc.errors(),
                },
            ) from exc
    raise WindowInvalidError(
        message=f"unsupported absolute window input type {type(raw).__name__}",
        context={"kind": "AbsoluteWindowTypeInvalid", "window": repr(raw)},
    )


def make_absolute_window(
    timescope: TimeScope | None,
    *,
    grain: GrainInput = None,
    time_dimension: str | None = None,
) -> AbsoluteWindow | None:
    if timescope is None:
        if grain is None and time_dimension is None:
            return None
        raise WindowInvalidError(
            message="time_scope is required when grain or time_dimension is provided",
            hint='Pass time_scope={"start": "2026-07-01", "end": "2026-08-01"}.',
            context={"kind": "TimeScopeRequired"},
        )
    resolved_grain = normalize_grain(grain)

    def _as_absolute_bound(value: object) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return AbsoluteWindow(
        start=_as_absolute_bound(timescope.start),
        end=_as_absolute_bound(timescope.end),
        grain=resolved_grain,
        time_dimension=time_dimension,
        semantic_scope=timescope if timescope.kind == "calendar_period" else None,
    )


def bind_temporal_window(
    window: AbsoluteWindow | None,
    *,
    snapshot: PeriodCalendarSnapshotV1 | None,
) -> AbsoluteWindow | None:
    """Attach one immutable calendar snapshot to an execution window."""
    if window is None:
        return None
    if (
        snapshot is None
        and isinstance(window.grain, TemporalGrain)
        and window.grain.kind == "semantic"
    ):
        raise WindowInvalidError(
            message="semantic grain has no current certified period snapshot",
            hint="Preview the period calendar with one exhaustive persisted snapshot, then retry.",
            context={"kind": "SemanticGrainSnapshotMissing", "grain": window.grain.to_token()},
        )
    return window.model_copy(update={"temporal_snapshot": snapshot})


def dump_window(window: AbsoluteWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return window.model_dump(mode="json")
