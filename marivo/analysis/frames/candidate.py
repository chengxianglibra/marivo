"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis.evidence.types import JsonScalar
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.windows import AbsoluteWindow
from marivo.refs import DimensionKind, Ref

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
    "concentration",
    "slice_zscore",
    "global_zscore_runs",
    "mad",
    "seasonal_robust_zscore",
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
    params: dict[str, Any]


class _CandidateSelectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    candidate_ref: str
    source_artifact_ref: str
    rank: int = Field(ge=1)
    score: float
    reason_codes: tuple[str, ...] = ()


class PointAnomalySelection(_CandidateSelectionBase):
    kind: Literal["point_anomaly"] = "point_anomaly"
    window: AbsoluteWindow | None = None
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)
    direction: str
    observed_value: float
    baseline_value: float
    delta: float


class PeriodShiftSelection(_CandidateSelectionBase):
    kind: Literal["period_shift"] = "period_shift"
    window: AbsoluteWindow
    baseline_window: AbsoluteWindow
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)
    direction: str


class DriverAxisSelection(_CandidateSelectionBase):
    kind: Literal["driver_axis"] = "driver_axis"
    axis: str | Ref[DimensionKind]


class SliceSelection(_CandidateSelectionBase):
    kind: Literal["slice"] = "slice"
    selector: dict[str | Ref[DimensionKind], JsonScalar]
    window: AbsoluteWindow | None = None


class WindowSelection(_CandidateSelectionBase):
    kind: Literal["window"] = "window"
    window: AbsoluteWindow
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)


class CrossSectionalOutlierSelection(_CandidateSelectionBase):
    kind: Literal["cross_sectional_outlier"] = "cross_sectional_outlier"
    keys: dict[str | Ref[DimensionKind], JsonScalar]
    direction: str
    peer_scope: tuple[str, ...] = ()


CandidateSelection = Annotated[
    PointAnomalySelection
    | PeriodShiftSelection
    | DriverAxisSelection
    | SliceSelection
    | WindowSelection
    | CrossSectionalOutlierSelection,
    Field(discriminator="kind"),
]


@dataclass(repr=False)
class CandidateSet(BaseFrame):
    """Call mv.help(CandidateSet) for its public consumption contract."""

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
                context={
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

    def select(self, *, rank: int = 1) -> CandidateSelection:
        """Return one closed typed selection for a ranked candidate row.

        Example:
            selection = candidates.select(rank=1)
            print(selection.kind, selection.source_artifact_ref)
        """
        from marivo.analysis.intents.select import select

        return select(self, rank=rank)
