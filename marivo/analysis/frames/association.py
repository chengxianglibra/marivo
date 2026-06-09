"""AssociationResult and AssociationResultMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class AssociationResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    metric_ids: list[str]
    method: Literal["pearson"]
    correlation: float
    aligned_row_count: int
    dropped_row_count: int
    produced_by_job: str | None
    lineage_oneliner: str


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


@dataclass(repr=False)
class AssociationResult(BaseFrame):
    meta: AssociationResultMeta

    def _repr_identity(self) -> str:
        return f"AssociationResult ref={self.meta.ref} rows={self.meta.row_count}"

    def summary(self) -> AssociationResultSummary:  # type: ignore[override]
        step_intents = [step.intent for step in self.meta.lineage.steps]
        lineage_oneliner = " -> ".join(step_intents) if step_intents else "(empty)"
        return AssociationResultSummary(
            kind=self.meta.kind,
            ref=self.meta.ref,
            metric_ids=self.meta.metric_ids,
            method=self.meta.method,
            correlation=self.meta.correlation,
            aligned_row_count=self.meta.aligned_row_count,
            dropped_row_count=self.meta.dropped_row_count,
            produced_by_job=self.meta.produced_by_job,
            lineage_oneliner=lineage_oneliner,
        )
