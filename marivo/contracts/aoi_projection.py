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


def _forecast_point_items(payload: dict[str, Any]) -> list[tuple[dict[str, str], dict[str, Any]]]:
    forecast = payload.get("forecast") or []
    items: list[tuple[dict[str, str], dict[str, Any]]] = []
    for entry in forecast:
        if isinstance(entry, dict) and isinstance(entry.get("points"), list):
            keys = _string_keys(entry.get("keys"))
            for point in entry.get("points") or []:
                if isinstance(point, dict):
                    items.append((keys, point))
        elif isinstance(entry, dict):
            items.append(({}, entry))
    return items


def _first_point_value(entry: dict[str, Any], field: str) -> Any:
    """Read a field value from the first point in a series entry."""
    points = entry.get("points") or []
    if not points:
        return None
    return points[0].get(field)


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


def _project_decompose_attribution_frame(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_family") == "attribution_frame":
        return aoi.AttributionFrameArtifact.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )
    raise ValueError("decompose AOI projection requires an attribution_frame artifact")


def project_aoi_artifact_result(intent_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if intent_type == "observe":
        if payload.get("artifact_family") == "metric_frame":
            return artifact_to_envelope_result(validate_aoi_artifact(payload))
        raise ValueError("observe AOI projection requires a metric_frame artifact")

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
        if payload.get("artifact_family") == "candidate_set":
            return artifact_to_envelope_result(validate_aoi_artifact(payload))
        raise ValueError("detect AOI projection requires a candidate_set artifact")

    if intent_type == "forecast":
        return aoi.ForecastSeriesResult(
            points=[
                aoi.Point(
                    keys=keys or None,
                    bucket_start=_as_aoi_datetime(_point_start(point)),
                    value=float(point.get("point_forecast") or 0.0),
                    ci_low=(point.get("prediction_interval") or {}).get("lower")
                    if point.get("prediction_interval")
                    else None,
                    ci_high=(point.get("prediction_interval") or {}).get("upper")
                    if point.get("prediction_interval")
                    else None,
                )
                for keys, point in _forecast_point_items(payload)
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
    if (
        intent_type == "detect"
        and isinstance(raw, dict)
        and raw.get("artifact_family") == "candidate_set"
    ):
        return artifact_to_envelope_result(validate_aoi_artifact(raw))
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
        return project_aoi_artifact_result("observe", projected_payload)
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

    if payload.get("artifact_family") == "candidate_set":
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
