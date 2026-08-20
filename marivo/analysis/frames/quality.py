"""Typed analysis quality results."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pydantic import ConfigDict, model_validator

from marivo.analysis.frames.base import (
    _DEFAULT_FRAME_PREVIEW_ROWS,
    BaseFrame,
    BaseFrameMeta,
    _display_column_names,
    _preview_cell,
)
from marivo.refs import RefPayloadV1
from marivo.render import Card


class QualityReportMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quality_report"] = "quality_report"
    source_refs: list[str]
    report_shape: Literal[
        "metric",
        "delta",
        "event_journey",
        "event_funnel",
        "event_time_to_event",
        "lifecycle_history",
        "lifecycle_distribution",
        "lifecycle_transitions",
        "lifecycle_dwell",
        "lifecycle_violations",
        "funnel_delta",
        "attribution",
        "funnel_attribution",
    ]
    target_kind: Literal[
        "metric_frame",
        "event_frame",
        "lifecycle_frame",
        "delta_frame",
        "attribution_frame",
    ]
    target_metric_id: str | None = None
    target_semantic_model: str | None = None
    target_semantic_kind: Literal[
        "scalar",
        "time_series",
        "segmented",
        "panel",
        "journey",
        "funnel",
        "time_to_event",
        "history",
        "distribution",
        "transitions",
        "dwell",
        "violations",
        "funnel_loss_rate",
    ]
    target_event_pattern_fingerprint: str | None = None
    target_state_model_ref: RefPayloadV1 | None = None
    target_state_model_fingerprint: str | None = None
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
        if self.report_shape == "funnel_delta":
            if self.target_kind != "delta_frame" or self.target_semantic_kind != "funnel":
                raise ValueError("funnel_delta quality requires DeltaFrame[funnel]")
            if not self.target_event_pattern_fingerprint:
                raise ValueError("funnel_delta quality requires a pattern fingerprint")
            return self
        if self.report_shape == "funnel_attribution":
            if (
                self.target_kind != "attribution_frame"
                or self.target_semantic_kind != "funnel_loss_rate"
            ):
                raise ValueError(
                    "funnel_attribution quality requires AttributionFrame[funnel_loss_rate]"
                )
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("funnel_attribution target step is retained in source metadata")
            return self
        if self.report_shape == "attribution":
            if self.target_kind != "attribution_frame" or self.target_semantic_kind not in {
                "scalar",
                "time_series",
                "segmented",
                "panel",
            }:
                raise ValueError("attribution quality requires a metric AttributionFrame target")
            if not self.target_metric_id or self.target_semantic_model is None:
                raise ValueError("attribution quality requires metric target identity")
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("metric attribution quality cannot carry an Event pattern")
            if self.target_coverage_basis is not None:
                raise ValueError("metric attribution quality cannot carry Event coverage")
            if (
                self.target_state_model_ref is not None
                or self.target_state_model_fingerprint is not None
            ):
                raise ValueError("metric attribution quality cannot carry a StateModel")
            return self
        if self.report_shape == "metric":
            if self.target_kind != "metric_frame" or self.target_semantic_kind not in {
                "scalar",
                "time_series",
                "segmented",
                "panel",
            }:
                raise ValueError("metric quality reports require a MetricFrame target")
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("metric quality reports cannot carry an Event pattern")
            if self.target_coverage_basis is not None:
                raise ValueError("metric quality reports cannot carry Event coverage")
            if (
                self.target_state_model_ref is not None
                or self.target_state_model_fingerprint is not None
            ):
                raise ValueError("metric quality reports cannot carry a StateModel")
            return self
        if self.report_shape == "delta":
            if self.target_kind != "delta_frame" or self.target_semantic_kind not in {
                "scalar",
                "time_series",
                "segmented",
                "panel",
            }:
                raise ValueError("delta quality reports require a metric DeltaFrame target")
            if not self.target_metric_id or self.target_semantic_model is None:
                raise ValueError("delta quality reports require metric target identity")
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("metric delta quality reports cannot carry an Event pattern")
            if self.target_coverage_basis is not None:
                raise ValueError("metric delta quality reports cannot carry Event coverage")
            if (
                self.target_state_model_ref is not None
                or self.target_state_model_fingerprint is not None
            ):
                raise ValueError("metric delta quality reports cannot carry a StateModel")
            return self
        if self.report_shape.startswith("lifecycle_"):
            expected_semantic_kind = self.report_shape.removeprefix("lifecycle_")
            if (
                self.target_kind != "lifecycle_frame"
                or self.target_semantic_kind != expected_semantic_kind
            ):
                raise ValueError(
                    f"{self.report_shape} quality reports require a "
                    f"LifecycleFrame[{expected_semantic_kind}] target"
                )
            if self.target_state_model_ref is None or not self.target_state_model_fingerprint:
                raise ValueError("Lifecycle quality reports require exact StateModel identity")
            if self.target_event_pattern_fingerprint is not None:
                raise ValueError("Lifecycle quality reports cannot carry an Event pattern")
            if self.target_metric_id is not None or self.target_semantic_model is not None:
                raise ValueError("Lifecycle quality reports cannot carry metric target fields")
            if (expected_semantic_kind == "history") != (self.target_coverage_basis is not None):
                raise ValueError("Lifecycle coverage basis is retained only for history quality")
            return self
        expected_semantic_kind = self.report_shape.removeprefix("event_")
        if self.target_kind != "event_frame" or self.target_semantic_kind != expected_semantic_kind:
            raise ValueError(
                f"{self.report_shape} quality reports require an "
                f"EventFrame[{expected_semantic_kind}] target"
            )
        if not self.target_event_pattern_fingerprint:
            raise ValueError("Event quality reports require a pattern fingerprint")
        if self.target_coverage_basis is None:
            raise ValueError("Event quality reports require a coverage basis")
        if self.target_metric_id is not None or self.target_semantic_model is not None:
            raise ValueError("Event quality reports cannot carry metric target fields")
        if (
            self.target_state_model_ref is not None
            or self.target_state_model_fingerprint is not None
        ):
            raise ValueError("Event quality reports cannot carry a StateModel")
        return self


@dataclass(repr=False)
class QualityReport(BaseFrame):
    """Call marivo.help(QualityReport) for its public consumption contract."""

    meta: QualityReportMeta

    @property
    def overall_status(self) -> Literal["ok", "warning", "blocking"]:
        """Return the report's authoritative mechanical quality verdict."""
        return self.meta.overall_status

    @property
    def blocking_issue_count(self) -> int:
        """Return the number of blocking checks in this report."""
        return self.meta.blocking_issue_count

    @property
    def warning_count(self) -> int:
        """Return the number of warning checks in this report."""
        return self.meta.warning_count

    def _repr_identity(self) -> str:
        return (
            f"QualityReport ref={self.meta.ref} status={self.meta.overall_status} "
            f"blocking={self.meta.blocking_issue_count} rows={self.meta.row_count}"
        )

    def _attention_dataframe(self) -> pd.DataFrame:
        """Return warning/blocking checks in decision order, preserving source order."""
        attention = self._df.loc[self._df["severity"].isin(("blocking", "warning"))].copy()
        if attention.empty:
            return attention
        attention["_attention_rank"] = attention["severity"].map({"blocking": 0, "warning": 1})
        return attention.sort_values("_attention_rank", kind="stable").drop(
            columns=["_attention_rank"]
        )

    def _attention_rows_provider(self) -> Iterator[tuple[str, ...]]:
        columns = self._public_column_names()
        for row in self._attention_dataframe().itertuples(index=False, name=None):
            yield tuple(str(_preview_cell(value)) for value in row[: len(columns)])

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        total = len(self._df)
        ok_count = total - self.meta.blocking_issue_count - self.meta.warning_count
        status_parts = [
            f"status={self.meta.overall_status}",
            f"checks={total}",
            f"ok={ok_count}",
            f"blocking={self.meta.blocking_issue_count}",
            f"warning={self.meta.warning_count}",
        ]
        evidence = self._evidence_status_token()
        if evidence is not None:
            status_parts.append(evidence)
        card = self._header_card().status(" ".join(status_parts))
        card.field(
            "target",
            (
                f"kind={self.meta.target_kind} shape={self.meta.target_semantic_kind} "
                f"sources={','.join(self.meta.source_refs)}"
            ),
        )
        self._append_evidence_sections(
            card,
            include_digest_items=False,
            include_quality_issues=False,
        )
        attention_count = self.meta.blocking_issue_count + self.meta.warning_count
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=total,
            label="checks",
            bounded_rows_provider=self._attention_rows_provider,
            bounded_row_count=attention_count,
            bounded_label="attention",
            show_omission_counts=True,
            bounded_row_limit=_DEFAULT_FRAME_PREVIEW_ROWS,
            recovery=f"session.get_frame('{self.meta.ref}').to_pandas()",
        )
