"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

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
    """Call mv.help(CoverageFrame) for its public consumption contract."""

    meta: CoverageFrameMeta

    def _repr_identity(self) -> str:
        return f"CoverageFrame ref={self.meta.ref} parent={self.meta.parent_ref} rows={self.meta.row_count}"
