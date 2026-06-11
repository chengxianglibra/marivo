"""MetricFrame and MetricFrameMeta."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, assert_semantic_shape
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.windows import (
    dump_window,
    normalize_absolute_window_input,
)

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.session.core import Session


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
    decomposition: dict[str, Any] | None = None


@dataclass(repr=False)
class MetricFrame(BaseFrame):
    meta: MetricFrameMeta

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

    @classmethod
    def from_dataframe(
        cls,
        df: pd.DataFrame,
        *,
        metric_id: str,
        axes: dict[str, Any],
        measure: dict[str, Any],
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
        semantic_model: str,
        window: object | None = None,
        where: dict[str, Any] | None = None,
        session: Session,
    ) -> MetricFrame:
        """Create and persist an external-entry MetricFrame from pandas."""
        from marivo.analysis.session.core import ensure_session_writable
        from marivo.analysis.session.persistence import write_frame_to_disk

        ensure_session_writable(session)
        resolved_window = normalize_absolute_window_input(window)
        meta_window = dump_window(resolved_window)

        frame_ref = f"frame_{secrets.token_hex(4)}"
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="from_dataframe",
                        job_ref=None,
                        inputs=[],
                        params_digest="external",
                    )
                ],
                external_inputs=[frame_ref],
            ),
            metric_id=metric_id,
            axes=axes,
            measure=measure,
            window=meta_window,
            where=where or {},
            semantic_kind=semantic_kind,
            semantic_model=semantic_model,
        )
        frame = cls(_df=df.copy(), meta=meta)
        frame.meta = cast("MetricFrameMeta", write_frame_to_disk(session._layout, frame))
        return frame

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
            decomposition=self.meta.decomposition,
            advice="re-run observe() to regenerate it",
        )
