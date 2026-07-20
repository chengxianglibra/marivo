"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.calendar.model import AlignPeriod, CalendarFallback
from marivo.analysis.errors import (
    AlignmentPolicyValidationError,
    SemanticKindMismatchError,
)
from marivo.analysis.refs import CalendarRef
from marivo.refs import Ref, SemanticKind, SemanticKindTag

AlignmentKind = Literal[
    "window_bucket",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
]
WindowBucketMode = Literal["ordinal_bucket", "calendar_bucket"]
_DIMENSION_ANCHOR_FIELDS = {"subject", "time_axis", "axis"}


def _reject_anchor_kind(*, field_name: str, value: object, actual_kind: object) -> None:
    expected = (
        "metric"
        if field_name == "metric"
        else "dimension or time_dimension"
        if field_name in _DIMENSION_ANCHOR_FIELDS
        else "semantic"
    )
    raise ValueError(
        f"{field_name} expected {expected} ref, got {type(value).__name__} kind={actual_kind}"
    )


def _validate_anchor_kind(value: object, *, field_name: str, kind: SemanticKind | None) -> None:
    if field_name == "metric":
        if kind == SemanticKind.MEASURE:
            raise SemanticKindMismatchError(
                message=(
                    f"{field_name} cannot be a measure; measures are aggregated values, "
                    "not analysis anchors. Use a metric, entity, categorical dimension, or time dimension."
                ),
                context={"field": field_name, "actual_kind": "measure"},
            )
        if kind != SemanticKind.METRIC:
            _reject_anchor_kind(field_name=field_name, value=value, actual_kind=kind)
        return
    if field_name in _DIMENSION_ANCHOR_FIELDS:
        if kind == SemanticKind.MEASURE:
            raise SemanticKindMismatchError(
                message=(
                    f"{field_name} cannot be a measure; measures are aggregated values, "
                    "not analysis anchors. Use a metric, entity, categorical dimension, or time dimension."
                ),
                context={"field": field_name, "actual_kind": "measure"},
            )
        if kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            _reject_anchor_kind(field_name=field_name, value=value, actual_kind=kind)


