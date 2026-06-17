"""QualityReport frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.render import format_bounded_card, result_repr


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

    def _repr_identity(self) -> str:
        return (
            f"QualityReportSummary ref={self.ref} status={self.overall_status} "
            f"blocking={self.blocking_issue_count}"
        )

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=(
                f"{self.overall_status}; blocking={self.blocking_issue_count} "
                f"warning={self.warning_count}"
            ),
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


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
