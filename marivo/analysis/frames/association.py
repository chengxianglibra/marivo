"""AssociationResult and AssociationResultMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, _display_column_names
from marivo.render import Card


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

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        metric_ids = ",".join(self.meta.metric_ids)
        return (
            Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES)
            .status(
                f"method={self.meta.method} r={self.meta.correlation:.2f} "
                f"aligned={self.meta.aligned_row_count} dropped={self.meta.dropped_row_count} "
                f"metrics={metric_ids}"
            )
            .lazy_table(
                columns=columns,
                rows_provider=self._preview_rows_provider,
                row_count=len(self._df),
            )
        )
