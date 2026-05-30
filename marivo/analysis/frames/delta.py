"""DeltaFrame and DeltaFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict

from marivo.analysis.errors import ComponentFrameUnavailableError
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, assert_semantic_shape

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame


class DeltaFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["delta_frame"] = "delta_frame"
    metric_id: str
    source_current_ref: str
    source_baseline_ref: str
    alignment: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    decomposition: dict[str, Any] | None = None


@dataclass(repr=False)
class DeltaFrame(BaseFrame):
    meta: DeltaFrameMeta

    _NEXT_INTENTS = ("decompose", "discover", "transform")

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

    def components(self) -> ComponentFrame:
        """Load the linked ComponentFrame for component-aware deltas."""
        from marivo.analysis.frames.component import ComponentFrame
        from marivo.analysis.session._load import load_frame
        from marivo.analysis.session.attach import active

        if self.meta.component_ref is None:
            raise ComponentFrameUnavailableError(
                message=(
                    "components are only available for derived ratio or "
                    "weighted-average delta frames produced by component-aware compare"
                ),
                details={"parent_ref": self.ref, "parent_kind": self.meta.kind},
            )
        loaded = load_frame(self.meta.component_ref, session=active())
        if not isinstance(loaded, ComponentFrame):
            raise ComponentFrameUnavailableError(
                message="linked component_ref did not resolve to a ComponentFrame",
                details={
                    "parent_ref": self.ref,
                    "parent_kind": self.meta.kind,
                    "component_ref": self.meta.component_ref,
                    "loaded_kind": loaded.meta.kind,
                },
            )
        return loaded
