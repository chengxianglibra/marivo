"""Lower generated AOI request models into runner parameter dictionaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, RootModel

from marivo.contracts.aoi_runtime import (
    AoiAtomicRequest,
    AoiDerivedRequest,
    assert_derived_request_matches_intent,
    assert_request_matches_intent,
)
from marivo.contracts.generated import aoi


def lower_aoi_request(intent_type: str, request: AoiAtomicRequest) -> dict[str, Any]:
    assert_request_matches_intent(intent_type, request)

    if isinstance(request, (aoi.Observe1, aoi.Observe2, aoi.Observe3)):
        return _lower_observe(request)
    if isinstance(request, aoi.Compare):
        return {
            "left_artifact_id": request.left_artifact_id,
            "right_artifact_id": request.right_artifact_id,
            "compare_type": request.compare_type,
        }
    if isinstance(request, aoi.Decompose):
        return {
            "compare_artifact_id": request.compare_artifact_id,
            "dimension": request.dimension,
            "limit": request.limit,
        }
    if isinstance(request, aoi.Correlate):
        return {
            "left_artifact_id": request.left_artifact_id,
            "right_artifact_id": request.right_artifact_id,
            "method": request.method,
        }
    if isinstance(request, aoi.Detect):
        return {
            "metric": request.metric,
            "time_scope": _dump_time_scope(request.time_scope),
            "granularity": request.granularity,
            "filter": _dump_model(request.filter) if request.filter is not None else None,
            "dimension": _dump_model(request.dimension) if request.dimension is not None else None,
            "strategy": request.strategy,
            "sensitivity": request.sensitivity,
            "limit": _dump_model(request.limit) if request.limit is not None else None,
        }
    if isinstance(request, (aoi.Test, aoi.Forecast)):
        return request.model_dump(exclude_none=True)
    raise TypeError(f"Unsupported AOI request type: {type(request).__name__}")


def lower_aoi_derived_request(intent_type: str, request: AoiDerivedRequest) -> dict[str, Any]:
    assert_derived_request_matches_intent(intent_type, request)

    if isinstance(request, aoi.Validate):
        return {
            "metric": request.metric,
            "left": _dump_slice(request.left),
            "right": _dump_slice(request.right),
            "hypothesis": request.hypothesis.model_dump(exclude_none=True),
        }
    raise TypeError(f"Unsupported AOI derived request type: {type(request).__name__}")


def _lower_observe(
    request: aoi.Observe1 | aoi.Observe2 | aoi.Observe3,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "metric": request.metric,
        "time_scope": _dump_time_scope(request.time_scope),
        "filter": _dump_model(request.filter) if request.filter is not None else None,
    }
    if request.granularity is not None:
        params["granularity"] = request.granularity
    if request.dimensions is not None:
        params["dimensions"] = _dump_model(request.dimensions)
    return params


def _dump_time_scope(time_scope: aoi.TimeScope) -> dict[str, Any]:
    return {
        "field": time_scope.field,
        "start": _iso_z(time_scope.start),
        "end": _iso_z(time_scope.end),
    }


def _dump_slice(slice_ref: aoi.Slice) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time_scope": _dump_time_scope(slice_ref.time_scope),
    }
    if slice_ref.filter is not None:
        payload["filter"] = _dump_model(slice_ref.filter)
    return payload


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _dump_model(value: Any) -> Any:
    if isinstance(value, RootModel):
        return _dump_model(value.root)
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_dump_model(item) for item in value]
    return value
