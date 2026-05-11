# tests/test_envelope.py
from __future__ import annotations

import unittest

from marivo.contracts.envelope import ExecutionEnvelope, StepRef


class TestExecutionEnvelope(unittest.TestCase):
    def test_construct_with_dict_result(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
        )
        self.assertEqual(env.intent_type, "observe")
        self.assertEqual(env.artifact_id, "art_1")
        self.assertEqual(env.result, {"value": 42.0})
        self.assertIsNone(env.provenance)
        self.assertIsNone(env.product_metadata)

    def test_construct_with_provenance(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
            provenance={"query_hash": "abc123"},
        )
        self.assertEqual(env.provenance, {"query_hash": "abc123"})

    def test_construct_with_product_metadata(self) -> None:
        env = ExecutionEnvelope(
            intent_type="validate",
            step_type="validate",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="validate"),
            artifact_id="art_1",
            result={"statistic": 2.1, "p_value": 0.03},
            product_metadata={"validation": {"status": "pass", "issues": []}},
        )
        self.assertEqual(env.product_metadata["validation"]["status"], "pass")

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
        self.assertEqual(legacy["intent_type"], "observe")
        self.assertEqual(legacy["step_ref"]["session_id"], "s1")
        self.assertEqual(legacy["artifact_id"], "art_1")
        # result fields are flat-merged at top level for backward compat
        self.assertEqual(legacy["value"], 42.0)
        self.assertEqual(legacy["observation_type"], "scalar")
