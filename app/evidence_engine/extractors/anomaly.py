from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.factories import make_anomaly_observation
from app.evidence_engine.schemas import Observation


def _compute_outliers(
    values: list[float],
    z_threshold: float,
    use_iqr: bool,
) -> list[int]:
    """Return indices of outlier values using z-score and optional IQR rules."""
    if len(values) < 3:
        return []

    try:
        mean = statistics.mean(values)
        std = statistics.stdev(values)
    except statistics.StatisticsError:
        return []

    if std == 0.0:
        z_outliers: set[int] = set()
    else:
        z_outliers = {
            idx for idx, value in enumerate(values) if abs((value - mean) / std) > z_threshold
        }

    if not use_iqr:
        return sorted(z_outliers)

    try:
        quartiles = statistics.quantiles(values, n=4, method="inclusive")
    except statistics.StatisticsError:
        return sorted(z_outliers)

    if len(quartiles) < 3:
        return sorted(z_outliers)

    q1 = quartiles[0]
    q3 = quartiles[2]
    iqr = q3 - q1
    if iqr == 0.0:
        return sorted(z_outliers)

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    iqr_outliers = {idx for idx, value in enumerate(values) if value < lower or value > upper}
    return sorted(z_outliers | iqr_outliers)


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

        outlier_indices = set(_compute_outliers(values, z_threshold=z_threshold, use_iqr=False))
        if not outlier_indices:
            return []

        mean = statistics.mean(values)
        std = statistics.stdev(values)

        observations: list[Observation] = []
        value_idx = 0
        for row in row_list:
            row_dict = dict(row)
            if value_col not in row_dict:
                continue
            val = float(row_dict[value_col])
            z = (val - mean) / std if std != 0.0 else 0.0
            if value_idx in outlier_indices:
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
            value_idx += 1

        return observations
