from __future__ import annotations

from typing import Any

from marivo.contracts.aoi_projection import project_aoi_artifact_from_any
from marivo.runtime.intents.derived_envelopes import (
    aoi_artifact_dump,
    build_derived_bundle_envelope,
)


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

    @staticmethod
    def resolve_metric(metric_name: str) -> Any:
        return type(
            "M",
            (),
            {
                "semantic_object": {"header": {"aggregation_semantics": "sum"}},
            },
        )()

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
        "current": {"time_scope": {"start": "2026-01-01", "end": "2026-01-02"}},
        "baseline": {"time_scope": {"start": "2026-01-08", "end": "2026-01-09"}},
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


def test_aoi_artifact_dump_preserves_flat_runtime_payload_for_projection() -> None:
    flat_compare_result = {
        "intent_type": "compare",
        "step_type": "compare",
        "step_ref": {
            "session_id": "sess_1",
            "step_id": "step_compare_1",
            "step_type": "compare",
        },
        "artifact_id": "art_compare",
        "comparison_type": "scalar_delta",
        "current_value": 10.0,
        "baseline_value": 7.0,
        "absolute_delta": 3.0,
        "provenance": {"query": "internal"},
        "product_metadata": {"debug": True},
    }

    artifact = aoi_artifact_dump(flat_compare_result)
    projected = project_aoi_artifact_from_any(artifact)

    assert artifact["artifact_id"] == "art_compare"
    assert artifact["result"]["comparison_type"] == "scalar_delta"
    assert "provenance" not in artifact["result"]
    assert projected["artifact_id"] == "art_compare"
    assert projected["result"]["current_value"] == 10.0
    assert projected["result"]["baseline_value"] == 7.0
    assert projected["result"]["delta"] == 3.0


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
