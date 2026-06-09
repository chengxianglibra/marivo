"""ForecastFrame frame family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


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
    horizon_unit: Literal["day", "week", "month", "quarter"]
    model: Literal["naive", "seasonal_naive", "drift"]
    seasonality_period: int | None
    interval_level: float
    interval_method: Literal["normal_residual"]
    train_row_count_per_segment: dict[str, int]
    segment_dimensions: list[str]


@dataclass(repr=False)
class ForecastFrame(BaseFrame):
    meta: ForecastFrameMeta

    def _repr_identity(self) -> str:
        return (
            f"ForecastFrame ref={self.meta.ref} metric={self.meta.metric_id} "
            f"rows={self.meta.row_count}"
        )
