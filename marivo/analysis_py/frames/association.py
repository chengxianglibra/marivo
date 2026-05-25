"""AssociationResult and AssociationResultMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta


class AssociationResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["association_result"] = "association_result"
    source_refs: list[str]
    metric_ids: list[str]
    semantic_kinds: list[Literal["scalar", "time_series", "segmented", "panel"]]
    semantic_models: list[str]
    method: Literal["pearson"]
    alignment: dict[str, Any]
    lag_policy: dict[str, Any]
    aligned_row_count: int
    dropped_row_count: int
    correlation: float


@dataclass
class AssociationResult(BaseFrame):
    meta: AssociationResultMeta
