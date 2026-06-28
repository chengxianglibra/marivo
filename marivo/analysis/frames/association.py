"""AssociationResult and AssociationResultMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.frames.render import format_bounded_card


class AssociationResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["association_result"] = "association_result"
    source_refs: list[str]
    metric_ids: list[str]
    semantic_kinds: list[Literal["scalar", "time_series", "segmented", "panel"]]
    semantic_models: list[str]
    method: Literal["pearson"]
    alignment: dict[str, Any]
    lag_policy: dict[str, Any]
    aligned_row_count: int
    dropped_row_count: int
    correlation: float


@dataclass(repr=False)
class AssociationResult(BaseFrame):
    meta: AssociationResultMeta

    def _repr_identity(self) -> str:
        return (
            f"AssociationResult ref={self.meta.ref} method={self.meta.method} "
            f"r={self.meta.correlation:.2f} rows={self.meta.row_count}"
        )

    def render(self) -> str:
        columns, preview_rows = self._preview_rows(limit=5)
        metric_ids = ",".join(self.meta.metric_ids)
        return format_bounded_card(
            identity=self._repr_identity(),
            status=(
                f"method={self.meta.method} r={self.meta.correlation:.2f} "
                f"aligned={self.meta.aligned_row_count} dropped={self.meta.dropped_row_count} "
                f"metrics={metric_ids}"
            ),
            columns=columns,
            rows=preview_rows,
            row_count=len(self._df),
            preview_truncation_hint="call .to_pandas() for terminal custom analysis",
            available=self._AVAILABLE_ENTRIES,
        )
