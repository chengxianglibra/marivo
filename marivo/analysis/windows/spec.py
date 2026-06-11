from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_serializer, field_validator

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
    """Half-open time interval [start, end) — start is inclusive, end is exclusive.

    For date-only strings like ``"2026-07-31"``, the exclusive end means data
    from that date is **not** included.  To include all of July, use
    ``end="2026-08-01"``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    start: str
    end: str
    grain: Grain | None = None
    time_dimension: str | None = None

    @field_validator("grain", mode="before")
    @classmethod
    def _normalize_grain(cls, value: Any) -> Grain | None:
        return normalize_grain(value)

    @field_serializer("grain")
    def _serialize_grain(self, value: Grain | None) -> str | None:
        return value.to_token() if value is not None else None


class TimeScope(BaseModel):
    """Half-open time interval [start, end) — start is inclusive, end is exclusive."""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str


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
            f"timescope holds only start/end; pass {', '.join(misplaced)} as "
            "observe(..., grain=..., time_dimension=...) arguments, not inside timescope."
        )
    raise WindowInvalidError(
        message="timescope form is invalid",
        hint=hint,
        details={
            "kind": "TimeScopeModelInvalid",
            "timescope": dict(raw),
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
        # TimeScopeInput type so observe callers use timescope + grain/time_dimension.
        return TimeScope(start=raw.start, end=raw.end)
    if isinstance(raw, dict):
        try:
            return TimeScope.model_validate(raw)
        except ValidationError as exc:
            _raise_timescope_model_invalid(raw=raw, error=exc)
    raise WindowInvalidError(
        message=f"unsupported timescope input type {type(raw).__name__}",
        details={"kind": "TimeScopeTypeInvalid", "timescope": repr(raw)},
    )


def normalize_absolute_window_input(raw: object) -> AbsoluteWindow | None:
    if raw is None:
        return None
    if isinstance(raw, AbsoluteWindow):
        return raw
    if isinstance(raw, TimeScope):
        return AbsoluteWindow(start=raw.start, end=raw.end)
    if isinstance(raw, dict):
        try:
            return AbsoluteWindow.model_validate(raw)
        except ValidationError as exc:
            raise WindowInvalidError(
                message="absolute window form is invalid",
                details={
                    "kind": "AbsoluteWindowModelInvalid",
                    "window": dict(raw),
                    "validation_errors": exc.errors(),
                },
            ) from exc
    raise WindowInvalidError(
        message=f"unsupported absolute window input type {type(raw).__name__}",
        details={"kind": "AbsoluteWindowTypeInvalid", "window": repr(raw)},
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
            message="timescope is required when grain or time_dimension is provided",
            hint='Pass timescope={"start": "2026-07-01", "end": "2026-08-01"}.',
            details={"kind": "TimeScopeRequired"},
        )
    resolved_grain = normalize_grain(grain)
    return AbsoluteWindow(
        start=timescope.start,
        end=timescope.end,
        grain=resolved_grain,
        time_dimension=time_dimension,
    )


def dump_window(window: AbsoluteWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return window.model_dump(mode="json")
