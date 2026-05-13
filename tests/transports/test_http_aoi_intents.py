from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marivo.contracts.generated import aoi
from marivo.transports.http.sessions import router


class _FakeRuntime:
    def __init__(self) -> None:
        self.observe_payload: Any | None = None

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
