from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact
from marivo.contracts.generated import aoi


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


def _first_point_value(entry: dict[str, Any], field: str) -> Any:
    """Read a field value from the first point in a series entry."""
    points = entry.get("points") or []
    if not points:
        return None
    return points[0].get(field)


def _metric_frame_window(point: dict[str, Any]) -> dict[str, Any] | None:
    raw_window = point.get("window")
    if isinstance(raw_window, dict) and raw_window.get("start") and raw_window.get("end"):
        return {
            "start": _as_aoi_datetime(raw_window.get("start")),
            "end": _as_aoi_datetime(raw_window.get("end")),
        }
    start = _point_start(point)
    end = point.get("bucket_end") or point.get("end")
    if start is None or end is None:
        return None
    return {"start": _as_aoi_datetime(start), "end": _as_aoi_datetime(end)}


def _metric_frame_point(point: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {"value": point.get("value")}
    window = _metric_frame_window(point)
    if window is not None:
        value["window"] = window
    return value


def _delta_frame_window(point: dict[str, Any]) -> dict[str, Any] | None:
    raw_window = point.get("window")
    if isinstance(raw_window, dict) and raw_window.get("start") and raw_window.get("end"):
        return {
            "start": _as_aoi_datetime(raw_window.get("start")),
            "end": _as_aoi_datetime(raw_window.get("end")),
        }
    return None


def _delta_frame_point(point: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "current_value": point.get("current_value"),
        "baseline_value": point.get("baseline_value"),
        "delta_abs": point.get("delta_abs"),
        "delta_pct": point.get("delta_pct"),
        "direction": point.get("direction") or "undefined",
    }
    window = _delta_frame_window(point)
    if window is not None:
        value["window"] = window
    if "presence" in point:
        value["presence"] = point.get("presence")
    return value


def _delta_frame_subject(payload: dict[str, Any]) -> dict[str, Any]:
    subject_raw = payload.get("subject")
    subject = subject_raw if isinstance(subject_raw, dict) else {}

    def side(name: str) -> dict[str, Any]:
        side_raw = subject.get(name)
        side_payload = side_raw if isinstance(side_raw, dict) else {}
        time_scope = _as_aoi_time_scope(side_payload.get("time_scope"))
        return {
            "time_scope": time_scope.model_dump(mode="json") if time_scope is not None else None,
            "scope": side_payload.get("scope")
            if isinstance(side_payload.get("scope"), dict)
            else {},
        }

    return {
        "kind": "comparison",
        "metric_ref": str(subject.get("metric_ref") or payload.get("metric_ref") or ""),
        "current": side("current"),
        "baseline": side("baseline"),
    }


def _project_delta_frame_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    payload_raw = payload.get("payload")
    payload_body: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
    series = [
        {
            "keys": _string_keys(entry.get("keys")),
            "points": [
                _delta_frame_point(point)
                for point in entry.get("points") or []
                if isinstance(point, dict)
            ],
        }
        for entry in payload_body.get("series") or []
        if isinstance(entry, dict)
    ]
    scope = payload_body.get("scope") if isinstance(payload_body.get("scope"), dict) else {}
    return {
        "artifact_id": str(payload.get("artifact_id") or "artifact_compare"),
        "artifact_family": "delta_frame",
        "shape": payload.get("shape"),
        "subject": _delta_frame_subject(payload),
        "axes": payload.get("axes") or [],
        "measures": payload.get("measures") or [],
        "payload": {"series": series, "scope": scope},
    }


def _default_time_scope() -> dict[str, str]:
    return {
        "field": "time",
        "start": "1970-01-01T00:00:00Z",
        "end": "1970-01-02T00:00:00Z",
    }


def _attribution_time_scope(value: Any) -> Any:
    if isinstance(value, dict) and value.get("start") is not None and value.get("end") is not None:
        return {
            "field": str(value.get("field") or "time"),
            "start": value.get("start"),
            "end": value.get("end"),
        }
    return _default_time_scope()


def _attribution_scope(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _attribution_subject(payload: dict[str, Any]) -> dict[str, Any]:
    subject = payload.get("subject")
    if isinstance(subject, dict):
        return subject
    resolved_scopes_raw = payload.get("resolved_scopes")
    resolved_scopes: dict[str, Any] = (
        resolved_scopes_raw if isinstance(resolved_scopes_raw, dict) else {}
    )
    metric_ref = str(payload.get("metric_ref") or payload.get("metric") or "unknown")
    if not metric_ref.startswith("metric."):
        metric_ref = f"metric.{metric_ref}"
    return {
        "kind": "comparison",
        "metric_ref": metric_ref,
        "current": {
            "time_scope": _attribution_time_scope(payload.get("current_time_scope")),
            "scope": _attribution_scope(resolved_scopes.get("current")),
        },
        "baseline": {
            "time_scope": _attribution_time_scope(payload.get("baseline_time_scope")),
            "scope": _attribution_scope(resolved_scopes.get("baseline")),
        },
    }


def _attribution_lineage(payload: dict[str, Any]) -> dict[str, Any]:
    lineage = payload.get("lineage")
    if isinstance(lineage, dict):
        return lineage
    compare_ref_raw = payload.get("compare_ref")
    compare_ref: dict[str, Any] = compare_ref_raw if isinstance(compare_ref_raw, dict) else {}
    return {
        "operation": "decompose",
        "source_artifact_ids": [str(compare_ref.get("artifact_id") or "unknown")],
    }


def _attribution_series_from_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    dimension = str(payload.get("dimension") or "dimension")
    series = []
    for idx, row in enumerate(payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        key = row.get("key") if "key" in row else row.get("dimension_value")
        point = {
            "contribution_abs": row.get("contribution_abs")
            if "contribution_abs" in row
            else row.get("absolute_contribution", 0.0),
            "contribution_pct": row.get("contribution_pct")
            if "contribution_pct" in row
            else row.get("contribution_share", 0.0),
            "rank": row.get("rank") or idx + 1,
        }
        if "current_value" in row:
            point["current_value"] = row.get("current_value")
        if "baseline_value" in row:
            point["baseline_value"] = row.get("baseline_value")
        if "presence" in row:
            point["presence"] = row.get("presence")
        series.append({"keys": {dimension: str(key or f"item_{idx}")}, "points": [point]})
    return series


def _project_decompose_attribution_frame(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_family") == "attribution_frame":
        return aoi.AttributionFrameArtifact.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )
    raise ValueError("decompose AOI projection requires an attribution_frame artifact")


def _metric_frame_subject(payload: dict[str, Any]) -> aoi.MetricFrameSubject:
    raw_time_scope = (
        payload.get("time_scope")
        or (payload.get("analytical_metadata") or {}).get("time_scope")
        or (payload.get("analytical_metadata") or {}).get("matched_time_scope")
    )
    time_scope = _as_aoi_time_scope(raw_time_scope) or aoi.TimeScope(
        field="time",
        start=_as_aoi_datetime("1970-01-01T00:00:00Z"),
        end=_as_aoi_datetime("1970-01-02T00:00:00Z"),
    )
    scope = payload.get("scope")
    return aoi.MetricFrameSubject.model_validate(
        {
            "kind": "metric",
            "metric_ref": str(
                payload.get("metric_ref") or payload.get("metric") or "metric.unknown"
            ),
            "time_scope": time_scope,
            "scope": scope if isinstance(scope, dict) else {},
        }
    )


def _project_observe_metric_frame(artifact_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    observation_type = payload.get("observation_type")
    granularity = payload.get("granularity") or payload.get("grain") or "day"
    dimensions = payload.get("dimensions") or payload.get("dimension")
    if isinstance(dimensions, str):
        dimension_names = [dimensions]
    elif isinstance(dimensions, list):
        dimension_names = [str(dimension) for dimension in dimensions]
    else:
        dimension_names = []

    if observation_type == "time_series" or payload.get("series"):
        shape = "panel" if dimension_names else "time_series"
        axes: list[dict[str, Any]] = [{"kind": "time", "grain": granularity}]
        axes.extend({"kind": "dimension", "name": name} for name in dimension_names)
        raw_series = payload.get("series") or []
        series = [
            {
                "keys": _string_keys(entry.get("keys")),
                "points": [_metric_frame_point(point) for point in entry.get("points") or []],
            }
            if isinstance(entry, dict) and isinstance(entry.get("points"), list)
            else {"keys": {}, "points": [_metric_frame_point(entry)]}
            for entry in raw_series
            if isinstance(entry, dict)
        ]
    elif observation_type == "segmented" or payload.get("segments"):
        shape = "segmented"
        axes = [{"kind": "dimension", "name": name} for name in (dimension_names or ["segment"])]
        series = [
            {
                "keys": _string_keys(segment.get("keys")),
                "points": [{"value": segment.get("value")}],
            }
            for segment in payload.get("segments") or []
            if isinstance(segment, dict)
        ]
    else:
        shape = "scalar"
        axes = []
        series = [{"keys": {}, "points": [{"value": payload.get("value")}]}]

    artifact = aoi.MetricFrameArtifact.model_validate(
        {
            "artifact_id": artifact_id,
            "artifact_family": "metric_frame",
            "shape": shape,
            "subject": _metric_frame_subject(payload),
            "axes": axes,
            "measures": [{"id": "value", "value_type": "number", "nullable": True}],
            "payload": {"series": series},
        }
    )
    return artifact.model_dump(mode="json", exclude_none=True)


def project_aoi_artifact_result(intent_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if intent_type == "observe":
        artifact_id = str(payload.get("artifact_id") or "artifact_observe")
        return _project_observe_metric_frame(artifact_id, payload)

    if intent_type == "compare":
        if payload.get("artifact_family") == "delta_frame":
            return artifact_to_envelope_result(
                validate_aoi_artifact(_project_delta_frame_artifact(payload))
            )
        raise ValueError("compare AOI projection requires a delta_frame artifact")

    if intent_type == "decompose":
        return _project_decompose_attribution_frame(payload)

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
                    value=float(candidate.get("current_value") or 0.0),
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
    if (
        intent_type == "observe"
        and isinstance(raw, dict)
        and raw.get("artifact_family") == "metric_frame"
    ):
        return artifact_to_envelope_result(validate_aoi_artifact(raw))
    if (
        intent_type == "compare"
        and isinstance(raw, dict)
        and raw.get("artifact_family") == "delta_frame"
    ):
        return project_aoi_artifact_result("compare", raw)
    if isinstance(raw, dict) and raw.get("artifact_id") and ("result" in raw or "failure" in raw):
        try:
            return artifact_to_envelope_result(validate_aoi_artifact(raw))
        except ValidationError:
            raw_result = raw.get("result")
            if isinstance(raw_result, dict):
                raw_result.setdefault("artifact_id", raw.get("artifact_id"))
                payload = raw_result
    projected_payload = raw if intent_type == "detect" and isinstance(raw, dict) else payload
    if intent_type == "observe":
        projected_payload = payload
        if isinstance(raw, dict) and not (
            raw.get("artifact_id") and ("result" in raw or "failure" in raw)
        ):
            projected_payload = raw
        projected_payload.setdefault("artifact_id", artifact_id)
        return artifact_to_envelope_result(
            validate_aoi_artifact(_project_observe_metric_frame(artifact_id, projected_payload))
        )
    if intent_type == "decompose":
        if isinstance(raw, dict) and raw.get("artifact_id") and "failure" in raw:
            return artifact_to_envelope_result(validate_aoi_artifact(raw))
        projected_payload = payload
        if isinstance(raw, dict):
            raw_result = raw.get("result")
            if isinstance(raw_result, dict):
                projected_payload = raw_result
            elif not (raw.get("artifact_id") and ("result" in raw or "failure" in raw)):
                projected_payload = raw
        projected_payload.setdefault("artifact_id", artifact_id)
        return project_aoi_artifact_result("decompose", projected_payload)
    if intent_type == "compare" and projected_payload.get("artifact_family") == "delta_frame":
        projected_payload.setdefault("artifact_id", artifact_id)
        return project_aoi_artifact_result("compare", projected_payload)
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

    if artifact_type == "anomaly_candidates" or "candidates" in payload:
        return "detect"
    if artifact_type == "delta_frame" or payload.get("artifact_family") == "delta_frame":
        return "compare"
    if payload.get("shape") in (
        "scalar_delta",
        "time_series_delta",
        "segmented_delta",
        "panel_delta",
    ):
        return "compare"
    if payload.get("artifact_family") == "attribution_frame":
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
