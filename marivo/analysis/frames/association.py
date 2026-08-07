"""Typed association analysis results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, model_validator

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, _display_column_names
from marivo.render import Card

#: The single lag a summary/evidence represents is selected as the lag with the
#: strongest absolute correlation, preferring the closest lag on ties.
SELECTION_RULE_MAX_ABS = "max_abs_correlation_closest_lag"
#: A single-lag (default) correlate call has no exploration: exactly lag 0 is used.
SELECTION_RULE_SINGLE = "single_lag"


class AssociationResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["association_result"] = "association_result"
    source_refs: list[str]
    metric_ids: list[str]
    semantic_kinds: list[Literal["scalar", "time_series", "segmented", "panel"]]
    semantic_models: list[str]
    method: Literal["pearson", "spearman", "kendall"]
    alignment: dict[str, Any]
    lag_policy: dict[str, Any]
    aligned_row_count: int
    dropped_row_count: int
    correlation: float
    best_lag: int = 0
    selection_rule: str = SELECTION_RULE_SINGLE

    @model_validator(mode="before")
    @classmethod
    def _infer_selection_rule_for_legacy_artifacts(cls, data: Any) -> Any:
        """Default the selection rule from ``lag_policy`` for legacy artifacts.

        Artifacts written before ``selection_rule`` was introduced carry only
        ``lag_policy.mode``; a range artifact would otherwise wrongly reload as
        ``"single_lag"`` and mislead agents about the summary's provenance.
        """
        if not isinstance(data, dict):
            return data
        if "selection_rule" in data and data["selection_rule"] is not None:
            return data
        lag_policy = data.get("lag_policy") or {}
        if isinstance(lag_policy, dict) and lag_policy.get("mode") == "range":
            data["selection_rule"] = SELECTION_RULE_MAX_ABS
        return data

    @property
    def selected_lag_offset(self) -> int:
        """Return the lag the summary/evidence represents (the selected lag).

        Derived from ``best_lag`` (the persisted single source of truth), so the
        field never needs separate persistence or schema migration.
        """
        return self.best_lag


@dataclass(repr=False)
class AssociationResult(BaseFrame):
    """Call marivo.help(AssociationResult) for its public consumption contract."""

    meta: AssociationResultMeta

    def _repr_identity(self) -> str:
        return (
            f"AssociationResult ref={self.meta.ref} method={self.meta.method} "
            f"r={self.meta.correlation:.2f} lag={self.meta.selected_lag_offset} "
            f"rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        metric_ids = ",".join(self.meta.metric_ids)
        status_parts = [
            f"method={self.meta.method}",
            f"r={self.meta.correlation:.2f}",
            f"lag={self.meta.selected_lag_offset}",
            f"sel={self.meta.selection_rule}",
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
