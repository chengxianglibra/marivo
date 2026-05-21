from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from marivo.contracts.envelope import ExecutionEnvelope
from marivo.runtime.intents._helpers import commit_aoi_artifact_result, commit_step_result


def test_commit_step_result_returns_dict_with_step_ref():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.return_value = "artifact-abc"

    result = commit_step_result(
        runtime=mock_runtime,
        session_id="sess-1",
        step_id="step-1",
        step_type="observe",
        artifact_type="observation",
        artifact_name="revenue_observe",
        artifact_payload={"rows": []},
        summary="Observed revenue",
        provenance={"intent": "observe"},
    )

    assert result["step_ref"]["session_id"] == "sess-1"
    assert result["step_ref"]["step_id"] == "step-1"
    assert result["step_ref"]["step_type"] == "observe"
    assert result["artifact_id"] == "artifact-abc"
    assert result["intent_type"] == "observe"
    assert result["step_type"] == "observe"
    assert result["rows"] == []
    assert mock_runtime.commit_artifact_with_extraction.call_count == 1
    assert mock_runtime.insert_step.call_count == 1


def test_commit_step_result_passes_artifact_type_and_name():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.return_value = "art-xyz"

    commit_step_result(
        runtime=mock_runtime,
        session_id="sess-2",
        step_id="step-2",
        step_type="compare",
        artifact_type="compare_artifact",
        artifact_name="revenue_compare",
        artifact_payload={"comparison_type": "scalar_delta"},
        summary="Compared revenue",
        provenance={"left_step_id": "s1", "right_step_id": "s2"},
    )

    call_args = mock_runtime.commit_artifact_with_extraction.call_args
    assert call_args[0][0] == "sess-2"
    assert call_args[0][1] == "step-2"
    assert call_args[0][2] == "compare_artifact"
    assert call_args[0][3] == "revenue_compare"
    assert call_args[0][4] == {"comparison_type": "scalar_delta"}
    assert call_args[1].get("step_type") == "compare"


def test_commit_step_result_passes_provenance_and_semantic_metadata():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.return_value = "art-prov"

    commit_step_result(
        runtime=mock_runtime,
        session_id="sess-3",
        step_id="step-3",
        step_type="detect",
        artifact_type="detection",
        artifact_name="revenue_detect",
        artifact_payload={"anomalies": []},
        summary="Detected anomalies",
        provenance={"query_hash": "abc123"},
        semantic_metadata={"metric": "revenue"},
    )

    call_args = mock_runtime.insert_step.call_args
    # insert_step(step_id, session_id, step_type, summary, result, provenance=..., semantic_metadata=...)
    assert call_args[0][0] == "step-3"
    assert call_args[0][1] == "sess-3"
    assert call_args[0][2] == "detect"
    assert call_args[0][3] == "Detected anomalies"
    assert call_args[1].get("provenance") == {"query_hash": "abc123"}
    assert call_args[1].get("semantic_metadata") == {"metric": "revenue"}


def test_commit_step_result_defaults_semantic_metadata_to_none():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.return_value = "art-def"

    commit_step_result(
        runtime=mock_runtime,
        session_id="sess-4",
        step_id="step-4",
        step_type="observe",
        artifact_type="observation",
        artifact_name="rev_obs",
        artifact_payload={"value": 42},
        summary="Observed",
        provenance={},
    )

    call_args = mock_runtime.insert_step.call_args
    assert call_args[1].get("semantic_metadata") is None


def test_commit_step_result_merges_payload_into_result():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.return_value = "art-merge"

    result = commit_step_result(
        runtime=mock_runtime,
        session_id="sess-5",
        step_id="step-5",
        step_type="observe",
        artifact_type="observation",
        artifact_name="rev_obs",
        artifact_payload={"schema_version": "1.0", "metric": "revenue", "value": 100.0},
        summary="Observed revenue",
        provenance={},
    )

    # The artifact_payload keys should be merged into the result dict
    assert result["schema_version"] == "1.0"
    assert result["metric"] == "revenue"
    assert result["value"] == 100.0


def test_commit_aoi_artifact_result_returns_envelope_and_inserts_nested_result():
    mock_runtime = MagicMock()
    mock_runtime.commit_artifact_with_extraction.side_effect = lambda *args, **kwargs: kwargs[
        "artifact_id"
    ]
    payload = {
        "artifact_id": "placeholder-artifact",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.view_time",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "scope": {},
        },
        "axes": [],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"value": 42.0}]}]},
    }

    envelope = commit_aoi_artifact_result(
        runtime=mock_runtime,
        session_id="sess-aoi",
        step_id="step-aoi",
        step_type="observe",
        artifact_type="observation",
        artifact_name="view_time_observe",
        artifact_payload=payload,
        summary="Observed view_time",
        provenance={"query_hash": "abc123"},
        product_metadata={"source": "task-5"},
        semantic_metadata={"metric": "view_time"},
    )

    assert isinstance(envelope, ExecutionEnvelope)
    assert envelope.artifact_id.startswith("art_")
    assert envelope.artifact_id != "placeholder-artifact"
    assert envelope.result == {
        "artifact_id": envelope.artifact_id,
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.view_time",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "scope": {},
        },
        "axes": [],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"value": 42.0}]}]},
    }
    assert "value" not in envelope.model_dump()

    commit_args = mock_runtime.commit_artifact_with_extraction.call_args
    assert commit_args[0] == (
        "sess-aoi",
        "step-aoi",
        "observation",
        "view_time_observe",
        {
            "artifact_id": envelope.artifact_id,
            "artifact_family": "metric_frame",
            "shape": "scalar",
            "subject": {
                "kind": "metric",
                "metric_ref": "metric.view_time",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "scope": {},
            },
            "axes": [],
            "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
            "payload": {"series": [{"keys": {}, "points": [{"value": 42.0}]}]},
        },
    )
    assert commit_args[1]["step_type"] == "observe"
    assert commit_args[1]["artifact_id"] == envelope.artifact_id

    insert_args = mock_runtime.insert_step.call_args
    assert insert_args[0] == (
        "step-aoi",
        "sess-aoi",
        "observe",
        "Observed view_time",
        envelope.model_dump(),
    )
    assert insert_args[1]["provenance"] == {"query_hash": "abc123"}
    assert insert_args[1]["semantic_metadata"] == {"metric": "view_time"}
    assert insert_args[0][4]["artifact_id"] == envelope.artifact_id
    assert insert_args[0][4]["result"]["artifact_id"] == envelope.artifact_id
    assert insert_args[0][4]["result"]["artifact_family"] == "metric_frame"


def test_commit_aoi_artifact_result_rejects_non_aoi_payload_before_insert():
    mock_runtime = MagicMock()

    with pytest.raises(ValidationError):
        commit_aoi_artifact_result(
            runtime=mock_runtime,
            session_id="sess-aoi",
            step_id="step-aoi",
            step_type="observe",
            artifact_type="observation",
            artifact_name="invalid_observe",
            artifact_payload={"value": 42.0},
            summary="Invalid observe",
        )

    mock_runtime.commit_artifact_with_extraction.assert_not_called()
    mock_runtime.insert_step.assert_not_called()
