"""Typed forecast analysis frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo._temporal import FrameTemporalContractV1
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.render import Card


class ForecastFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["forecast_frame"] = "forecast_frame"
    source_refs: list[str]
    metric_id: str
    semantic_model: str
    semantic_kind: Literal["time_series", "panel"]
    measure: dict[str, Any]
    axes: dict[str, Any]
    history_window: dict[str, Any]
    forecast_window: dict[str, Any]
    horizon: int
    horizon_unit: str
    model: Literal["naive", "seasonal_naive", "drift"]
    seasonality_period: int | None
    interval_level: float
    interval_method: Literal["normal_residual"]
    train_row_count_per_segment: dict[str, int]
    segment_dimensions: list[str]
    temporal_contract: FrameTemporalContractV1 | None = None


@dataclass(repr=False)
class ForecastFrame(BaseFrame):
    """Call marivo.help(ForecastFrame) for its public consumption contract."""

    meta: ForecastFrameMeta

    def _repr_identity(self) -> str:
        return (
            f"ForecastFrame ref={self.meta.ref} metric={self.meta.metric_id} "
            f"rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        card = self._header_card()
        card.field(
            "forecast",
            (
                f"model={self.meta.model} horizon={self.meta.horizon} "
                f"unit={self.meta.horizon_unit} interval={self.meta.interval_level:g} "
                f"method={self.meta.interval_method}"
            ),
        )
        card.field("history_window", str(self.meta.history_window))
        card.field("forecast_window", str(self.meta.forecast_window))
        card.field(
            "segments",
            (
                f"dimensions={','.join(self.meta.segment_dimensions) or 'none'} "
                f"training_counts={self.meta.train_row_count_per_segment}"
            ),
        )
        self._append_evidence_sections(card)
        return self._append_preview_table(card)
