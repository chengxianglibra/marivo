"""MetricFrame and MetricFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, assert_semantic_shape

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.coverage import CoverageFrame


class MetricFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_frame"] = "metric_frame"
    metric_id: str
    unit: str | None = None
    axes: dict[str, Any]
    measure: dict[str, Any]
    window: dict[str, Any] | None
    where: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    composition: dict[str, Any] | None = None
    fold: dict[str, Any] | None = None
    reaggregatable: bool = True
    sample_set_digest: str | None = None
    quantile_mode: Literal["exact", "approximate"] | None = None
    quantile_method: str | None = None
    coverage_ref: str | None = None
    coverage_summary: dict[str, Any] | None = None


@dataclass(repr=False)
class MetricFrame(BaseFrame):
    meta: MetricFrameMeta

    #: Canonical column name for the metric value in the wrapped DataFrame.
    VALUE_COLUMN: str = "value"

    _NEXT_INTENTS = (
        "compare",
        "discover",
        "correlate",
        "transform",
        "assess_quality",
        "hypothesis_test",
        "forecast",
    )

    def _repr_identity(self) -> str:
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"MetricFrame ref={self.meta.ref} metric={self.meta.metric_id} "
            f"shape={self.meta.semantic_kind}{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel"]:
        """The frame's semantic shape (distinct from .shape, the dataframe dims)."""
        return self.meta.semantic_kind

    def as_scalar(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="scalar", frame_kind=self.meta.kind
        )
        return self

    def as_time_series(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="time_series", frame_kind=self.meta.kind
        )
        return self

    def as_segmented(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="segmented", frame_kind=self.meta.kind
        )
        return self

    def as_panel(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="panel", frame_kind=self.meta.kind
        )
        return self

    def components(self) -> ComponentFrame:
        """Load the linked ComponentFrame for component-aware derived metrics."""
        from marivo.analysis.frames._component import _load_component_frame

        return _load_component_frame(
            parent_ref=self.ref,
            parent_kind=self.meta.kind,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            component_ref=self.meta.component_ref,
            composition=self.meta.composition,
            advice="re-run observe() to regenerate it",
        )

    def coverage(self) -> CoverageFrame:
        """Load the linked CoverageFrame for sampled time-slot coverage."""
        from marivo.analysis.frames._coverage import _load_coverage_frame

        return _load_coverage_frame(
            parent_ref=self.ref,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            coverage_ref=self.meta.coverage_ref,
        )