def _semantic_anchor_id(
    value: Ref[SemanticKindTag] | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if type(value) is Ref:
        _validate_anchor_kind(value, field_name=field_name, kind=value.kind)
        return value.path
    raise ValueError(
        f"expected exact Ref; got {type(value).__name__}. Pass entry.ref or ms.Ref.<kind>(path)."
    )


class AlignmentPolicy(BaseModel):
    """Call mv.help(AlignmentPolicy) for its public consumption contract.

    Immutable policy governing how two observation windows are aligned
    before comparison, correlation, or hypothesis testing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AlignmentKind = "window_bucket"
    calendar: CalendarRef | None = None
    period: AlignPeriod = "month"
    fallback: CalendarFallback = "drop"
    mode: WindowBucketMode = "ordinal_bucket"
    strict_lengths: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_calendar_bucket(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("kind") == "calendar_bucket":
            raise AlignmentPolicyValidationError(
                message="alignment kind 'calendar_bucket' was renamed to 'window_bucket'",
                context={"case": "legacy_calendar_bucket", "kind": "calendar_bucket"},
            )
        return data

    @model_validator(mode="after")
    def validate_calendar_ref(self) -> AlignmentPolicy:
        if self.kind != "window_bucket" and self.mode != "ordinal_bucket":
            raise AlignmentPolicyValidationError(
                message="calendar-backed alignment does not accept window_bucket mode",
                context={
                    "case": "window_bucket_mode_not_applicable",
                    "kind": self.kind,
                    "mode": self.mode,
                },
            )
        if self.kind != "window_bucket" and self.strict_lengths:
            raise AlignmentPolicyValidationError(
                message="calendar-backed alignment does not accept strict_lengths",
                context={
                    "case": "window_bucket_strict_lengths_not_applicable",
                    "kind": self.kind,
                },
            )
        if self.kind != "window_bucket" and self.calendar is None:
            raise AlignmentPolicyValidationError(
                message=f"alignment kind {self.kind!r} requires calendar=CalendarRef(...)",
                context={"case": "missing_calendar", "kind": self.kind},
            )
        if self.kind == "window_bucket" and self.calendar is not None:
            raise AlignmentPolicyValidationError(
                message="window_bucket does not accept calendar",
                context={"case": "unexpected_calendar", "kind": self.kind},
            )
        return self


def window_bucket(
    *,
    mode: WindowBucketMode = "ordinal_bucket",
    strict_lengths: bool = False,
) -> AlignmentPolicy:
    """Construct a window-bucket alignment policy.

    Args:
        mode: Bucket pairing mode. ``"ordinal_bucket"`` pairs buckets by
            position within each input window; ``"calendar_bucket"`` joins by
            absolute bucket key.
        strict_lengths: When ``True``, ordinal window-bucket alignment rejects
            unequal expected bucket counts.

    Returns:
        An ``AlignmentPolicy`` with ``kind="window_bucket"``.

    Example:
        ``session.compare(cur, base, alignment=mv.window_bucket())``.

    Constraints:
        ``window_bucket`` alignment does not accept a calendar argument.
    """
    return AlignmentPolicy(
        kind="window_bucket",
        mode=mode,
        strict_lengths=strict_lengths,
    )


def dow_aligned(
    *,
    calendar: CalendarRef,
    period: AlignPeriod = "month",
    fallback: CalendarFallback = "drop",
) -> AlignmentPolicy:
    """Construct a day-of-week calendar alignment policy.

    Args:
        calendar: Calendar provider ref used to derive aligned periods.
        period: Calendar period used when deriving alignment keys.
        fallback: Fallback behavior for unmatched calendar rows.

    Returns:
        An ``AlignmentPolicy`` with ``kind="dow_aligned"``.

    Example:
        ``mv.dow_aligned(calendar=mv.CalendarRef("cn_holidays"))``.

    Constraints:
        ``calendar`` must be a ``CalendarRef``; use ``mv.CalendarRef(...)`` for
        provider ids.
    """
    return AlignmentPolicy(
        kind="dow_aligned",
        calendar=calendar,
        period=period,
        fallback=fallback,
    )


def holiday_aligned(
    *,
    calendar: CalendarRef,
    period: AlignPeriod = "month",
    fallback: CalendarFallback = "drop",
) -> AlignmentPolicy:
    """Construct a holiday calendar alignment policy.

    Args:
        calendar: Calendar provider ref used to derive holiday alignment keys.
        period: Calendar period used when deriving alignment keys.
        fallback: Fallback behavior for unmatched calendar rows.

    Returns:
        An ``AlignmentPolicy`` with ``kind="holiday_aligned"``.

    Example:
        ``mv.holiday_aligned(calendar=mv.CalendarRef("cn_holidays"))``.

    Constraints:
        ``calendar`` must be a ``CalendarRef``; use ``mv.CalendarRef(...)`` for
        provider ids.
    """
    return AlignmentPolicy(
        kind="holiday_aligned",
        calendar=calendar,
        period=period,
        fallback=fallback,
    )


def holiday_and_dow_aligned(
    *,
    calendar: CalendarRef,
    period: AlignPeriod = "month",
    fallback: CalendarFallback = "drop",
) -> AlignmentPolicy:
    """Construct a holiday-then-day-of-week calendar alignment policy.

    Args:
        calendar: Calendar provider ref used to derive holiday and day-of-week
            alignment keys.
        period: Calendar period used when deriving alignment keys.
        fallback: Fallback behavior for unmatched calendar rows.

    Returns:
        An ``AlignmentPolicy`` with ``kind="holiday_and_dow_aligned"``.

    Example:
        ``mv.holiday_and_dow_aligned(calendar=mv.CalendarRef("cn_holidays"))``.

    Constraints:
        ``calendar`` must be a ``CalendarRef``; use ``mv.CalendarRef(...)`` for
        provider ids.
    """
    return AlignmentPolicy(
        kind="holiday_and_dow_aligned",
        calendar=calendar,
        period=period,
        fallback=fallback,
    )


class SamplingPolicy(BaseModel):
    """Call mv.help(SamplingPolicy) for its public consumption contract.

    Immutable policy controlling paired-sample extraction for compare,
    correlate, and hypothesis_test.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["bucket"] = "bucket"
    method: Literal["paired_numeric_summary"] = "paired_numeric_summary"
    pairing: Literal["window_bucket", "segment_key"] = "window_bucket"
    null_handling: Literal["drop_pair"] = "drop_pair"
    min_n: int = Field(default=3, ge=2)
