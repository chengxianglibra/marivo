from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.factories import make_contribution_observation
from app.evidence_engine.schemas import Observation


class ContributionShiftExtractor(ExtractorContract):
    name = "contribution_shift_rows"
    artifact_type: ClassVar[str] = "contribution_shift_rows"
    observation_types: ClassVar[list[str]] = ["contribution_shift"]
    preconditions: ClassVar[list[str]] = ["dim_col", "baseline_col", "current_col"]

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})

        dim_col = context.get("dim_col")
        baseline_col = context.get("baseline_col")
        current_col = context.get("current_col")
        if not dim_col or not baseline_col or not current_col:
            raise ValueError(
                "ContributionShiftExtractor requires 'dim_col', 'baseline_col', 'current_col' in context"
            )

        share_threshold = float(context.get("share_threshold", 0.10))
        metric = str(context.get("metric", str(dim_col)))

        row_list = list(rows)
        if not row_list:
            return []

        total_baseline = sum(float(r.get(baseline_col, 0)) for r in row_list)
        total_current = sum(float(r.get(current_col, 0)) for r in row_list)

        contributions: list[dict[str, Any]] = []
        for row in row_list:
            row_dict = dict(row)
            baseline_val = float(row_dict.get(baseline_col, 0))
            current_val = float(row_dict.get(current_col, 0))

            baseline_share = baseline_val / total_baseline if total_baseline > 0 else 0.0
            current_share = current_val / total_current if total_current > 0 else 0.0
            share_delta = current_share - baseline_share

            if abs(share_delta) >= share_threshold:
                contributions.append({
                    "segment_value": row_dict.get(dim_col),
                    "baseline_share": baseline_share,
                    "current_share": current_share,
                    "delta_share": share_delta,
                    "current_count": int(current_val),
                })

        if not contributions:
            return []

        quality = {"freshness_ok": True, "sample_size_ok": True}
        return [make_contribution_observation(metric, str(dim_col), contributions, quality)]
