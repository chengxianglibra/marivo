"""CandidateSet and CandidateSetMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, Field

from marivo.analysis.followups import (
    BlockingIssue,
    FollowupAction,
)
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta

CandidateShape = Literal[
    "point_anomaly",
    "period_shift",
    "driver_axis",
    "slice",
    "window",
    "cross_sectional_outlier",
]
CandidateObjective = Literal[
    "point_anomalies",
    "period_shifts",
    "driver_axes",
    "interesting_slices",
    "interesting_windows",
    "cross_sectional_outliers",
]
CandidateStrategy = Literal[
    "zscore",
    "delta_window_zscore",
    "variance_explained",
    "delta_magnitude",
    "rolling_zscore",
    "mad",
]
CandidateSourceKind = Literal["metric_frame", "delta_frame"]
CandidateSemanticKind = Literal["scalar", "time_series", "segmented", "panel"]


class CandidateSetMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_set"] = "candidate_set"

    shape: CandidateShape
    objective: CandidateObjective
    strategy: CandidateStrategy

    source_ref: str
    source_kind: CandidateSourceKind
    metric_ids: list[str]
    semantic_kind: CandidateSemanticKind
    semantic_model: str

    source_refs: list[str]
    recommended_followups: list[FollowupAction] = Field(default_factory=list)
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)

    params: dict[str, Any]


@dataclass(repr=False)
class CandidateSet(BaseFrame):
    meta: CandidateSetMeta

    _NEXT_INTENTS = ("select",)

    def _repr_identity(self) -> str:
        return (
            f"CandidateSet ref={self.meta.ref} objective={self.meta.objective} "
            f"strategy={self.meta.strategy} rows={self.meta.row_count}"
        )

    def _assert_shape(self, expected: CandidateShape) -> CandidateSet:
        if self.meta.shape != expected:
            from marivo.analysis.errors import SemanticKindMismatchError

            raise SemanticKindMismatchError(
                message=f"CandidateSet shape mismatch: expected {expected!r}",
                details={
                    "got_shape": self.meta.shape,
                    "expected_shape": expected,
                },
            )
        return self

    def as_point_anomaly(self) -> CandidateSet:
        return self._assert_shape("point_anomaly")

    def as_period_shift(self) -> CandidateSet:
        return self._assert_shape("period_shift")

    def as_driver_axis(self) -> CandidateSet:
        return self._assert_shape("driver_axis")

    def as_slice(self) -> CandidateSet:
        return self._assert_shape("slice")

    def as_window(self) -> CandidateSet:
        return self._assert_shape("window")

    def as_cross_sectional_outlier(self) -> CandidateSet:
        return self._assert_shape("cross_sectional_outlier")

    def select(self, *, rank: int = 1, attribute: str) -> Any:
        """Read one typed attribute from a single ranked candidate row."""
        from marivo.analysis.intents.select import select

        return select(self, rank=rank, attribute=attribute)
