# tests/test_envelope.py
from __future__ import annotations

from marivo.contracts.envelope import ExecutionEnvelope, StepRef


class TestExecutionEnvelope:
    def test_construct_with_dict_result(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
        )
        assert env.intent_type == "observe"
        assert env.artifact_id == "art_1"
        assert env.result == {"value": 42.0}
        assert env.provenance is None
        assert env.product_metadata is None

    def test_construct_with_provenance(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
            provenance={"query_hash": "abc123"},
        )
        assert env.provenance == {"query_hash": "abc123"}

    def test_construct_with_product_metadata(self) -> None:
        env = ExecutionEnvelope(
            intent_type="validate",
            step_type="validate",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="validate"),
            artifact_id="art_1",
            result={"statistic": 2.1, "p_value": 0.03},
            product_metadata={"validation": {"status": "pass", "issues": []}},
        )
        assert env.product_metadata["validation"]["status"] == "pass"

    def test_to_legacy_dict_flat_merges_result(self) -> None:
        """Backward compat: to_legacy_dict() produces the flat dict shape
        that existing HTTP responses and MCP consumers expect."""
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0, "observation_type": "scalar"},
        )
        legacy = env.to_legacy_dict()
        assert legacy["intent_type"] == "observe"
        assert legacy["step_ref"]["session_id"] == "s1"
        assert legacy["artifact_id"] == "art_1"
        # result fields are flat-merged at top level for backward compat
        assert legacy["value"] == 42.0
        assert legacy["observation_type"] == "scalar"
