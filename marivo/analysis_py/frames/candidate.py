"""CandidateSet and CandidateSetMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta


class CandidateSetMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_set"] = "candidate_set"
    source_ref: str
    objective: Literal["point_anomalies"]
    strategy: Literal["zscore"]
    metric_ids: list[str]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    params: dict[str, Any]


@dataclass
class CandidateSet(BaseFrame):
    meta: CandidateSetMeta
