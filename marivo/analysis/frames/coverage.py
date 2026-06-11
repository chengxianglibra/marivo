"""CoverageFrame for sampled metric time-slot coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class CoverageFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["coverage_frame"] = "coverage_frame"
    parent_ref: str
    coverage_kind: Literal["time_slot"] = "time_slot"
    axes: dict[str, Any]
    sample_interval: str


@dataclass(repr=False)
class CoverageFrame(BaseFrame):
    meta: CoverageFrameMeta

    def _repr_identity(self) -> str:
        return f"CoverageFrame ref={self.meta.ref} parent={self.meta.parent_ref} rows={self.meta.row_count}"
