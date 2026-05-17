from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from marivo.contracts.generated import aoi
from marivo.transports.http.models import ObserveResponse, ValidateResponse
from marivo.transports.http.sessions import router


class _FakeRuntime:
    def __init__(self) -> None:
        self.observe_payload: Any | None = None
        self.detect_payload: Any | None = None
        self.test_payload: Any | None = None
        self.forecast_payload: Any | None = None
        self.validate_payload: Any | None = None

    def observe(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.observe_payload = payload
        return {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_observe_1",
                "step_type": "observe",
            },
            "artifact_id": "art_observe_1",
            "result": {
                "artifact_id": "art_observe_1",
                "result": {
                    "artifact_kind": "scalar_observation",
                    "metric": "metric.revenue",
                    "value": 42.0,
                },
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }

    def detect(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.detect_payload = payload
        return {
            "intent_type": "detect",
            "step_type": "detect",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_detect_1",
                "step_type": "detect",
            },
            "artifact_id": "art_detect_1",
            "result": {
                "artifact_id": "art_detect_1",
                "result": {
                    "items": [],
                },
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }

    def test(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.test_payload = payload
        return {
            "intent_type": "test",
            "step_type": "test",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_test_1",
                "step_type": "test",
            },
            "artifact_id": "art_test_1",
            "result": {
                "artifact_id": "art_test_1",
                "result": {
                    "statistic": 2.1,
                    "p_value": 0.04,
                    "decision": {"reject_null": True},
                    "assumption_notes": [],
                },
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }

    def forecast(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.forecast_payload = payload
        return {
            "intent_type": "forecast",
            "step_type": "forecast",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_forecast_1",
                "step_type": "forecast",
            },
            "artifact_id": "art_forecast_1",
            "result": {
                "artifact_id": "art_forecast_1",
                "result": {
                    "points": [],
                },
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }

    def validate(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.validate_payload = payload
        return {
            "intent_type": "validate",
            "step_type": "validate",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_validate_1",
                "step_type": "validate",
            },
            "artifact_id": "art_validate_1",
            "result": {
                "bundle_type": "validation_bundle",
                "aoi_artifacts": [
                    {
                        "artifact_id": "art_test_1",
                        "result": {
                            "statistic": 2.1,
                            "p_value": 0.04,
                            "decision": {"reject_null": True},
                            "assumption_notes": [],
                        },
                    }
                ],
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }


def _client(runtime: _FakeRuntime) -> TestClient:
    app = FastAPI()
    app.state.services = type("Services", (), {"runtime": runtime})()
    app.include_router(router)
    return TestClient(app)


def _step_ref(step_type: str) -> dict[str, str]:
    return {
        "session_id": "sess_1",
        "step_id": f"step_{step_type}_1",
        "step_type": step_type,
    }


def test_atomic_response_model_accepts_aoi_artifact_wrapper() -> None:
    response = ObserveResponse.model_validate(
        {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": _step_ref("observe"),
            "artifact_id": "art_observe_1",
            "result": {
                "artifact_id": "art_observe_1",
                "result": {"value": 42.0},
            },
        }
    )

    assert response.result.result == aoi.ScalarObservationResult(value=42.0)


def test_atomic_response_model_rejects_flat_or_rich_runtime_fields() -> None:
    with pytest.raises(ValidationError):
        ObserveResponse.model_validate(
            {
                "intent_type": "observe",
                "step_type": "observe",
                "step_ref": _step_ref("observe"),
                "artifact_id": "art_observe_1",
                "result": {
                    "artifact_id": "art_observe_1",
                    "result": {
                        "artifact_kind": "scalar_observation",
                        "metric": "metric.revenue",
                        "value": 42.0,
                    },
                },
            }
        )


def test_derived_response_model_requires_typed_aoi_artifacts() -> None:
    response = ValidateResponse.model_validate(
        {
            "intent_type": "validate",
            "step_type": "validate",
            "step_ref": _step_ref("validate"),
            "artifact_id": "art_validate_1",
            "result": {
                "bundle_type": "validation_bundle",
                "aoi_artifacts": [
                    {
                        "artifact_id": "art_test_1",
                        "result": {
                            "statistic": 2.1,
                            "p_value": 0.04,
                            "decision": {"reject_null": True},
                            "assumption_notes": [],
                        },
                    }
                ],
            },
        }
    )

    assert response.result.aoi_artifacts[0].artifact_id == "art_test_1"

    with pytest.raises(ValidationError):
        ValidateResponse.model_validate(
            {
                "intent_type": "validate",
                "step_type": "validate",
                "step_ref": _step_ref("validate"),
                "artifact_id": "art_validate_1",
                "result": {
                    "bundle_type": "validation_bundle",
                    "aoi_artifacts": [{"artifact_id": "art_bad", "result": {"metric": "x"}}],
                },
            }
        )


def test_observe_accepts_aoi_request_and_returns_execution_envelope() -> None:
    runtime = _FakeRuntime()
    client = _client(runtime)

    response = client.post(
        "/sessions/sess_1/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
        },
    )

    assert response.status_code == 200
    assert isinstance(runtime.observe_payload, aoi.Observe1)
    body = response.json()
    assert body["intent_type"] == "observe"
    assert body["artifact_id"] == "art_observe_1"
    assert body["result"] == {
        "artifact_id": "art_observe_1",
        "result": {"value": 42.0},
    }
    assert "value" not in body


def test_observe_accepts_time_series_aoi_request() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "granularity": "quarter",
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.observe_payload, aoi.Observe2)
    assert runtime.observe_payload.granularity == "quarter"


def test_observe_accepts_segmented_aoi_request() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "dimensions": ["region"],
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.observe_payload, aoi.Observe3)
    assert runtime.observe_payload.dimensions is not None
    assert [dimension.root for dimension in runtime.observe_payload.dimensions] == ["region"]


def test_detect_accepts_aoi_request_with_strategy_and_dimension() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/detect",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "granularity": "day",
            "dimension": "region",
            "strategy": "period_shift",
            "sensitivity": "balanced",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.detect_payload, aoi.Detect)
    assert runtime.detect_payload.dimension == "region"
    assert runtime.detect_payload.strategy == "period_shift"
    assert runtime.detect_payload.sensitivity == "balanced"


def test_detect_rejects_removed_split_by_profile_fields() -> None:
    response = _client(_FakeRuntime()).post(
        "/sessions/sess_1/intents/detect",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "granularity": "day",
            "strategy": "point_anomaly",
            "split_by": ["region"],
            "profile": "auto",
        },
    )

    assert response.status_code == 422


