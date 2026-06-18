"""HypothesisTestResult frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.render import format_bounded_card, result_repr


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


class HypothesisTestResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    metric_ids: list[str]
    hypothesis: Literal["mean_changed"]
    method: Literal["paired_t"]
    alpha: float
    result_shape: Literal["single", "per_segment"]
    segment_dimensions: list[str]
    rejected_count: int
    not_enough_data_count: int
    row_count: int
    produced_by_job: str | None
    lineage_oneliner: str

    def _repr_identity(self) -> str:
        return (
            f"HypothesisTestResultSummary ref={self.ref} "
            f"hypothesis={self.hypothesis} method={self.method} "
            f"rejected={self.rejected_count}"
        )

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=(
                f"alpha={self.alpha:g} shape={self.result_shape} rows={self.row_count} "
                f"not_enough_data={self.not_enough_data_count} lineage={self.lineage_oneliner}"
            ),
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


@dataclass(repr=False)
class HypothesisTestResult(BaseFrame):
    meta: HypothesisTestResultMeta

    def _repr_identity(self) -> str:
        return (
            f"HypothesisTestResult ref={self.meta.ref} "
            f"hypothesis={self.meta.hypothesis} method={self.meta.method} "
            f"rejected={self.meta.rejected_count} rows={self.meta.row_count}"
        )

    def summary(self) -> HypothesisTestResultSummary:  # type: ignore[override]  # replaces generic FrameSummary fields with hypothesis-test-specific fields
        step_intents = [step.intent for step in self.meta.lineage.steps]
        lineage_oneliner = " -> ".join(step_intents) if step_intents else "(empty)"
        return HypothesisTestResultSummary(
            kind=self.meta.kind,
            ref=self.meta.ref,
            metric_ids=self.meta.metric_ids,
            hypothesis=self.meta.hypothesis,
            method=self.meta.method,
            alpha=self.meta.alpha,
            result_shape=self.meta.result_shape,
            segment_dimensions=self.meta.segment_dimensions,
            rejected_count=self.meta.rejected_count,
            not_enough_data_count=self.meta.not_enough_data_count,
            row_count=len(self._df),
            produced_by_job=self.meta.produced_by_job,
            lineage_oneliner=lineage_oneliner,
        )
