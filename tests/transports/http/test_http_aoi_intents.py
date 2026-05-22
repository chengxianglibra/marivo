from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from marivo.contracts.generated import aoi
from marivo.transports.http.models import (
    DecomposeResponse,
    DiagnoseResponse,
    ObserveResponse,
    ValidateResponse,
)
from marivo.transports.http.sessions import router


def _delta_frame_result(artifact_id: str = "art_compare_1") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-08T00:00:00Z",
                    "end": "2026-01-15T00:00:00Z",
                },
                "scope": {},
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                },
                "scope": {},
            },
        },
        "axes": [{"kind": "comparison_side"}],
        "measures": [{"id": "delta_abs", "value_type": "number", "nullable": True}],
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "current_value": 120.0,
                            "baseline_value": 100.0,
                            "delta_abs": 20.0,
                            "delta_pct": 0.2,
                            "direction": "increase",
                        }
                    ],
                }
            ],
            "scope": {
                "current_value": 120.0,
                "baseline_value": 100.0,
                "delta_abs": 20.0,
                "delta_pct": 0.2,
                "direction": "increase",
            },
        },
    }


class _FakeRuntime:
    def __init__(self) -> None:
        self.observe_payload: Any | None = None
        self.compare_payload: Any | None = None
        self.detect_payload: Any | None = None
        self.decompose_payload: Any | None = None
        self.test_payload: Any | None = None
        self.forecast_payload: Any | None = None
        self.attribute_payload: Any | None = None
        self.validate_payload: Any | None = None
        self.diagnose_payload: Any | None = None

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

    def compare(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.compare_payload = payload
        return {
            "intent_type": "compare",
            "step_type": "compare",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_compare_1",
                "step_type": "compare",
            },
            "artifact_id": "art_compare_1",
            "result": _delta_frame_result("art_compare_1"),
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

    def decompose(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.decompose_payload = payload
        return {
            "intent_type": "decompose",
            "step_type": "decompose",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_decompose_1",
                "step_type": "decompose",
            },
            "artifact_id": "art_decompose_1",
            "result": {
                "artifact_id": "art_decompose_1",
                "artifact_family": "attribution_frame",
                "shape": "ranked_contributions",
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.revenue",
                    "current": {
                        "time_scope": {
                            "field": "event_time",
                            "start": "2026-01-08T00:00:00Z",
                            "end": "2026-01-15T00:00:00Z",
                        },
                        "scope": {},
                    },
                    "baseline": {
                        "time_scope": {
                            "field": "event_time",
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-08T00:00:00Z",
                        },
                        "scope": {},
                    },
                },
                "axes": [{"kind": "dimension", "name": "region"}],
                "measures": [
                    {"id": "contribution_abs", "value_type": "number", "nullable": False},
                    {"id": "contribution_pct", "value_type": "number", "nullable": True},
                ],
                "capabilities": ["filterable"],
                "lineage": {"operation": "decompose", "source_artifact_ids": ["art_compare_1"]},
                "payload": {
                    "series": [
                        {
                            "keys": {"region": "US"},
                            "points": [
                                {
                                    "contribution_abs": 7.0,
                                    "contribution_pct": 0.7,
                                    "rank": 1,
                                }
                            ],
                        }
                    ],
                    "scope": {"delta_abs": 10.0},
                    "quality": {"reconciliation_status": "within_tolerance"},
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

    def diagnose(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.diagnose_payload = payload
        return {
            "intent_type": "diagnose",
            "step_type": "diagnose",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_diagnose_1",
                "step_type": "diagnose",
            },
            "artifact_id": "art_diagnose_1",
            "result": {
                "bundle_type": "diagnosis_bundle",
                "aoi_artifacts": [],
                "diagnoses": [],
            },
            "provenance": {"mocked": True},
            "product_metadata": None,
        }

    def attribute(self, session_id: str, payload: Any) -> dict[str, Any]:
        self.attribute_payload = payload
        return {
            "intent_type": "attribute",
            "step_type": "attribute",
            "step_ref": {
                "session_id": session_id,
                "step_id": "step_attribute_1",
                "step_type": "attribute",
            },
            "artifact_id": "art_attribute_1",
            "result": {
                "bundle_type": "attribute_bundle",
                "aoi_artifacts": [
                    _delta_frame_result("art_compare_1"),
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


def _metric_frame_result(artifact_id: str = "art_observe_1") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
            "scope": {},
        },
        "axes": [],
        "measures": [
            {
                "id": "value",
                "value_type": "number",
                "nullable": True,
                "unit": None,
            }
        ],
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "value": 42.0,
                        }
                    ],
                }
            ]
        },
    }


