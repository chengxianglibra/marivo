"""AttributionFrame and AttributionFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta


class AttributionFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["attribution_frame"] = "attribution_frame"
    metric_ids: list[str]
    source_refs: list[str]
    attribution_kind: Literal["decomposition", "correlation", "anomaly"]
    driver_field: str | None
    value_column: str | None
    contribution_column: str | None
    method: str
    params: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass
class AttributionFrame(BaseFrame):
    meta: AttributionFrameMeta
