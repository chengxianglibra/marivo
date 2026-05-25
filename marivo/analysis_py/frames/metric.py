"""MetricFrame and MetricFrameMeta."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ConfigDict

from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.windows import (
    AbsoluteWindow,
    RelativeWindow,
    coerce_as_of,
    dump_window,
    normalize_window_input,
    resolve_to_absolute,
    zoneinfo_from_name,
)

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis_py.session.core import Session


class MetricFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_frame"] = "metric_frame"
    metric_id: str
    axes: dict[str, Any]
    measure: dict[str, Any]
    window: dict[str, Any] | None
    slice: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass
class MetricFrame(BaseFrame):
    meta: MetricFrameMeta

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
        slice: dict[str, Any] | None = None,
        session: Session,
    ) -> MetricFrame:
        """Create and persist an external-entry MetricFrame from pandas."""
        from marivo.analysis_py.session.core import ensure_session_writable
        from marivo.analysis_py.session.persistence import write_frame_to_disk

        ensure_session_writable(session)
        window_in = normalize_window_input(window)
        resolved_window: AbsoluteWindow | None
        if isinstance(window_in, RelativeWindow):
            effective_tz = session.tz
            if window_in.tz is not None:
                effective_tz = zoneinfo_from_name(window_in.tz)
            resolved_window = resolve_to_absolute(
                window_in,
                as_of=coerce_as_of(window_in.as_of, tz=effective_tz),
                tz=effective_tz,
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
            slice=slice or {},
            semantic_kind=semantic_kind,
            semantic_model=semantic_model,
        )
        frame = cls(_df=df.copy(), meta=meta)
        frame.meta = cast("MetricFrameMeta", write_frame_to_disk(session.layout, frame))
        return frame
