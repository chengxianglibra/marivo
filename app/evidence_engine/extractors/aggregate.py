from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from uuid import uuid4

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.schemas import Observation


class AggregateRowExtractor(ExtractorContract):
    """Extract observations from aggregate_query rows.

    Context keys:
        group_by (list[str]): columns used for GROUP BY — become slice dimensions.
        observation_type (str): observation type (default: "metric_change").
        value_column (str | None): primary numeric column for payload.
            When omitted, auto-detects the first numeric column that isn't a group_by column.
        metric (str): metric label (default: "aggregate").
    """

    name = "aggregate_rows"
    artifact_type: ClassVar[str] = "aggregate_rows"
    observation_types: ClassVar[list[str]] = ["metric_change"]
    preconditions: ClassVar[list[str]] = []

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        group_by: list[str] = list(context.get("group_by", []))
        observation_type = str(context.get("observation_type", "metric_change"))
        metric = str(context.get("metric", "aggregate"))
        value_column: str | None = context.get("value_column")

        observations: list[Observation] = []
        for row in rows:
            row_dict = dict(row)

            # Determine primary value column
            vc = value_column
            if not vc:
                vc = self._detect_value_column(row_dict, group_by)

            # Build slice from group_by columns
            slice_dict = {col: row_dict[col] for col in group_by if col in row_dict}

            # Build payload with all non-group-by values
            payload: dict[str, Any] = {}
            if vc and vc in row_dict:
                payload["current_value"] = row_dict[vc]
            for k, v in row_dict.items():
                if k not in group_by:
                    payload[k] = v

            observations.append({
                "observation_id": f"obs_{uuid4().hex[:12]}",
                "type": observation_type,
                "subject": {
                    "metric": metric,
                    "slice": slice_dict,
                },
                "payload": payload,
                "significance": {
                    "sample_size": int(payload.get("current_value", 0)) if isinstance(payload.get("current_value"), (int, float)) else 0,
                    "practical_significance": True,
                },
                "quality": {
                    "freshness_ok": True,
                    "sample_size_ok": True,
                },
            })
        return observations

    @staticmethod
    def _detect_value_column(row: dict[str, Any], group_by: list[str]) -> str | None:
        for k, v in row.items():
            if k not in group_by and isinstance(v, (int, float)):
                return k
        return None
