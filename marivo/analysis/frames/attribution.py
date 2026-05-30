"""AttributionFrame and AttributionFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, assert_attribution_shape


class AttributionFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["attribution_frame"] = "attribution_frame"
    metric_ids: list[str]
    source_refs: list[str]
    scope_delta_ref: str | None = None
    attribution_kind: Literal["decomposition", "correlation", "anomaly"]
    driver_field: str | None
    value_column: str | None
    contribution_column: str | None
    method: str
    params: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass
class AttributionFrame(BaseFrame):
    meta: AttributionFrameMeta

    @property
    def attribution_shape(self) -> str:
        """The decomposition method tag: 'sum', 'ratio_mix', or 'weighted_mix'."""
        return self.meta.method

    def as_sum(self) -> AttributionFrame:
        assert_attribution_shape(got=self.meta.method, expected="sum", frame_kind=self.meta.kind)
        return self

    def as_ratio_mix(self) -> AttributionFrame:
        assert_attribution_shape(
            got=self.meta.method, expected="ratio_mix", frame_kind=self.meta.kind
        )
        return self

    def as_weighted_mix(self) -> AttributionFrame:
        assert_attribution_shape(
            got=self.meta.method, expected="weighted_mix", frame_kind=self.meta.kind
        )
        return self
