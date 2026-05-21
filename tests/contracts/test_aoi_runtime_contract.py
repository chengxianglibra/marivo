from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from marivo.contracts.aoi_runtime import (
    AOI_DERIVED_OPERATION_REGISTRY,
    AOI_OPERATION_REGISTRY,
    RuntimeIntentEnvelope,
    artifact_to_envelope_result,
    assert_derived_request_matches_intent,
    assert_request_matches_intent,
    validate_aoi_artifact,
)
from marivo.contracts.envelope import ExecutionEnvelope, StepRef
from marivo.contracts.generated import aoi


def _time_scope() -> aoi.TimeScope:
    return aoi.TimeScope.model_validate(
        {
            "field": "event_time",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
        }
    )


def _time_scope_payload() -> dict[str, str]:
    return {
        "field": "event_time",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
    }


def _observe_request() -> aoi.Observe:
    return aoi.Observe(
        metric="view_time",
        time_scope=_time_scope(),
    )


def test_runtime_intent_envelope_accepts_generated_observe_request() -> None:
    request = _observe_request()

    envelope = RuntimeIntentEnvelope(
        session_id="session_1",
        actor="alice",
        request=request,
    )

    assert envelope.request is request


def test_assert_request_matches_intent_rejects_operation_mismatch() -> None:
    request = aoi.Forecast(horizon=7, source_artifact_id="artifact_1")

    with pytest.raises(ValueError, match="AOI_OPERATION_MISMATCH"):
        assert_request_matches_intent("compare", request)


def test_aoi_operation_registry_contains_atomic_operations() -> None:
    assert set(AOI_OPERATION_REGISTRY) == {
        "compare",
        "correlate",
        "decompose",
        "detect",
        "forecast",
        "observe",
        "test",
    }


def test_aoi_correlate_accepts_artifact_ids_method_and_min_pairs() -> None:
    request = aoi.Correlate.model_validate(
        {
            "left_artifact_id": "art_left",
            "right_artifact_id": "art_right",
            "method": "pearson",
            "min_pairs": 7,
        }
    )

    assert request.left_artifact_id == "art_left"
    assert request.right_artifact_id == "art_right"
    assert request.method == "pearson"
    assert request.min_pairs == 7


def test_aoi_correlate_accepts_omitted_optional_parameters() -> None:
    request = aoi.Correlate.model_validate(
        {
            "left_artifact_id": "art_left",
            "right_artifact_id": "art_right",
        }
    )

    assert request.method is None
    assert request.min_pairs is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "left_artifact_id": "art_left",
            "right_artifact_id": "art_right",
            "method": "kendall",
        },
        {
            "left_artifact_id": "art_left",
            "right_artifact_id": "art_right",
            "min_pairs": 0,
        },
        {
            "left_artifact_id": "art_left",
            "right_artifact_id": "art_right",
            "min_pairs": None,
        },
    ],
)
def test_aoi_correlate_rejects_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        aoi.Correlate.model_validate(payload)


def test_aoi_forecast_accepts_artifact_id_and_horizon_only() -> None:
    request = aoi.Forecast.model_validate(
        {
            "source_artifact_id": "art_source",
            "horizon": 7,
        }
    )

    assert request.source_artifact_id == "art_source"
    assert request.horizon == 7


@pytest.mark.parametrize(
    "payload",
    [
        {"source_artifact_id": "art_source", "horizon": 7, "profile": "auto"},
        {"source_artifact_id": "art_source", "horizon": 7, "interval_level": 0.95},
        {"source_artifact_id": "art_source", "horizon": 0},
        {"source_artifact_id": "art_source", "horizon": None},
        {"source_artifact_id": None, "horizon": 7},
    ],
)
def test_aoi_forecast_rejects_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        aoi.Forecast.model_validate(payload)


def test_aoi_derived_operation_registry_contains_derived_operations() -> None:
    assert set(AOI_DERIVED_OPERATION_REGISTRY) == {"attribute", "diagnose", "validate"}


def test_runtime_intent_envelope_accepts_generated_validate_request() -> None:
    request = aoi.Validate(
        metric="view_time",
        current=aoi.Slice(time_scope=_time_scope()),
        baseline=aoi.Slice(time_scope=_time_scope()),
        grain="day",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="two_sided",
            significance="balanced",
        ),
    )

    envelope = RuntimeIntentEnvelope(
        session_id="session_1",
        actor="alice",
        request=request,
    )

    assert envelope.request is request


