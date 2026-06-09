"""ComponentFrame and ComponentFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class ComponentFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["component_frame"] = "component_frame"
    parent_ref: str
    parent_kind: Literal["metric_frame", "delta_frame"]
    metric_id: str
    decomposition_kind: Literal["ratio", "weighted_average"]
    components: dict[str, str]
    axes: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass(repr=False)
class ComponentFrame(BaseFrame):
    meta: ComponentFrameMeta

    _NEXT_INTENTS: tuple[str, ...] = ()

    def _repr_identity(self) -> str:
        return f"ComponentFrame ref={self.meta.ref} rows={self.meta.row_count}"
