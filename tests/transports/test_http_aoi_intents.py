from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marivo.contracts.generated import aoi
from marivo.transports.http.sessions import router


class _FakeRuntime:
    def __init__(self) -> None:
        self.observe_payload: Any | None = None
        self.detect_payload: Any | None = None
        self.test_payload: Any | None = None

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


def _client(runtime: _FakeRuntime) -> TestClient:
    app = FastAPI()
    app.state.services = type("Services", (), {"runtime": runtime})()
    app.include_router(router)
    return TestClient(app)


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
            "filter": None,
            "granularity": None,
            "dimensions": None,
        },
    )

    assert response.status_code == 200
    assert isinstance(runtime.observe_payload, aoi.Observe1)
    body = response.json()
    assert body["intent_type"] == "observe"
    assert body["artifact_id"] == "art_observe_1"
    assert body["result"] == {
        "artifact_id": "art_observe_1",
        "result": {
            "artifact_kind": "scalar_observation",
            "metric": "metric.revenue",
            "value": 42.0,
        },
    }
    assert "value" not in body


def test_observe_rejects_legacy_time_scope_shape() -> None:
    response = _client(_FakeRuntime()).post(
        "/sessions/sess_1/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "kind": "range",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "filter": None,
        },
    )

    assert response.status_code == 422


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
            "filter": None,
            "dimension": "region",
            "strategy": "period_shift",
            "sensitivity": "balanced",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.detect_payload, aoi.Detect)
    assert runtime.detect_payload.dimension is not None
    assert runtime.detect_payload.dimension.root == "region"
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
            "filter": None,
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
            "filter": None,
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
            },
            "filter": None,
        },
        "right": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            },
            "filter": None,
        },
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


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


def test_test_rejects_method_parameter() -> None:
    payload = _valid_test_request()
    payload["method"] = "welch_t"

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 422


def test_test_rejects_hypothesis_label() -> None:
    payload = _valid_test_request()
    payload["hypothesis"]["label"] = "legacy label"

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 422


def test_test_rejects_hypothesis_alpha() -> None:
    payload = _valid_test_request()
    payload["hypothesis"]["alpha"] = 0.05

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 422
