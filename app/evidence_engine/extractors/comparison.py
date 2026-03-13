from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.evidence_engine.extractors.base import ObservationExtractor
from app.evidence_engine.factories import make_observation
from app.evidence_engine.schemas import Observation


class ComparisonRowExtractor(ObservationExtractor):
    name = "comparison_rows"

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        metric = str(context.get("metric", "")).strip()
        observation_type = str(context.get("observation_type", "metric_change")).strip()
        payload_fields = {
            str(key): str(value)
            for key, value in dict(context.get("payload_fields", {})).items()
        }
        quality_builder = context.get("quality_builder")

        observations: list[Observation] = []
        for row in rows:
            payload = {
                payload_name: row[row_field]
                for payload_name, row_field in payload_fields.items()
                if row_field in row
            }
            quality = (
                quality_builder(row)
                if callable(quality_builder)
                else {"freshness_ok": True, "sample_size_ok": True}
            )
            observations.append(
                make_observation(
                    observation_type,
                    metric,
                    dict(row),
                    payload,
                    quality,
                )
            )
        return observations
