"""DeltaFrame and DeltaFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta


class DeltaFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["delta_frame"] = "delta_frame"
    metric_id: str
    source_a_ref: str
    source_b_ref: str
    alignment: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass
class DeltaFrame(BaseFrame):
    meta: DeltaFrameMeta
