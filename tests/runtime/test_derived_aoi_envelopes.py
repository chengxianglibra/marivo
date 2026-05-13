from __future__ import annotations

from typing import Any

import pytest

from marivo.runtime.intents import validate as validate_intent
from marivo.runtime.intents.derived_envelopes import build_derived_bundle_envelope


class _Core:
    def normalize_intent_metric_ref(self, metric_ref: str) -> str:
        return metric_ref

    def metric_name_from_ref(self, metric_ref: str) -> str:
        return metric_ref.removeprefix("metric.")


class _Runtime:
    core = _Core()

    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []

    def insert_artifact(
        self,
        session_id: str,
        step_id: str,
        artifact_type: str,
        artifact_name: str,
        content: dict[str, Any],
        *,
        artifact_id: str | None = None,
    ) -> str:
        committed_artifact_id = artifact_id or f"art_{artifact_type}"
        self.artifacts.append(
            {
                "session_id": session_id,
                "step_id": step_id,
                "artifact_type": artifact_type,
                "artifact_name": artifact_name,
                "content": content,
                "artifact_id": committed_artifact_id,
            }
        )
        return committed_artifact_id

    def insert_step(
        self,
        step_id: str,
        session_id: str,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            {
                "step_id": step_id,
                "session_id": session_id,
                "step_type": step_type,
                "summary": summary,
                "result": result,
                "provenance": provenance,
            }
        )


def _params() -> dict[str, Any]:
    return {
        "metric": "metric.revenue",
        "left": {"time_scope": {"start": "2026-01-01", "end": "2026-01-02"}},
        "right": {"time_scope": {"start": "2026-01-08", "end": "2026-01-09"}},
        "sample_kind": "numeric",
    }


def _observe(step_id: str, artifact_id: str) -> dict[str, Any]:
    return {
        "intent_type": "observe",
        "step_type": "observe",
        "step_ref": {
            "session_id": "sess_1",
            "step_id": step_id,
            "step_type": "observe",
        },
        "artifact_id": artifact_id,
        "observation_type": "numeric_sample_summary",
        "result": {
            "artifact_id": artifact_id,
            "result": {"sample_size": 10, "mean": 1.0, "stddev": 0.1},
        },
    }


def _assert_bundle_storage_is_consistent(result: dict[str, Any], runtime: _Runtime) -> None:
    assert result["artifact_id"].startswith("art_")
    assert result["artifact_id"] == runtime.artifacts[0]["artifact_id"]
    assert runtime.artifacts[0]["content"]["artifact_id"] == result["artifact_id"]
    assert runtime.steps[0]["result"]["artifact_id"] == result["artifact_id"]


def test_attribute_bundle_envelope_keeps_aoi_artifacts_in_result_and_product_metadata() -> None:
    runtime = _Runtime()
    aoi_artifact = {"artifact_id": "art_decompose", "result": {"rows": [{"key": "US"}]}}

    result = build_derived_bundle_envelope(
        runtime=runtime,
        session_id="sess_1",
        step_type="attribute",
        bundle_type="attribute_bundle",
        artifact_name="attribute_bundle",
        aoi_artifacts=[aoi_artifact],
        summary="attribute bundle",
        product_status="succeeded",
        issues=[],
    )

    assert result["intent_type"] == "attribute"
    assert result["result"] == {"bundle_type": "attribute_bundle", "aoi_artifacts": [aoi_artifact]}
    assert result["product_metadata"] == {
        "derived_operation": "attribute",
        "status": "succeeded",
        "issues": [],
        "aoi_artifacts": [aoi_artifact],
    }
    _assert_bundle_storage_is_consistent(result, runtime)


def test_diagnosis_bundle_envelope_keeps_aoi_artifacts_in_result_and_product_metadata() -> None:
    runtime = _Runtime()
    aoi_artifact = {"artifact_id": "art_detect", "result": {"anomalies": []}}

    result = build_derived_bundle_envelope(
        runtime=runtime,
        session_id="sess_1",
        step_type="diagnose",
        bundle_type="diagnosis_bundle",
        artifact_name="diagnosis_bundle",
        aoi_artifacts=[aoi_artifact],
        summary="diagnosis bundle",
        product_status="needs_attention",
        issues=[{"code": "low_confidence", "message": "needs review"}],
    )

    assert result["intent_type"] == "diagnose"
    assert result["result"]["bundle_type"] == "diagnosis_bundle"
    assert result["product_metadata"]["derived_operation"] == "diagnose"
    assert result["product_metadata"]["status"] == "needs_attention"
    assert result["product_metadata"]["issues"][0]["code"] == "low_confidence"
    assert result["product_metadata"]["aoi_artifacts"][0]["artifact_id"] == "art_detect"
    _assert_bundle_storage_is_consistent(result, runtime)


def test_validate_returns_validation_bundle_with_aoi_artifact_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observes = iter([_observe("step_left", "art_left"), _observe("step_right", "art_right")])

    def _run_observe_intent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(observes)

    def _run_test_intent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "intent_type": "test",
            "step_type": "test",
            "step_ref": {
                "session_id": "sess_1",
                "step_id": "step_test",
                "step_type": "test",
            },
            "artifact_id": "art_test",
            "result": {
                "artifact_id": "art_test",
                "result": {
                    "method": "welch_t",
                    "decision": {"reject_null": True},
                    "estimate": {"estimand": "mean_diff", "value": 10.0},
                    "p_value": 0.01,
                },
            },
            "method": "welch_t",
            "decision": {"reject_null": True},
            "estimate": {"estimand": "mean_diff", "value": 10.0},
            "p_value": 0.01,
            "validation": {"status": "valid", "issues": []},
        }

    monkeypatch.setattr(validate_intent, "run_observe_intent", _run_observe_intent)
    monkeypatch.setattr(validate_intent, "run_test_intent", _run_test_intent)

    runtime = _Runtime()
    result = validate_intent.run_validate_intent(runtime, "sess_1", _params())

    assert result["intent_type"] == "validate"
    assert result["result"]["bundle_type"] == "validation_bundle"
    assert result["product_metadata"]["derived_operation"] == "validate"
    assert result["product_metadata"]["status"] == "succeeded"
    assert result["product_metadata"]["aoi_artifacts"][0]["artifact_id"] == "art_test"
    _assert_bundle_storage_is_consistent(result, runtime)


def test_validate_returns_failed_validation_bundle_when_atomic_test_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observes = iter([_observe("step_left", "art_left"), _observe("step_right", "art_right")])

    def _run_observe_intent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(observes)

    def _run_test_intent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError("test rejected the input")

    monkeypatch.setattr(validate_intent, "run_observe_intent", _run_observe_intent)
    monkeypatch.setattr(validate_intent, "run_test_intent", _run_test_intent)

    runtime = _Runtime()
    result = validate_intent.run_validate_intent(runtime, "sess_1", _params())

    assert result["intent_type"] == "validate"
    assert result["result"] == {"bundle_type": "validation_bundle", "aoi_artifacts": []}
    assert result["product_metadata"]["status"] == "failed"
    assert result["product_metadata"]["issues"][0]["code"] == "derived_orchestration_failed"
    assert runtime.artifacts[0]["content"]["artifact_id"] == result["artifact_id"]
