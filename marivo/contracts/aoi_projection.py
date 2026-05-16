from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact
from marivo.contracts.generated import aoi

_ObserveResult = (
    aoi.ScalarObservationResult | aoi.TimeSeriesObservationResult | aoi.SegmentedObservationResult
)

_CompareResult = aoi.ScalarDeltaResult | aoi.TimeSeriesDeltaResult | aoi.SegmentedDeltaResult


def _as_aoi_datetime(value: Any) -> datetime:
    raw = str(value or "").strip() or datetime.now(UTC).isoformat()
    normalized = raw.replace("Z", "+00:00").replace(" ", "T")
    if "T" not in normalized:
        normalized = f"{normalized}T00:00:00+00:00"
    if "+" not in normalized[10:] and "-" not in normalized[10:]:
        normalized = f"{normalized}+00:00"
    return datetime.fromisoformat(normalized)


def _as_aoi_time_scope(value: Any) -> aoi.TimeScope | None:
    if not isinstance(value, dict) or value.get("start") is None or value.get("end") is None:
        return None
    return aoi.TimeScope(
        field=str(value.get("field") or "time"),
        start=_as_aoi_datetime(value.get("start")),
        end=_as_aoi_datetime(value.get("end")),
    )


def _string_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _point_start(item: dict[str, Any]) -> Any:
    window = item.get("window")
    if isinstance(window, dict):
        return window.get("start")
    return item.get("bucket_start") or item.get("start")


