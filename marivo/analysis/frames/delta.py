"""DeltaFrame and DeltaFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field

from marivo.analysis.frames.base import (
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    assert_semantic_shape,
)
from marivo.render import Card

if TYPE_CHECKING:
    from marivo.analysis.frames.base import ArtifactContract
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.transforms import DeltaFrameTransforms
    from marivo.analysis.intents._shape import AttributionShape


class DeltaFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["delta_frame"] = "delta_frame"
    metric_id: str
    unit: str | None = None
    source_current_ref: str
    source_baseline_ref: str
    alignment: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    composition: dict[str, Any] | None = None
    fold: dict[str, Any] | None = None
    component_folds: list[dict[str, Any]] = Field(default_factory=list)
    cumulative: dict[str, Any] | None = None
    rollup_fold: Literal["last"] | None = None


@dataclass(repr=False)
class DeltaFrame(BaseFrame):
    meta: DeltaFrameMeta

    _NEXT_INTENTS = ("attribute", "discover", "transform")

    def _repr_identity(self) -> str:
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"DeltaFrame ref={self.meta.ref} metric={self.meta.metric_id}"
            f"{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel"]:
        """The frame's semantic shape (distinct from .shape, the dataframe dims)."""
        return self.meta.semantic_kind

    def as_scalar(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="scalar", frame_kind=self.meta.kind
        )
        return self

    def as_time_series(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="time_series", frame_kind=self.meta.kind
        )
        return self

    def as_segmented(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="segmented", frame_kind=self.meta.kind
        )
        return self

    def as_panel(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="panel", frame_kind=self.meta.kind
        )
        return self

    def _to_date_tail(self) -> dict[str, Any] | None:
        """Return the to-date alignment dump when a non-empty baseline tail exists.

        Surfaced in ``show()`` / ``contract()`` so the agent knows the baseline
        window was longer than the current window: the extra tail buckets were
        dropped from the delta rows but remain available via ``to_pandas()``.
        """
        to_date = self.meta.alignment.get("to_date") if self.meta.alignment else None
        if not isinstance(to_date, dict):
            return None
        tail = to_date.get("baseline_tail_buckets")
        if not isinstance(tail, int) or tail <= 0:
            return None
        return to_date

    def _card(self) -> Card:
        card = super()._card()
        to_date = self._to_date_tail()
        if to_date is not None:
            card.field(
                "to_date_alignment",
                (
                    f"matched_buckets={to_date.get('matched_buckets')} "
                    f"baseline_tail_buckets={to_date.get('baseline_tail_buckets')} "
                    f"reset_grain={to_date.get('reset_grain')}"
                ),
            )
        return card

    def contract(self) -> ArtifactContract:
        contract = super().contract()
        to_date = self._to_date_tail()
        if to_date is None:
            return contract
        caveat = ArtifactPrecondition(
            check="to_date_baseline_tail",
            status="pass",
            reason=(
                f"ordinal alignment matched {to_date.get('matched_buckets')} buckets; "
                f"{to_date.get('baseline_tail_buckets')} baseline tail bucket(s) dropped "
                f"from delta rows (reset_grain={to_date.get('reset_grain')})"
            ),
        )
        affordances = [
            affordance.model_copy(update={"preconditions": [*affordance.preconditions, caveat]})
            for affordance in contract.affordances
        ]
        return contract.model_copy(update={"affordances": affordances})

    def predicted_attribution_shape(self) -> AttributionShape:
        """Predict the AttributionFrame shape decompose will produce for this delta.

        Reads this delta's component_ref + decomposition kind only (no component
        load); "sum" when not component-aware, else "ratio_mix"/"weighted_mix".
        """
        from marivo.analysis.intents._shape import attribution_output_shape

        return attribution_output_shape(self.meta)

    @property
    def transform(self) -> DeltaFrameTransforms:
        """Return typed transforms for this DeltaFrame."""
        from marivo.analysis.frames.transforms import DeltaFrameTransforms

        return DeltaFrameTransforms(self)

    def components(self) -> ComponentFrame:
        """Load the linked ComponentFrame for component-aware deltas."""
        from marivo.analysis.frames._component import _load_component_frame

        return _load_component_frame(
            parent_ref=self.ref,
            parent_kind=self.meta.kind,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            component_ref=self.meta.component_ref,
            composition=self.meta.composition,
            advice="re-run compare() to regenerate it",
        )
