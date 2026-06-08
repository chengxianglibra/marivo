"""QualityReport frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    status: Literal["ok", "warning", "blocking"]
    message: str


class QualityReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    target_metric_id: str | None
    target_semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    overall_status: Literal["ok", "warning", "blocking"]
    blocking_issue_count: int
    warning_count: int
    checks: list[CheckResult]
    produced_by_job: str | None
    lineage_oneliner: str


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

    def summary(self) -> QualityReportSummary:  # type: ignore[override]  # replaces meaningless FrameSummary fields with quality-specific ones
        step_intents = [step.intent for step in self.meta.lineage.steps]
        lineage_oneliner = " -> ".join(step_intents) if step_intents else "(empty)"
        checks = [
            CheckResult(
                check_id=str(row["check_id"]),
                status=cast("Literal['ok', 'warning', 'blocking']", str(row["status"])),
                message=str(row["message"]),
            )
            for row in self._df.to_dict("records")
        ]
        return QualityReportSummary(
            kind=self.meta.kind,
            ref=self.meta.ref,
            target_metric_id=self.meta.target_metric_id,
            target_semantic_kind=self.meta.target_semantic_kind,
            overall_status=self.meta.overall_status,
            blocking_issue_count=self.meta.blocking_issue_count,
            warning_count=self.meta.warning_count,
            checks=checks,
            produced_by_job=self.meta.produced_by_job,
            lineage_oneliner=lineage_oneliner,
        )

    def __repr__(self) -> str:
        m = self.meta
        target = m.target_metric_id or "?"
        header = (
            f"<{type(self).__name__} ref={m.ref} kind={m.kind} "
            f"overall={m.overall_status} blocking={m.blocking_issue_count} "
            f"warnings={m.warning_count} target={target} "
            f"({m.target_semantic_kind})>"
        )
        if len(self._df) == 0:
            return header
        lines = [header]
        for _, row in self._df.iterrows():
            status = str(row["status"])
            check_id = str(row["check_id"])
            message = str(row["message"])
            lines.append(f"  {status:<10s} {check_id:<20s} {message}")
        return "\n".join(lines)
