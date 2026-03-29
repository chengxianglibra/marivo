from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from uuid import uuid4

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.schemas import Observation


class CorrelationObservationExtractor(ExtractorContract):
    """Extract observations from correlate_metrics results.

    Context keys:
        left_metric (str): label for series A metric.
        right_metric (str): label for series B metric.
        join_on (str): shared key column used for alignment.

    The subject.metric is set to right_metric (the outcome/primary metric),
    with related_metric carrying left_metric.
    """

    name = "correlation_observations"
    artifact_type: ClassVar[str] = "correlation"
    observation_types: ClassVar[list[str]] = ["correlation_result"]
    preconditions: ClassVar[list[str]] = []

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        left_metric = str(context.get("left_metric", "left"))
        right_metric = str(context.get("right_metric", "right"))
        join_on = str(context.get("join_on", ""))
        left_scope_slice = dict(context.get("left_scope_slice", {}))
        right_scope_slice = dict(context.get("right_scope_slice", {}))

        observations: list[Observation] = []
        for row in rows:
            row_dict = dict(row)
            rho = float(row_dict.get("rho", 0.0))
            p_value = float(row_dict.get("p_value", 1.0))
            n = int(row_dict.get("n", 0))
            method = str(row_dict.get("method", "spearman"))
            observed_window = row_dict.get("observed_window")

            payload: dict[str, Any] = {
                "rho": rho,
                "p_value": p_value,
                "n": n,
                "method": method,
                "left_metric": left_metric,
                "right_metric": right_metric,
            }
            if join_on:
                payload["join_on"] = join_on

            obs: Observation = {
                "observation_id": f"obs_{uuid4().hex[:12]}",
                "type": "correlation_result",
                "subject": {
                    "metric": right_metric,
                    "slice": right_scope_slice,
                    "related_metric": left_metric,
                    "left_slice": left_scope_slice,
                },
                "payload": payload,
                "significance": {
                    "significant": p_value < 0.05,
                    "strong": abs(rho) >= 0.7,
                },
                "quality": {
                    "freshness_ok": True,
                    "sample_size_ok": n >= 3,
                },
            }
            if observed_window is not None:
                obs["observed_window"] = observed_window

            observations.append(obs)

        return observations
