"""Typed analysis policies for analysis operators."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.calendar.model import AlignPeriod, CalendarFallback
from marivo.analysis.errors import (
    AlignmentPolicyValidationError,
    LagPolicyValidationError,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef

AlignmentKind = Literal[
    "window_bucket",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
]
WindowBucketMode = Literal["ordinal_bucket", "calendar_bucket"]


class AlignmentPolicy(BaseModel):
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
                details={"case": "legacy_calendar_bucket", "kind": "calendar_bucket"},
            )
        return data

    @model_validator(mode="after")
    def validate_calendar_ref(self) -> AlignmentPolicy:
        if self.kind != "window_bucket" and self.mode != "ordinal_bucket":
            raise AlignmentPolicyValidationError(
                message="calendar-backed alignment does not accept window_bucket mode",
                details={
                    "case": "window_bucket_mode_not_applicable",
                    "kind": self.kind,
                    "mode": self.mode,
                },
            )
        if self.kind != "window_bucket" and self.strict_lengths:
            raise AlignmentPolicyValidationError(
                message="calendar-backed alignment does not accept strict_lengths",
                details={
                    "case": "window_bucket_strict_lengths_not_applicable",
                    "kind": self.kind,
                },
            )
        if self.kind != "window_bucket" and self.calendar is None:
            raise AlignmentPolicyValidationError(
                message=f"alignment kind {self.kind!r} requires calendar=CalendarRef(...)",
                details={"case": "missing_calendar", "kind": self.kind},
            )
        if self.kind == "window_bucket" and self.calendar is not None:
            raise AlignmentPolicyValidationError(
                message="window_bucket does not accept calendar",
                details={"case": "unexpected_calendar", "kind": self.kind},
            )
        return self


class LagPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["single"] = "single"
    offset: int = Field(default=0)

    @model_validator(mode="after")
    def validate_supported_policy(self) -> LagPolicy:
        if self.mode != "single":
            raise LagPolicyValidationError(
                message="only LagPolicy(mode='single', offset=0) is supported",
                details={"case": "unsupported_mode", "mode": self.mode},
            )
        if self.offset != 0:
            raise LagPolicyValidationError(
                message="only zero-lag correlation is supported",
                details={"case": "nonzero_offset", "offset": self.offset},
            )
        return self


class SamplingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["bucket"] = "bucket"
    method: Literal["paired_numeric_summary"] = "paired_numeric_summary"
    pairing: Literal["window_bucket", "segment_key"] = "window_bucket"
    null_handling: Literal["drop_pair"] = "drop_pair"
    min_n: int = Field(default=3, ge=2)


class PromotionSemanticAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricRef | None = None
    subject: DimensionRef | None = None
    time_axis: DimensionRef | None = None
    source_metric: ArtifactRef | None = None
    source_delta: ArtifactRef | None = None
    current: ArtifactRef | None = None
    baseline: ArtifactRef | None = None
    axis: DimensionRef | None = None


class PromotionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auto_infer: bool = True
    semantic_anchors: PromotionSemanticAnchors = Field(default_factory=PromotionSemanticAnchors)
    required_fields: list[str] = Field(default_factory=list)
    on_missing: Literal["fail_closed"] = "fail_closed"