def project_aoi_artifact_result(intent_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if intent_type == "observe":
        observation_type = payload.get("observation_type")
        if observation_type == "time_series":
            observe_result: _ObserveResult = aoi.TimeSeriesObservationResult(
                points=[
                    aoi.TimeSeriesPoint(
                        bucket_start=_as_aoi_datetime(_point_start(point)),
                        value=point.get("value"),
                    )
                    for point in payload.get("series") or []
                ]
            )
        elif observation_type == "segmented":
            observe_result = aoi.SegmentedObservationResult(
                rows=[
                    aoi.SegmentedObservationRow(
                        item_id=f"segment_{idx}",
                        keys=_string_keys(segment.get("keys")),
                        value=segment.get("value"),
                    )
                    for idx, segment in enumerate(payload.get("segments") or [])
                ]
            )
        else:
            observe_result = aoi.ScalarObservationResult(value=payload.get("value"))
        return observe_result.model_dump(mode="json")

    if intent_type == "compare":
        comparison_type = payload.get("comparison_type")
        if comparison_type is None and {"left_value", "right_value", "absolute_delta"} & set(
            payload
        ):
            comparison_type = "scalar_delta"
        matched_time_scope = _as_aoi_time_scope(
            (payload.get("analytical_metadata") or {}).get("matched_time_scope")
        )
        if comparison_type == "time_series_delta":
            compare_result: _CompareResult = aoi.TimeSeriesDeltaResult(
                points=[
                    aoi.DeltaPoint(
                        bucket_start=_as_aoi_datetime(_point_start(row)),
                        left_value=row.get("left_value"),
                        right_value=row.get("right_value"),
                        delta=row.get("absolute_delta"),
                    )
                    for row in payload.get("rows") or []
                ],
                matched_time_scope=matched_time_scope,
            )
        elif comparison_type == "segmented_delta":
            compare_result = aoi.SegmentedDeltaResult(
                rows=[
                    aoi.SegmentedDeltaRow(
                        item_id=f"segment_delta_{idx}",
                        keys=_string_keys(row.get("keys")),
                        left_value=row.get("left_value"),
                        right_value=row.get("right_value"),
                        delta=row.get("absolute_delta"),
                    )
                    for idx, row in enumerate(payload.get("rows") or [])
                ],
                matched_time_scope=matched_time_scope,
            )
        else:
            compare_result = aoi.ScalarDeltaResult(
                left_value=payload.get("left_value"),
                right_value=payload.get("right_value"),
                delta=payload.get("absolute_delta"),
                matched_time_scope=matched_time_scope,
            )
        return compare_result.model_dump(mode="json")

    if intent_type == "decompose":
        return aoi.DeltaDecompositionResult(
            items=[
                aoi.DecompositionItem(
                    item_id=str(
                        row.get("item_id")
                        or row.get("key")
                        or row.get("dimension_value")
                        or f"item_{idx}"
                    ),
                    key=row.get("key") if "key" in row else row.get("dimension_value"),
                    contribution=row.get("absolute_contribution") or 0.0,
                    share=row.get("contribution_share") or 0.0,
                )
                for idx, row in enumerate(payload.get("rows") or [])
            ]
        ).model_dump(mode="json")

    if intent_type == "correlate":
        statistic = payload.get("statistic") or {}
        return aoi.AssociationResult(
            coefficient=float(statistic.get("coefficient") or 0.0),
            p_value=statistic.get("p_value"),
            n_pairs=int(statistic.get("n_pairs") or 0),
            matched_time_scope=_as_aoi_time_scope(
                (payload.get("analytical_metadata") or {}).get("matched_time_scope")
            ),
        ).model_dump(mode="json")

    if intent_type == "detect":
        return aoi.AnomalyCandidatesResult(
            items=[
                aoi.AnomalyCandidate(
                    item_id=str(
                        ((candidate.get("candidate_ref") or {}).get("item_ref") or {}).get("key")
                        or f"candidate_{idx}"
                    ),
                    bucket_start=_as_aoi_datetime((candidate.get("window") or {}).get("start")),
                    value=float(candidate.get("observed_value") or 0.0),
                    score=float(candidate.get("candidate_score") or 0.0),
                    series_keys=_string_keys(candidate.get("slice"))
                    if candidate.get("slice") is not None
                    else None,
                )
                for idx, candidate in enumerate(payload.get("candidates") or [])
            ]
        ).model_dump(mode="json")

    if intent_type == "forecast":
        return aoi.ForecastSeriesResult(
            points=[
                aoi.Point(
                    bucket_start=_as_aoi_datetime(_point_start(point)),
                    value=float(point.get("point_forecast") or 0.0),
                    ci_low=(point.get("prediction_interval") or {}).get("lower")
                    if point.get("prediction_interval")
                    else None,
                    ci_high=(point.get("prediction_interval") or {}).get("upper")
                    if point.get("prediction_interval")
                    else None,
                )
                for point in payload.get("forecast") or []
            ]
        ).model_dump(mode="json")

    if intent_type == "test":
        decision_raw = payload.get("decision")
        reject_null = decision_raw.get("reject_null") if isinstance(decision_raw, dict) else None
        decision = aoi.Decision(reject_null=reject_null if isinstance(reject_null, bool) else None)
        return aoi.HypothesisTestResult(
            statistic=float(payload.get("statistic") or 0.0),
            p_value=float(payload.get("p_value") or 0.0),
            decision=decision,
            assumption_notes=payload.get("assumption_notes") or [],
        ).model_dump(mode="json")

    raise ValueError(f"Unsupported AOI artifact projection for intent_type={intent_type!r}")


def project_aoi_artifact(
    intent_type: str, artifact_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    raw = payload.get("result")
    if isinstance(raw, dict) and raw.get("artifact_id") and ("result" in raw or "failure" in raw):
        try:
            return artifact_to_envelope_result(validate_aoi_artifact(raw))
        except ValidationError:
            raw_result = raw.get("result")
            if isinstance(raw_result, dict):
                payload = raw_result
    projected_payload = raw if intent_type == "detect" and isinstance(raw, dict) else payload
    return artifact_to_envelope_result(
        validate_aoi_artifact(
            {
                "artifact_id": artifact_id,
                "result": project_aoi_artifact_result(intent_type, projected_payload),
            }
        )
    )


def _infer_intent_type(payload: dict[str, Any]) -> str:
    artifact_type = payload.get("artifact_type")
    observation_type = payload.get("observation_type")
    comparison_type = payload.get("comparison_type")

    if artifact_type == "anomaly_candidates" or "candidates" in payload:
        return "detect"
    if artifact_type == "compare_artifact" or comparison_type is not None:
        return "compare"
    if artifact_type == "delta_decomposition" or "contribution_summary" in payload:
        return "decompose"
    if artifact_type == "pairwise_time_series_association":
        return "correlate"
    if artifact_type == "hypothesis_test" or "p_value" in payload:
        return "test"
    if artifact_type == "forecast_series" or observation_type == "forecast_series":
        return "forecast"
    if (
        observation_type is not None
        or "value" in payload
        or "series" in payload
        or "segments" in payload
    ):
        return "observe"
    raise ValueError("Cannot infer AOI intent type for derived artifact")


def project_aoi_artifact_from_any(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return artifact_to_envelope_result(validate_aoi_artifact(value))
    except ValidationError:
        pass

    raw_result = value.get("result")
    projection_payload = raw_result if isinstance(raw_result, dict) else value
    artifact_id = value.get("artifact_id") or projection_payload.get("artifact_id")
    if artifact_id is None:
        raise ValueError("AOI artifact projection requires artifact_id")
    intent_type = _infer_intent_type(projection_payload)
    return project_aoi_artifact(intent_type, str(artifact_id), projection_payload)
