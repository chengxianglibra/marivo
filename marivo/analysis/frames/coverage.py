"""CoverageFrame for metric coverage.

Two coverage kinds exist and never share one summary payload:

- ``time_slot``: sampled semi-additive (time_fold) coverage. Each bucket counts
  actual sample points vs the expected count derived from the sample interval.
  ``sample_interval`` carries that interval (e.g. ``"5minute"``).
- ``window_coverage``: trailing (rolling N) cumulative coverage. Each bucket's
  expected window span is ``span_seconds``; the covered span is clipped by the
  data start, so partial buckets (whose window reaches before the first event)
  carry a real fractional ``coverage_ratio``. ``sample_interval`` is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class CoverageFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["coverage_frame"] = "coverage_frame"
    parent_ref: str
    coverage_kind: Literal["time_slot", "window_coverage"] = "time_slot"
    axes: dict[str, Any]
    sample_interval: str | None = None


@dataclass(repr=False)
class CoverageFrame(BaseFrame):
    meta: CoverageFrameMeta

    def _repr_identity(self) -> str:
        return f"CoverageFrame ref={self.meta.ref} parent={self.meta.parent_ref} rows={self.meta.row_count}"
