"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

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
    """Call mv.help(AssociationResult) for its public consumption contract."""

    meta: AssociationResultMeta

    def _repr_identity(self) -> str:
        return (
            f"AssociationResult ref={self.meta.ref} method={self.meta.method} "
            f"r={self.meta.correlation:.2f} rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        metric_ids = ",".join(self.meta.metric_ids)
        status_parts = [
            f"method={self.meta.method}",
            f"r={self.meta.correlation:.2f}",
            f"aligned={self.meta.aligned_row_count}",
            f"dropped={self.meta.dropped_row_count}",
            f"metrics={metric_ids}",
        ]
        evidence = self._evidence_status_token()
        if evidence is not None:
            status_parts.append(evidence)
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES).status(
            " ".join(status_parts)
        )
        self._append_evidence_sections(card)
        return card.lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )
