"""DeltaFrame and DeltaFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from pydantic import ConfigDict, Field

from marivo.analysis.errors import FrameReadError
from marivo.analysis.frames.base import (
    _PREVIEW_MAX_LIMIT,
    _RENDER_MAX_COLUMNS,
    BaseFrame,
    BaseFrameMeta,
    FramePreview,
    _display_column_names,
    _preview_cell,
    assert_semantic_shape,
)
from marivo.analysis.frames.render import format_bounded_card

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.intents._shape import AttributionShape

_DELTA_RENDER_PREVIEW_ROWS = 20


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


@dataclass(repr=False)
class DeltaFrame(BaseFrame):
    meta: DeltaFrameMeta

    _NEXT_INTENTS = ("decompose", "discover", "transform")

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

    def predicted_attribution_shape(self) -> AttributionShape:
        """Predict the AttributionFrame shape decompose will produce for this delta.

        Reads this delta's component_ref + decomposition kind only (no component
        load); "sum" when not component-aware, else "ratio_mix"/"weighted_mix".
        """
        from marivo.analysis.intents._shape import attribution_output_shape

        return attribution_output_shape(self.meta)

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

    # -- Delta-aware render / preview overrides ----------------------------------

    def _sorted_preview_df(self, limit: int) -> tuple[Any, int]:
        """Return a DataFrame sorted by |pct_change| desc, and the total row count.

        Rows where pct_change is NaN/inf fall back to |delta| desc.
        Rows with neither computable value go last (original order).
        """
        df = self._df
        total = len(df)
        if total == 0:
            return df, total

        df_sorted = df.copy()
        if "pct_change" in df_sorted.columns and "delta" in df_sorted.columns:
            pct = pd.to_numeric(df_sorted["pct_change"], errors="coerce")
            delta = pd.to_numeric(df_sorted["delta"], errors="coerce")

            # Primary sort key: |pct_change|; NaN/inf → 0 (pushed to bottom)
            pct_abs = np.abs(pct)
            pct_abs = pct_abs.where(np.isfinite(pct_abs), other=0.0)
            df_sorted["_sort_pct"] = pct_abs

            # Secondary sort key: |delta| (for rows without computable pct_change)
            delta_abs = np.abs(delta)
            delta_abs = delta_abs.where(np.isfinite(delta_abs), other=0.0)
            df_sorted["_sort_delta"] = delta_abs

            df_sorted = df_sorted.sort_values(
                by=["_sort_pct", "_sort_delta"],
                ascending=False,
                kind="mergesort",
            )
            df_sorted = df_sorted.drop(columns=["_sort_pct", "_sort_delta"])

        return df_sorted.head(limit), total

    def render(self) -> str:
        """Return bounded plain-text result card sorted by |pct_change| descending.

        Delta frames show up to 20 preview rows (instead of the default 5)
        sorted so the biggest changes appear first.

        Returns:
            Bounded plain text suitable for terminal/agent inspection.

        Example:
            >>> delta.show()
            DeltaFrame ref=frame_abc metric=sales.revenue rows=91
            preview (top 20 of 91 rows):
            ...
        """
        preview_df, total = self._sorted_preview_df(_DELTA_RENDER_PREVIEW_ROWS)
        columns = _display_column_names(self._df.columns)
        visible_columns = columns[:_RENDER_MAX_COLUMNS]
        preview_rows: list[list[str]] = []
        for row in preview_df.itertuples(index=False, name=None):
            preview_rows.append([str(_preview_cell(v)) for v in row[:_RENDER_MAX_COLUMNS]])

        label = (
            f"preview (top {len(preview_rows)} of {total} rows):"
            if total > _DELTA_RENDER_PREVIEW_ROWS
            else "preview:"
        )
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self._render_status(),
            columns=visible_columns,
            rows=preview_rows,
            row_count=total,
            preview_truncation_hint="call .preview(limit=...) or .to_pandas()",
            available=self._AVAILABLE_ENTRIES,
            max_preview_rows=_DELTA_RENDER_PREVIEW_ROWS,
            preview_label=label,
        )

    def preview(self, limit: int = 10) -> FramePreview:
        """Return a bounded preview sorted by |pct_change| descending.

        Args:
            limit: Maximum number of preview rows (1–100).

        Returns:
            FramePreview with rows sorted by magnitude of change.

        Example:
            >>> delta.preview(limit=20)
        """
        if limit < 1 or limit > _PREVIEW_MAX_LIMIT:
            raise FrameReadError(
                message="preview limit must be between 1 and 100",
                details={"limit": limit, "min": 1, "max": _PREVIEW_MAX_LIMIT},
            )

        preview_df, total = self._sorted_preview_df(limit)
        columns = _display_column_names(self._df.columns)
        rows = [
            {column: _preview_cell(value) for column, value in zip(columns, row, strict=True)}
            for row in preview_df.itertuples(index=False, name=None)
        ]
        return FramePreview(
            kind=self.meta.kind,
            ref=self.meta.ref,
            row_count=total,
            returned_row_count=len(rows),
            columns=columns,
            rows=rows,
            is_truncated=total > limit,
        )