def test_aoi_validate_accepts_full_current_shape_with_filters() -> None:
    request = aoi.Validate.model_validate(
        {
            "metric": "view_time",
            "current": {
                "time_scope": _time_scope_payload(),
                "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
            },
            "baseline": {
                "time_scope": _time_scope_payload(),
                "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]},
            },
            "grain": "day",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "greater",
                "significance": "aggressive",
            },
        }
    )

    assert request.current.filter is not None
    assert request.baseline.filter is not None
    assert request.hypothesis.alternative == "greater"
    assert request.hypothesis.significance == "aggressive"


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"method": "welch_t"},
        {"kind": "numeric"},
        {"current": {"scope": {"predicate": "region = 'US'"}}},
        {"current": {"filter": None}},
        {"hypothesis": {"__remove__": "family"}},
        {"hypothesis": {"__remove__": "alternative"}},
        {"hypothesis": {"__remove__": "significance"}},
        {"hypothesis": {"alpha": 0.05}},
    ],
)
def test_aoi_validate_rejects_invalid_shape(
    payload_patch: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "metric": "view_time",
        "current": {"time_scope": _time_scope_payload()},
        "baseline": {"time_scope": _time_scope_payload()},
        "grain": "day",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }
    _merge_patch(payload, payload_patch)

    with pytest.raises(ValidationError):
        aoi.Validate.model_validate(payload)


def test_runtime_intent_envelope_accepts_generated_attribute_request() -> None:
    request = aoi.Attribute(
        metric="view_time",
        current=aoi.Slice(time_scope=_time_scope()),
        baseline=aoi.Slice(time_scope=_time_scope()),
        dimensions=["region"],
    )

    envelope = RuntimeIntentEnvelope(
        session_id="session_1",
        actor="alice",
        request=request,
    )

    assert envelope.request is request


def test_runtime_intent_envelope_accepts_generated_diagnose_request() -> None:
    request = aoi.Diagnose(
        metric="view_time",
        time_scope=_time_scope(),
        granularity="day",
        dimensions=["region"],
        strategy="point_anomaly",
    )

    envelope = RuntimeIntentEnvelope(
        session_id="session_1",
        actor="alice",
        request=request,
    )

    assert envelope.request is request


def test_assert_derived_request_matches_intent_rejects_operation_mismatch() -> None:
    request = aoi.Validate(
        metric="view_time",
        current=aoi.Slice(time_scope=_time_scope()),
        baseline=aoi.Slice(time_scope=_time_scope()),
        grain="day",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="two_sided",
            significance="balanced",
        ),
    )

    with pytest.raises(ValueError, match="AOI_DERIVED_OPERATION_MISMATCH"):
        assert_derived_request_matches_intent("attribute", request)


def test_validate_aoi_artifact_returns_success_artifact() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "artifact_1",
            "result": {"value": 42.0},
        }
    )

    assert isinstance(artifact, aoi.Artifact1)
    assert artifact.artifact_id == "artifact_1"


def test_validate_aoi_artifact_returns_failure_artifact() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "artifact_1",
            "failure": {
                "code": "NOT_COMPARABLE",
                "message": "No comparable baseline.",
            },
        }
    )

    assert isinstance(artifact, aoi.Artifact2)
    assert artifact.failure.code == "NOT_COMPARABLE"


def test_validate_aoi_artifact_accepts_generated_success_artifact() -> None:
    source = aoi.Artifact1(
        artifact_id="artifact_1",
        result=aoi.ScalarObservationResult(value=42.0),
    )

    artifact = validate_aoi_artifact(source)

    assert isinstance(artifact, aoi.Artifact1)
    assert artifact.model_dump(exclude_none=True) == source.model_dump(exclude_none=True)


def test_validate_aoi_artifact_rejects_mixed_generated_success_artifact() -> None:
    source = aoi.Artifact1(
        artifact_id="artifact_1",
        result=aoi.ScalarObservationResult(value=42.0),
        failure=aoi.AnalysisFailure(
            code="NOT_COMPARABLE",
            message="No comparable baseline.",
        ),
    )

    with pytest.raises(ValidationError):
        validate_aoi_artifact(source)


def test_validate_aoi_artifact_rejects_non_aoi_artifact_shape() -> None:
    with pytest.raises(ValidationError):
        validate_aoi_artifact({"value": 42.0})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "artifact_id": "artifact_1",
            "result": {"value": 42.0},
            "failure": None,
        },
        {
            "artifact_id": "artifact_1",
            "result": None,
            "failure": {
                "code": "NOT_COMPARABLE",
                "message": "No comparable baseline.",
            },
        },
    ],
)
def test_validate_aoi_artifact_rejects_nullable_counterpart_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        validate_aoi_artifact(payload)


def test_execution_envelope_keeps_aoi_artifact_under_result() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "artifact_1",
            "result": {"value": 42.0},
        }
    )

    envelope = ExecutionEnvelope(
        intent_type="observe",
        step_type="observe",
        step_ref=StepRef(
            session_id="session_1",
            step_id="step_1",
            step_type="observe",
        ),
        artifact_id="artifact_1",
        result=artifact_to_envelope_result(artifact),
    )

    assert envelope.result == {
        "artifact_id": "artifact_1",
        "result": {"value": 42.0},
    }
    assert "value" not in envelope.model_dump()


def _merge_patch(target: dict[str, Any], patch_value: dict[str, Any]) -> None:
    for key, value in patch_value.items():
        if key == "__remove__":
            target.pop(str(value))
            continue
        nested = target.get(key)
        if isinstance(value, dict) and isinstance(nested, dict):
            _merge_patch(nested, value)
        else:
            target[key] = value
