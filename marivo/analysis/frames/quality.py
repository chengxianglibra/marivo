"""QualityReport frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class QualityReportMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quality_report"] = "quality_report"
    source_refs: list[str]
    report_shape: Literal["metric"]
    target_kind: Literal["metric_frame"]
    target_metric_id: str | None
    target_semantic_model: str | None
    target_semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    checks_run: list[str]
    overall_status: Literal["ok", "warning", "blocking"]
    blocking_issue_count: int
    warning_count: int


@dataclass
class QualityReport(BaseFrame):
    meta: QualityReportMeta
