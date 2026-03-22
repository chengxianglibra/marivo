from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.factories import make_anomaly_observation
from app.evidence_engine.schemas import Observation


class AnomalyExtractor(ExtractorContract):
    name = "anomaly_rows"
    artifact_type: ClassVar[str] = "anomaly_rows"
    observation_types: ClassVar[list[str]] = ["anomaly_detection"]
    preconditions: ClassVar[list[str]] = ["value_col", "dim_col"]

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})

        value_col = context.get("value_col")
        dim_col = context.get("dim_col")
        if not value_col or not dim_col:
            raise ValueError("AnomalyExtractor requires 'value_col' and 'dim_col' in context")

        z_threshold = float(context.get("z_threshold", 2.0))
        metric = str(context.get("metric", value_col))

        row_list = list(rows)
        if len(row_list) < 3:
            return []

        values = [float(r[value_col]) for r in row_list if value_col in r]
        if len(values) < 3:
            return []

        mean = statistics.mean(values)
        try:
            std = statistics.stdev(values)
        except statistics.StatisticsError:
            std = 0.0

        if std == 0.0:
            return []

        observations: list[Observation] = []
        for row in row_list:
            row_dict = dict(row)
            if value_col not in row_dict:
                continue
            val = float(row_dict[value_col])
            z = (val - mean) / std
            if abs(z) > z_threshold:
                slice_info = {str(dim_col): row_dict.get(dim_col)}
                payload = {
                    "value": val,
                    "mean": mean,
                    "std": std,
                    "z_score": z,
                    "sample_size": len(values),
                }
                quality = {"freshness_ok": True, "sample_size_ok": True}
                observations.append(make_anomaly_observation(metric, slice_info, payload, quality))

        return observations
