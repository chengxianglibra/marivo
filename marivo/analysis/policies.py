"""Typed analysis policies for analysis operators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from marivo.analysis.calendar.model import AlignPeriod, CalendarFallback
from marivo.analysis.errors import (
    AlignmentPolicyValidationError,
    SemanticKindMismatchError,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef
from marivo.semantic.catalog import SemanticKind, SemanticObject, SemanticRef

AlignmentKind = Literal[
    "window_bucket",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
]
WindowBucketMode = Literal["ordinal_bucket", "calendar_bucket"]
SemanticAnchorInput = str | SemanticRef | SemanticObject


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
                details={"field": field_name, "actual_kind": "measure"},
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
                details={"field": field_name, "actual_kind": "measure"},
            )
        if kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            _reject_anchor_kind(field_name=field_name, value=value, actual_kind=kind)


def _semantic_anchor_id(value: SemanticAnchorInput | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, SemanticObject):
        _validate_anchor_kind(value, field_name=field_name, kind=value.kind)
        return value.ref.id
    if isinstance(value, SemanticRef):
        _validate_anchor_kind(value, field_name=field_name, kind=value.kind)
        return value.id
    if isinstance(value, Mapping):
        raise ValueError(
            f"expected str, SemanticRef, or SemanticObject, got {type(value).__name__}"
        )
    if isinstance(value, str):
        return value
    raise ValueError(f"expected str, SemanticRef, or SemanticObject, got {type(value).__name__}")


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


class SamplingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["bucket"] = "bucket"
    method: Literal["paired_numeric_summary"] = "paired_numeric_summary"
    pairing: Literal["window_bucket", "segment_key"] = "window_bucket"
    null_handling: Literal["drop_pair"] = "drop_pair"
    min_n: int = Field(default=3, ge=2)


class PromotionSemanticAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str | None = None
    subject: str | None = None
    time_axis: str | None = None
    source_metric: ArtifactRef | None = None
    source_delta: ArtifactRef | None = None
    current: ArtifactRef | None = None
    baseline: ArtifactRef | None = None
    axis: str | None = None

    @field_validator("metric", "subject", "time_axis", "axis", mode="before")
    @classmethod
    def normalize_semantic_anchor(
        cls, value: SemanticAnchorInput | None, info: ValidationInfo
    ) -> str | None:
        field_name = info.field_name
        if field_name is None:
            raise ValueError("semantic anchor field name is required")
        return _semantic_anchor_id(value, field_name=field_name)


class PromotionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_anchors: PromotionSemanticAnchors = Field(default_factory=PromotionSemanticAnchors)
    required_fields: list[str] = Field(default_factory=list)
    on_missing: Literal["fail_closed"] = "fail_closed"