def _attribution_frame_result(artifact_id: str = "art_decompose_1") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "attribution_frame",
        "shape": "ranked_contributions",
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-08T00:00:00Z",
                    "end": "2026-01-15T00:00:00Z",
                },
                "scope": {},
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                },
                "scope": {},
            },
        },
        "axes": [{"kind": "dimension", "name": "region"}],
        "measures": [
            {"id": "contribution_abs", "value_type": "number", "nullable": False},
            {"id": "contribution_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": {"operation": "decompose", "source_artifact_ids": ["art_compare_1"]},
        "payload": {
            "series": [
                {
                    "keys": {"region": "US"},
                    "points": [
                        {
                            "contribution_abs": 7.0,
                            "contribution_pct": 0.7,
                            "rank": 1,
                        }
                    ],
                }
            ],
            "scope": {"delta_abs": 10.0},
            "quality": {"reconciliation_status": "within_tolerance"},
        },
    }


def test_atomic_response_model_accepts_aoi_artifact_wrapper() -> None:
    response = ObserveResponse.model_validate(
        {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": _step_ref("observe"),
            "artifact_id": "art_observe_1",
            "result": _metric_frame_result(),
        }
    )

    assert isinstance(response.result, aoi.MetricFrameArtifact)
    assert response.result.payload.series[0].points[0].value == 42.0


def test_decompose_response_model_accepts_attribution_frame_artifact() -> None:
    response = DecomposeResponse.model_validate(
        {
            "intent_type": "decompose",
            "step_type": "decompose",
            "step_ref": _step_ref("decompose"),
            "artifact_id": "art_decompose_1",
            "result": _attribution_frame_result(),
        }
    )

    assert response.result.artifact_family == "attribution_frame"
    assert response.result.shape == "ranked_contributions"
    point = response.result.payload.series[0].points[0]
    assert point.contribution_abs == 7.0
    assert point.contribution_pct == 0.7


def test_decompose_response_model_rejects_loose_attribution_frame_artifact() -> None:
    loose_artifact = _attribution_frame_result()
    loose_artifact["axes"].append({"kind": "dimension", "name": "country"})

    with pytest.raises(ValidationError):
        DecomposeResponse.model_validate(
            {
                "intent_type": "decompose",
                "step_type": "decompose",
                "step_ref": _step_ref("decompose"),
                "artifact_id": "art_decompose_1",
                "result": loose_artifact,
            }
        )


def test_observe_response_model_accepts_failure_artifact_without_result() -> None:
    response = ObserveResponse.model_validate(
        {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": _step_ref("observe"),
            "artifact_id": "art_observe_1",
            "result": {
                "artifact_id": "art_observe_1",
                "failure": {
                    "code": "NO_OBSERVATION",
                    "message": "No observation exists for this slice.",
                },
            },
        }
    )

    assert response.result.failure.code == "NO_OBSERVATION"
    assert response.result.result is None


def test_observe_response_model_rejects_failure_artifact_with_metric_frame_result() -> None:
    with pytest.raises(ValidationError):
        ObserveResponse.model_validate(
            {
                "intent_type": "observe",
                "step_type": "observe",
                "step_ref": _step_ref("observe"),
                "artifact_id": "art_observe_1",
                "result": {
                    "artifact_id": "art_observe_1",
                    "result": _metric_frame_result(),
                    "failure": {
                        "code": "NO_OBSERVATION",
                        "message": "No observation exists for this slice.",
                    },
                },
            }
        )


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


def test_derived_response_model_accepts_top_level_metric_frame_artifact() -> None:
    response = DiagnoseResponse.model_validate(
        {
            "intent_type": "diagnose",
            "step_type": "diagnose",
            "step_ref": _step_ref("diagnose"),
            "artifact_id": "art_diagnose_1",
            "result": {
                "bundle_type": "diagnosis_bundle",
                "aoi_artifacts": [_metric_frame_result()],
            },
        }
    )

    artifact = response.result.aoi_artifacts[0]
    assert isinstance(artifact, aoi.MetricFrameArtifact)
    assert artifact.artifact_family == "metric_frame"


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
    assert isinstance(runtime.observe_payload, aoi.Observe)
    body = response.json()
    assert body["intent_type"] == "observe"
    assert body["artifact_id"] == "art_observe_1"
    assert body["result"]["artifact_id"] == "art_observe_1"
    assert body["result"]["artifact_family"] == "metric_frame"
    assert body["result"]["shape"] == "scalar"
    assert body["result"]["payload"]["series"][0]["points"][0]["value"] == 42.0
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
    assert isinstance(runtime.observe_payload, aoi.Observe)
    assert runtime.observe_payload.granularity == "quarter"


def test_diagnose_accepts_generic_time_granularity() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/diagnose",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-07-01T00:00:00Z",
            },
            "granularity": "quarter",
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.diagnose_payload, aoi.Diagnose)
    assert runtime.diagnose_payload.granularity == "quarter"


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
    assert isinstance(runtime.observe_payload, aoi.Observe)
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