def test_detect_requires_strategy() -> None:
    response = _client(_FakeRuntime()).post(
        "/sessions/sess_1/intents/detect",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "granularity": "day",
        },
    )

    assert response.status_code == 422


def _valid_test_request() -> dict[str, Any]:
    return {
        "metric": "metric.revenue",
        "left": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "right": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/sessions/sess_1/intents/observe",
            {
                "metric": "metric.revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                },
                "filter": None,
            },
        ),
        (
            "/sessions/sess_1/intents/detect",
            {
                "metric": "metric.revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                },
                "granularity": "day",
                "filter": None,
                "strategy": "point_anomaly",
            },
        ),
        (
            "/sessions/sess_1/intents/forecast",
            {"source_artifact_id": "art_timeseries", "horizon": 14, "profile": "auto"},
        ),
    ],
)
def test_aoi_request_rejects_removed_or_null_optional_fields(
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    response = _client(_FakeRuntime()).post(endpoint, json=payload)

    assert response.status_code == 422


def test_forecast_accepts_request_without_profile() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/forecast",
        json={"source_artifact_id": "art_timeseries", "horizon": 14},
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.forecast_payload, aoi.Forecast)
    assert runtime.forecast_payload.source_artifact_id == "art_timeseries"
    assert runtime.forecast_payload.horizon == 14
    assert "profile" not in runtime.forecast_payload.model_dump()


def test_test_accepts_aoi_request_and_returns_execution_envelope() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/test",
        json=_valid_test_request(),
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.test_payload, aoi.Test)
    assert runtime.test_payload.kind == "numeric"
    assert runtime.test_payload.hypothesis.family == "two_sample_mean"
    body = response.json()
    assert body["intent_type"] == "test"
    assert body["artifact_id"] == "art_test_1"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("method",), "welch_t"),
        (("hypothesis", "label"), "legacy label"),
        (("hypothesis", "alpha"), 0.05),
    ],
)
def test_test_rejects_non_contract_fields(path: tuple[str, ...], value: Any) -> None:
    payload = _valid_test_request()
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 422


def test_validate_accepts_aoi_request_and_returns_typed_bundle() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/validate",
        json={
            "metric": "metric.revenue",
            "left": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                }
            },
            "right": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-08T00:00:00Z",
                    "end": "2026-01-15T00:00:00Z",
                }
            },
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.validate_payload, aoi.Validate)
    body = response.json()
    assert body["intent_type"] == "validate"
    assert body["result"]["bundle_type"] == "validation_bundle"
    assert body["result"]["aoi_artifacts"][0]["result"]["p_value"] == 0.04
