"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, model_validator

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, _display_column_names
from marivo.render import Card


class QualityReportMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quality_report"] = "quality_report"
    source_refs: list[str]
    report_shape: Literal["metric", "event_journey"]
    target_kind: Literal["metric_frame", "event_frame"]
    target_metric_id: str | None = None
    target_semantic_model: str | None = None
    target_semantic_kind: Literal[
        "scalar",
        "time_series",
        "segmented",
        "panel",
        "journey",
    ]
    target_event_pattern_fingerprint: str | None = None
    target_coverage_basis: (
        Literal[
            "observed_watermark",
            "declared_complete",
            "mixed",
            "unknown",
        ]
        | None
    ) = None
    checks_run: list[str]
    overall_status: Literal["ok", "warning", "blocking"]
    blocking_issue_count: int
    warning_count: int

    @model_validator(mode="after")
    def _validate_target_shape(self) -> QualityReportMeta:
        if self.report_shape == "metric":
            if self.target_kind != "metric_frame" or self.target_semantic_kind == "journey":
                raise ValueError("metric quality reports require a MetricFrame target")
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("metric quality reports cannot carry an Event pattern")
            if self.target_coverage_basis is not None:
                raise ValueError("metric quality reports cannot carry Event coverage")
            return self
        if self.target_kind != "event_frame" or self.target_semantic_kind != "journey":
            raise ValueError("event_journey quality reports require an EventFrame[journey] target")
        if not self.target_event_pattern_fingerprint:
            raise ValueError("event_journey quality reports require a pattern fingerprint")
        if self.target_coverage_basis is None:
            raise ValueError("event_journey quality reports require a coverage basis")
        if self.target_metric_id is not None or self.target_semantic_model is not None:
            raise ValueError("event_journey quality reports cannot carry metric target fields")
        return self


@dataclass(repr=False)
class QualityReport(BaseFrame):
    """Call mv.help(QualityReport) for its public consumption contract."""

    meta: QualityReportMeta

    def _repr_identity(self) -> str:
        return (
            f"QualityReport ref={self.meta.ref} status={self.meta.overall_status} "
            f"blocking={self.meta.blocking_issue_count} rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        status_parts = [
            f"status={self.meta.overall_status}",
            f"blocking={self.meta.blocking_issue_count}",
            f"warning={self.meta.warning_count}",
        ]
        evidence = self._evidence_status_token()
        if evidence is not None:
            status_parts.append(evidence)
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES).status(
            " ".join(status_parts)
        )
        self._append_evidence_sections(card)
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )
