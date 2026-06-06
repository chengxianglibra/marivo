from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_serializer, field_validator

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.windows.grain import (
    Grain,
    GrainInput,
    ensure_grain_supported,
    normalize_grain,
)

# Deprecated alias: the analysis grain surface is now the structured Grain input union.
TimeGrain = GrainInput

__all__ = [
    "AbsoluteWindow",
    "Grain",
    "GrainInput",
    "TimeGrain",
    "TimeScope",
    "TimeScopeInput",
    "dump_window",
    "ensure_grain_supported",
    "make_absolute_window",
    "normalize_absolute_window_input",
    "normalize_grain",
    "normalize_timescope_input",
]


class AbsoluteWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    start: str
    end: str
    grain: Grain | None = None
    time_field: str | None = None

    @field_validator("grain", mode="before")
    @classmethod
    def _normalize_grain(cls, value: Any) -> Grain | None:
        return normalize_grain(value)

    @field_serializer("grain")
    def _serialize_grain(self, value: Grain | None) -> str | None:
        return value.to_token() if value is not None else None


class TimeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str


TimeScopeInput = TimeScope | dict[str, Any] | None


def _raise_timescope_model_invalid(
    *,
    raw: dict[str, Any],
    error: ValidationError,
) -> None:
    misplaced = [key for key in ("grain", "time_field") if key in raw]
    hint = None
    if misplaced:
        hint = (
            f"timescope holds only start/end; pass {', '.join(misplaced)} as "
            "observe(..., grain=..., time_field=...) arguments, not inside timescope."
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
        # TimeScopeInput type so observe callers use timescope + grain/time_field.
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
    time_field: str | None = None,
) -> AbsoluteWindow | None:
    if timescope is None:
        if grain is None and time_field is None:
            return None
        raise WindowInvalidError(
            message="timescope is required when grain or time_field is provided",
            hint='Pass timescope={"start": "2026-07-01", "end": "2026-07-31"}.',
            details={"kind": "TimeScopeRequired"},
        )
    resolved_grain = normalize_grain(grain)
    return AbsoluteWindow(
        start=timescope.start,
        end=timescope.end,
        grain=resolved_grain,
        time_field=time_field,
    )


def dump_window(window: AbsoluteWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return window.model_dump(mode="json")