def test_compare_accepts_aoi_request_with_compare_type() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/compare",
        json={
            "current_artifact_id": "artifact_left",
            "baseline_artifact_id": "artifact_right",
            "compare_type": "weekday_aligned",
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.compare_payload, aoi.Compare)
    assert runtime.compare_payload.current_artifact_id == "artifact_left"
    assert runtime.compare_payload.baseline_artifact_id == "artifact_right"
    assert runtime.compare_payload.compare_type == "weekday_aligned"


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


def test_decompose_accepts_aoi_request_with_limit() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/decompose",
        json={
            "compare_artifact_id": "artifact_compare",
            "dimension": "region",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.decompose_payload, aoi.Decompose)
    assert runtime.decompose_payload.compare_artifact_id == "artifact_compare"
    assert runtime.decompose_payload.dimension == "region"
    assert runtime.decompose_payload.limit == 5
    body = response.json()
    assert body["result"]["artifact_family"] == "attribution_frame"
    assert body["result"]["shape"] == "ranked_contributions"
    point = body["result"]["payload"]["series"][0]["points"][0]
    assert point["contribution_abs"] == 7.0
    assert point["contribution_pct"] == 0.7
    assert "items" not in body["result"]


def _valid_test_request() -> dict[str, Any]:
    return {
        "metric": "metric.revenue",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "grain": "day",
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


def _valid_validate_request() -> dict[str, Any]:
    payload = _valid_test_request()
    payload.pop("kind")
    return payload


def _valid_attribute_request() -> dict[str, Any]:
    return {
        "metric": "metric.revenue",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "dimensions": ["region"],
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
        (
            "/sessions/sess_1/intents/forecast",
            {"source_artifact_id": "art_timeseries", "horizon": 14, "interval_level": 0.95},
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
    assert runtime.test_payload.grain == "day"
    assert runtime.test_payload.hypothesis.family == "two_sample_mean"
    body = response.json()
    assert body["intent_type"] == "test"
    assert body["artifact_id"] == "art_test_1"


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_test_accepts_time_granularity_grain(grain: str) -> None:
    runtime = _FakeRuntime()
    payload = _valid_test_request()
    payload["grain"] = grain

    response = _client(runtime).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 200, response.text
    assert runtime.test_payload.grain == grain


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("method",), "welch_t"),
        (("grain",), "minute"),
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
        json=_valid_validate_request(),
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.validate_payload, aoi.Validate)
    assert runtime.validate_payload.grain == "day"
    body = response.json()
    assert body["intent_type"] == "validate"
    assert body["result"]["bundle_type"] == "validation_bundle"
    assert body["result"]["aoi_artifacts"][0]["result"]["p_value"] == 0.04


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_validate_accepts_time_granularity_grain(grain: str) -> None:
    runtime = _FakeRuntime()
    payload = _valid_validate_request()
    payload["grain"] = grain

    response = _client(runtime).post("/sessions/sess_1/intents/validate", json=payload)

    assert response.status_code == 200, response.text
    assert runtime.validate_payload.grain == grain


def test_attribute_accepts_aoi_request_and_returns_typed_bundle() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/attribute",
        json=_valid_attribute_request(),
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.attribute_payload, aoi.Attribute)
    assert runtime.attribute_payload.decomposition_method == "delta_share"
    assert runtime.attribute_payload.decomposition_limit == 5
    body = response.json()
    assert body["intent_type"] == "attribute"
    assert body["result"]["bundle_type"] == "attribute_bundle"
    assert body["result"]["aoi_artifacts"][0]["artifact_family"] == "delta_frame"
    assert body["result"]["aoi_artifacts"][0]["payload"]["scope"]["delta_abs"] == 20.0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("method",), "welch_t"),
        (("kind",), "numeric"),
        (("grain",), "minute"),
        (("current", "scope"), {"predicate": "region = 'US'"}),
        (("hypothesis", "alpha"), 0.05),
    ],
)
def test_validate_rejects_representative_non_contract_fields(
    path: tuple[str, ...],
    value: Any,
) -> None:
    payload = _valid_validate_request()
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/validate", json=payload)

    assert response.status_code == 422
