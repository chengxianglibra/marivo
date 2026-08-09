"""Typed analysis alignment and sampling policies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from marivo._temporal import Grain, builtin_grain
from marivo.analysis.errors import AlignmentPolicyValidationError

AlignmentKind = Literal[
    "window_bucket",
    "period_progress",
    "period_correspondence",
    "day_of_week",
    "occurrence_progress",
]
WindowBucketMode = Literal["ordinal_bucket", "calendar_bucket"]
UnmatchedMode = Literal["fail", "drop"]
_DEFAULT_WITHIN = builtin_grain("month")


def _invalid_policy(
    *,
    helper: str,
    received: object,
    reason: str,
    fields: tuple[str, ...] = (),
) -> AlignmentPolicyValidationError:
    return AlignmentPolicyValidationError(
        message=f"{helper} received an invalid alignment policy: {reason}",
        context={
            "case": "invalid_helper_arguments",
            "helper": helper,
            "received": repr(received),
            "accepted_fields": fields,
        },
    )


class AlignmentPolicy(BaseModel):
    """Closed public alignment protocol; construct values with one helper."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AlignmentKind

    def __new__(cls, *args: Any, **kwargs: Any) -> AlignmentPolicy:
        if cls is AlignmentPolicy:
            raise AlignmentPolicyValidationError(
                message="AlignmentPolicy is a protocol and cannot be constructed directly",
                context={
                    "case": "direct_constructor",
                    "received": sorted(str(key) for key in kwargs),
                },
            )
        return super().__new__(cls)

    def __repr__(self) -> str:
        fields = self.model_dump(mode="json")
        values = ", ".join(f"{key}={value!r}" for key, value in fields.items())
        return f"AlignmentPolicy({values})"


class _WindowBucketPolicy(AlignmentPolicy):
    kind: Literal["window_bucket"] = "window_bucket"
    mode: WindowBucketMode = "ordinal_bucket"
    strict_lengths: bool = False


class _DayOfWeekPolicy(AlignmentPolicy):
    kind: Literal["day_of_week"] = "day_of_week"
    within: Grain = _DEFAULT_WITHIN
    unmatched: UnmatchedMode = "fail"


class _PeriodProgressPolicy(AlignmentPolicy):
    kind: Literal["period_progress"] = "period_progress"
    unmatched: UnmatchedMode = "fail"


class _PeriodCorrespondencePolicy(AlignmentPolicy):
    kind: Literal["period_correspondence"] = "period_correspondence"
    correspondence: str
    unmatched: UnmatchedMode = "fail"


class _OccurrenceProgressPolicy(AlignmentPolicy):
    kind: Literal["occurrence_progress"] = "occurrence_progress"
    anchor: Literal["start", "end"] = "start"
    unmatched: UnmatchedMode = "fail"


def window_bucket(
    *,
    mode: WindowBucketMode = "ordinal_bucket",
    strict_lengths: bool = False,
) -> AlignmentPolicy:
    """Construct a request-window bucket alignment policy.

    Args:
        mode: ``ordinal_bucket`` pairs positions within each selected window;
            ``calendar_bucket`` pairs identical resolved bucket keys.
        strict_lengths: Reject ordinal windows whose expected bucket counts differ.

    Returns:
        A frozen ``AlignmentPolicy`` tagged ``window_bucket``.

    Example:
        ``session.compare(current, baseline, alignment=mv.window_bucket())``.

    Constraints:
        This helper accepts no calendar or period authority.
    """
    if mode not in {"ordinal_bucket", "calendar_bucket"}:
        raise _invalid_policy(
            helper="mv.window_bucket",
            received=mode,
            reason="mode must be 'ordinal_bucket' or 'calendar_bucket'",
            fields=("mode", "strict_lengths"),
        )
    if type(strict_lengths) is not bool:
        raise _invalid_policy(
            helper="mv.window_bucket",
            received=strict_lengths,
            reason="strict_lengths must be a bool",
            fields=("mode", "strict_lengths"),
        )
    return _WindowBucketPolicy(mode=mode, strict_lengths=strict_lengths)


def day_of_week(
    *,
    within: Grain = _DEFAULT_WITHIN,
    unmatched: UnmatchedMode = "fail",
) -> AlignmentPolicy:
    """Construct same-weekday-occurrence alignment inside one target period.

    Args:
        within: Built-in or certified semantic containing-period grain.
        unmatched: Whether absent coordinates fail or are dropped and counted.

    Returns:
        A frozen ``AlignmentPolicy`` tagged ``day_of_week``.

    Example:
        ``session.compare(current, baseline, alignment=mv.day_of_week())``.

    Constraints:
        Inputs must be one row per local day in exactly one containing period.
    """
    if type(within) is not Grain:
        raise _invalid_policy(
            helper="mv.day_of_week",
            received=within,
            reason="within must be a built-in or semantic Grain",
            fields=("within", "unmatched"),
        )
    if unmatched not in {"fail", "drop"}:
        raise _invalid_policy(
            helper="mv.day_of_week",
            received=unmatched,
            reason="unmatched must be 'fail' or 'drop'",
            fields=("within", "unmatched"),
        )
    return _DayOfWeekPolicy(within=within, unmatched=unmatched)


