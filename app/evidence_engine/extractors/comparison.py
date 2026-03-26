from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.factories import make_observation
from app.evidence_engine.schemas import Observation


class ComparisonRowExtractor(ExtractorContract):
    name = "comparison_rows"
    artifact_type: ClassVar[str] = "comparison_rows"
    observation_types: ClassVar[list[str]] = ["metric_change"]
    preconditions: ClassVar[list[str]] = []
    _DEFAULT_REQUIRED_PAYLOAD_KEYS: ClassVar[tuple[str, ...]] = (
        "current_value",
        "baseline_value",
        "delta_pct",
        "current_sessions",
        "baseline_sessions",
    )

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
        required_payload_keys = tuple(
            str(value)
            for value in context.get("required_payload_keys", self._DEFAULT_REQUIRED_PAYLOAD_KEYS)
        )
        quality_builder = context.get("quality_builder")

        dimensions = context.get("dimensions")

        observations: list[Observation] = []
        for index, row in enumerate(rows):
            row_dict = dict(row)
            missing = [
                payload_name
                for payload_name in required_payload_keys
                if payload_name not in payload_fields or payload_fields[payload_name] not in row_dict
            ]
            if missing:
                missing_str = ", ".join(missing)
                raise ValueError(
                    "comparison_rows extractor requires mapped comparison fields "
                    f"at row {index}: {missing_str}"
                )
            payload = {
                payload_name: row_dict[row_field]
                for payload_name, row_field in payload_fields.items()
            }
            quality = (
                quality_builder(row_dict)
                if callable(quality_builder)
                else {"freshness_ok": True, "sample_size_ok": True}
            )
            observations.append(
                make_observation(
                    observation_type,
                    metric,
                    row_dict,
                    payload,
                    quality,
                    dimensions=dimensions,
                )
            )
        return observations
