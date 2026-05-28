from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from marivo.analysis.errors import WindowInvalidError

TimeGrain = Literal["hour", "day", "week", "month", "quarter", "year"]


class AbsoluteWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    start: str
    end: str
    grain: TimeGrain | None = None
    tz: str | None = None
    time_field: str | None = None


class RelativeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["relative"] = "relative"
    expr: str
    as_of: str | None = None
    grain: TimeGrain | None = None
    tz: str | None = None
    time_field: str | None = None


WindowSpec = Annotated[AbsoluteWindow | RelativeWindow, Field(discriminator="kind")]
WindowInput = AbsoluteWindow | RelativeWindow | dict[str, Any] | str | None


def _raise_window_model_invalid(
    *,
    model_kind: str,
    raw: dict[str, Any],
    error: ValidationError,
) -> None:
    raise WindowInvalidError(
        message=f"window {model_kind} form is invalid",
        details={
            "kind": "WindowModelInvalid",
            "model": model_kind,
            "window": dict(raw),
            "validation_errors": error.errors(),
        },
    ) from error


def normalize_window_input(raw: object) -> AbsoluteWindow | RelativeWindow | None:
    if raw is None:
        return None
    if isinstance(raw, (AbsoluteWindow, RelativeWindow)):
        return raw
    if isinstance(raw, str):
        return RelativeWindow(expr=raw)
    if isinstance(raw, dict):
        has_expr = "expr" in raw
        has_boundary = "start" in raw or "end" in raw
        if has_expr and has_boundary:
            raise WindowInvalidError(
                message="window cannot mix relative expr with absolute start/end",
                details={"kind": "MixedWindowForm", "window": dict(raw)},
            )
        if has_expr:
            try:
                return RelativeWindow.model_validate(raw)
            except ValidationError as exc:
                _raise_window_model_invalid(model_kind="relative", raw=raw, error=exc)
        if has_boundary:
            try:
                return AbsoluteWindow.model_validate(raw)
            except ValidationError as exc:
                _raise_window_model_invalid(model_kind="absolute", raw=raw, error=exc)
    raise WindowInvalidError(
        message=f"unsupported window input type {type(raw).__name__}",
        details={"kind": "WindowTypeInvalid", "window": repr(raw)},
    )


def dump_window(window: AbsoluteWindow | RelativeWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return window.model_dump(mode="json")