def period_progress(*, unmatched: UnmatchedMode = "fail") -> AlignmentPolicy:
    """Construct same-progress alignment inside one certified target period.

    Args:
        unmatched: Whether absent progress coordinates fail or are dropped.

    Returns:
        A frozen ``AlignmentPolicy`` tagged ``period_progress``.

    Example:
        ``session.compare(current, baseline, alignment=mv.period_progress())``.

    Constraints:
        Each side must resolve to exactly one target period under the same authority.
    """
    if unmatched not in {"fail", "drop"}:
        raise _invalid_policy(
            helper="mv.period_progress",
            received=unmatched,
            reason="unmatched must be 'fail' or 'drop'",
            fields=("unmatched",),
        )
    return _PeriodProgressPolicy(unmatched=unmatched)


def period_correspondence(
    *,
    correspondence: str,
    unmatched: UnmatchedMode = "fail",
) -> AlignmentPolicy:
    """Construct alignment through one certified named period correspondence.

    Args:
        correspondence: Authored mapping name in the current semantic calendar.
        unmatched: Whether absent mapped periods fail or are dropped.

    Returns:
        A frozen ``AlignmentPolicy`` tagged ``period_correspondence``.

    Example:
        ``session.compare(current, baseline, alignment=mv.period_correspondence(correspondence='prior_year_shifted'))``.

    Constraints:
        Both frames must be complete at the exact correspondence level.
    """
    if type(correspondence) is not str or not correspondence.strip():
        raise _invalid_policy(
            helper="mv.period_correspondence",
            received=correspondence,
            reason="correspondence must be a non-empty name",
            fields=("correspondence", "unmatched"),
        )
    if unmatched not in {"fail", "drop"}:
        raise _invalid_policy(
            helper="mv.period_correspondence",
            received=unmatched,
            reason="unmatched must be 'fail' or 'drop'",
            fields=("correspondence", "unmatched"),
        )
    return _PeriodCorrespondencePolicy(
        correspondence=correspondence.strip(),
        unmatched=unmatched,
    )


def occurrence_progress(
    *,
    anchor: Literal["start", "end"] = "start",
    unmatched: UnmatchedMode = "fail",
) -> AlignmentPolicy:
    """Construct relative-local-day alignment inside two exact occurrences.

    Args:
        anchor: Count local-day ordinals forward from occurrence start or
            backward from its exclusive end.
        unmatched: Whether missing ordinals fail or are dropped and counted.

    Returns:
        A frozen ``AlignmentPolicy`` tagged ``occurrence_progress``.

    Example:
        ``session.compare(current, baseline, alignment=mv.occurrence_progress())``.

    Constraints:
        Both frames must be day-grain time-series or panel frames selected by
        exact temporal-occurrence scopes.
    """
    if type(anchor) is not str or anchor not in {"start", "end"}:
        raise _invalid_policy(
            helper="mv.occurrence_progress",
            received=anchor,
            reason="anchor must be 'start' or 'end'",
            fields=("anchor", "unmatched"),
        )
    if type(unmatched) is not str or unmatched not in {"fail", "drop"}:
        raise _invalid_policy(
            helper="mv.occurrence_progress",
            received=unmatched,
            reason="unmatched must be 'fail' or 'drop'",
            fields=("anchor", "unmatched"),
        )
    return _OccurrenceProgressPolicy(anchor=anchor, unmatched=unmatched)


def decode_alignment_policy(payload: Mapping[str, object]) -> AlignmentPolicy:
    """Decode a persisted policy; this is intentionally not a public input path."""
    if type(payload) is not dict:
        raise _invalid_policy(
            helper="alignment recovery",
            received=payload,
            reason="persisted policy must be an object",
        )
    kind = payload.get("kind")
    variants: dict[str, type[AlignmentPolicy]] = {
        "window_bucket": _WindowBucketPolicy,
        "day_of_week": _DayOfWeekPolicy,
        "period_progress": _PeriodProgressPolicy,
        "period_correspondence": _PeriodCorrespondencePolicy,
        "occurrence_progress": _OccurrenceProgressPolicy,
    }
    variant = variants.get(cast("str", kind))
    if variant is None:
        raise _invalid_policy(
            helper="alignment recovery",
            received=kind,
            reason="unknown alignment kind",
        )
    try:
        return variant.model_validate(payload)
    except Exception as exc:
        raise _invalid_policy(
            helper="alignment recovery",
            received=payload,
            reason=str(exc),
        ) from exc


class SamplingPolicy(BaseModel):
    """Call marivo.help(SamplingPolicy) for its public consumption contract.

    Immutable policy controlling paired-sample extraction for compare,
    correlate, and hypothesis_test.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["bucket"] = "bucket"
    method: Literal["paired_numeric_summary"] = "paired_numeric_summary"
    pairing: Literal["window_bucket", "segment_key"] = "window_bucket"
    null_handling: Literal["drop_pair"] = "drop_pair"
    min_n: int = Field(default=3, ge=2)
