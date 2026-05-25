"""Typed analysis policies for analysis_py operators."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis_py.calendar.model import AlignPeriod, CalendarFallback
from marivo.analysis_py.refs import CalendarRef

AlignmentKind = Literal[
    "calendar_bucket",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
]


class AlignmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AlignmentKind = "calendar_bucket"
    calendar: CalendarRef | None = None
    period: AlignPeriod = "month"
    fallback: CalendarFallback = "drop"

    @model_validator(mode="after")
    def validate_calendar_ref(self) -> AlignmentPolicy:
        if self.kind != "calendar_bucket" and self.calendar is None:
            raise ValueError(f"alignment kind {self.kind!r} requires calendar=CalendarRef(...)")
        if self.kind == "calendar_bucket" and self.calendar is not None:
            raise ValueError("calendar_bucket does not accept calendar")
        return self


class LagPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["single"] = "single"
    offset: int = Field(default=0)

    @model_validator(mode="after")
    def validate_supported_policy(self) -> LagPolicy:
        if self.mode != "single":
            raise ValueError("only LagPolicy(mode='single', offset=0) is supported")
        if self.offset != 0:
            raise ValueError("only zero-lag correlation is supported")
        return self
