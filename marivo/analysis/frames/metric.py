"""MetricFrame and MetricFrameMeta."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import ConfigDict

from marivo.analysis.errors import ComponentFrameUnavailableError
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.windows import (
    AbsoluteWindow,
    RelativeWindow,
    coerce_as_of,
    dump_window,
    normalize_window_input,
    resolve_to_absolute,
)

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.session.core import Session


class MetricFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_frame"] = "metric_frame"
    metric_id: str
    axes: dict[str, Any]
    measure: dict[str, Any]
    window: dict[str, Any] | None
    where: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    decomposition: dict[str, Any] | None = None


@dataclass
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
        window_in = normalize_window_input(window)
        resolved_window: AbsoluteWindow | None
        if isinstance(window_in, RelativeWindow):
            resolved_window = resolve_to_absolute(
                window_in,
                as_of=coerce_as_of(window_in.as_of, tz=cast("ZoneInfo", session.tz)),
                tz=cast("ZoneInfo", session.tz),
            )
        else:
            resolved_window = window_in
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
        frame.meta = cast("MetricFrameMeta", write_frame_to_disk(session.layout, frame))
        return frame

    def components(self) -> ComponentFrame:
        """Load the linked ComponentFrame for component-aware derived metrics."""
        from marivo.analysis.frames.component import ComponentFrame
        from marivo.analysis.session._load import load_frame
        from marivo.analysis.session.attach import active

        if self.meta.component_ref is None:
            raise ComponentFrameUnavailableError(
                message=(
                    "components are only available for derived ratio or "
                    "weighted-average metric frames produced by component-aware observe"
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
