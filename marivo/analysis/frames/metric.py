"""MetricFrame and MetricFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import (
    ArtifactContract,
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    assert_semantic_shape,
)
from marivo.render import Card

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.coverage import CoverageFrame


class MetricFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_frame"] = "metric_frame"
    metric_id: str | None
    unit: str | None = None
    axes: dict[str, Any]
    measure: dict[str, Any]
    measures: list[dict[str, Any]] | None = None
    window: dict[str, Any] | None
    where: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    composition: dict[str, Any] | None = None
    fold: dict[str, Any] | None = None
    reaggregatable: bool = True
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None
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
        if self.arity > 1:
            return (
                f"MetricFrame ref={self.meta.ref} metrics={self.arity} "
                f"shape={self.meta.semantic_kind} rows={self.meta.row_count}"
            )
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"MetricFrame ref={self.meta.ref} metric={self.meta.metric_id} "
            f"shape={self.meta.semantic_kind}{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel"]:
        """The frame's semantic shape (distinct from .shape, the dataframe dims)."""
        return self.meta.semantic_kind

    def measures_meta(self) -> list[dict[str, Any]]:
        """Ordered per-metric measure records; derived from scalar fields at arity-1."""
        if self.meta.measures:
            return [dict(entry) for entry in self.meta.measures]
        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        return [
            {
                "metric_id": self.meta.metric_id,
                "name": measure.get("name"),
                "column": self.VALUE_COLUMN,
                "unit": self.meta.unit,
                "additivity": self.meta.additivity,
                "reaggregatable": self.meta.reaggregatable,
            }
        ]

    @property
    def metrics(self) -> tuple[str, ...]:
        """Ordered metric ids carried by this frame."""
        return tuple(entry["metric_id"] for entry in self.measures_meta())

    @property
    def arity(self) -> int:
        """Number of metrics carried by this frame."""
        return len(self.measures_meta())

    # Every next-intent is gated at arity > 1; derive from _NEXT_INTENTS so
    # the two cannot drift.
    _GATED_INTENTS: tuple[str, ...] = _NEXT_INTENTS

    def _card(self) -> Card:
        card = super()._card()
        if self.arity > 1:
            card.listing(
                label="measures",
                items=[
                    f"{entry['metric_id']} column={entry['column']}"
                    + (f" unit={entry['unit']}" if entry.get("unit") else "")
                    for entry in self.measures_meta()
                ],
            )
        return card

    def contract(self) -> ArtifactContract:
        """Return the mechanical consumption contract, gating multi-metric frames.

        At arity > 1, gated affordances (compare, correlate, transform,
        assess_quality, hypothesis_test, forecast, discover) carry a
        ``single_metric`` precondition teaching the agent to project to one
        metric first. Arity-1 frames return the base contract unchanged.
        """
        contract = super().contract()
        if self.arity <= 1:
            return contract
        first_metric = self.metrics[0]
        precondition = ArtifactPrecondition(
            check="single_metric",
            status="fail",
            reason=(f'frame carries {self.arity} metrics; call .metric("{first_metric}") first'),
        )
        gated = set(self._GATED_INTENTS)
        affordances = [
            affordance.model_copy(
                update={"preconditions": [*affordance.preconditions, precondition]}
            )
            if affordance.operator in gated
            else affordance
            for affordance in contract.affordances
        ]
        return contract.model_copy(update={"affordances": affordances})

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

    def metric(self, metric_id: str) -> MetricFrame:
        """Project one metric out of this frame as an arity-1 MetricFrame.

        Args:
            metric_id: Full metric id carried by this frame (see ``.metrics``).

        Returns:
            An arity-1 MetricFrame with the shared axes and that metric's
            values in the canonical ``value`` column. On an arity-1 frame,
            returns ``self`` when the id matches.

        Example:
            >>> revenue = frame.metric("sales.revenue")

        Constraints:
            Requires the frame's owning session to be current; commits a
            ``select_metric`` step (no backend query).
        """
        from marivo.analysis.frames._metric_projection import project_metric

        return project_metric(self, metric_id)
