"""HypothesisTestResult frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class HypothesisTestResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["hypothesis_test_result"] = "hypothesis_test_result"
    source_refs: list[str]
    metric_ids: list[str]
    semantic_kinds: list[Literal["scalar", "time_series", "segmented", "panel"]]
    semantic_models: list[str]
    hypothesis: Literal["mean_changed"]
    method: Literal["paired_t"]
    alignment: dict[str, Any]
    sampling: dict[str, Any]
    alpha: float
    result_shape: Literal["single", "per_segment"]
    segment_dimensions: list[str]
    rejected_count: int
    not_enough_data_count: int


@dataclass
class HypothesisTestResult(BaseFrame):
    meta: HypothesisTestResultMeta
