from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.factories import make_funnel_observation
from app.evidence_engine.schemas import Observation


class FunnelExtractor(ExtractorContract):
    name = "funnel_rows"
    artifact_type: ClassVar[str] = "funnel_rows"
    observation_types: ClassVar[list[str]] = ["funnel_drop"]
    preconditions: ClassVar[list[str]] = ["stage_col", "count_col"]

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        stage_col = str(context.get("stage_col", "stage_name"))
        count_col = str(context.get("count_col", "count"))
        threshold = float(context.get("threshold", 0.30))
        funnel_name = str(context.get("funnel_name", "funnel"))

        if not rows:
            return []

        # Build stages list with adjacent drop rates
        row_list = list(rows)
        stages: list[dict[str, Any]] = []
        for i, row in enumerate(row_list):
            row_dict = dict(row)
            stage_name = str(row_dict.get(stage_col, f"stage_{i}"))
            count = float(row_dict.get(count_col, 0))
            drop_rate = 0.0
            if i > 0:
                prev_count = float(row_list[i - 1].get(count_col, 0))
                if prev_count > 0:
                    drop_rate = (prev_count - count) / prev_count
            stages.append(
                {
                    "stage_name": stage_name,
                    "delta_drop_rate": drop_rate,
                    "users": int(count),
                }
            )

        # Produce observation only if any drop exceeds threshold
        if not any(abs(s["delta_drop_rate"]) > threshold for s in stages):
            return []

        quality = {"freshness_ok": True, "sample_size_ok": True}
        return [make_funnel_observation(funnel_name, stages, quality)]
