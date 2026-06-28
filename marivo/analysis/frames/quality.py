"""QualityReport frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.frames.render import format_bounded_card


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


@dataclass(repr=False)
class QualityReport(BaseFrame):
    meta: QualityReportMeta

    def _repr_identity(self) -> str:
        return (
            f"QualityReport ref={self.meta.ref} status={self.meta.overall_status} "
            f"blocking={self.meta.blocking_issue_count} rows={self.meta.row_count}"
        )

    def render(self) -> str:
        columns, preview_rows = self._preview_rows(limit=5)
        return format_bounded_card(
            identity=self._repr_identity(),
            status=(
                f"status={self.meta.overall_status} "
                f"blocking={self.meta.blocking_issue_count} "
                f"warning={self.meta.warning_count}"
            ),
            columns=columns,
            rows=preview_rows,
            row_count=len(self._df),
            preview_truncation_hint="call .to_pandas() for terminal custom analysis",
            available=self._AVAILABLE_ENTRIES,
        )
