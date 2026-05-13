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

    def test_model_dump_does_not_flatten_result(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"artifact_id": "art_1", "result": {"value": 42.0}},
        )
        dumped = env.model_dump()

        self.assertEqual(
            dumped["result"],
            {"artifact_id": "art_1", "result": {"value": 42.0}},
        )
        self.assertNotIn("value", dumped)


class TestCommitStepResultEnvelope(unittest.TestCase):
    def test_build_envelope_returns_envelope(self) -> None:
        """build_envelope should return ExecutionEnvelope."""
        from marivo.contracts.envelope import ExecutionEnvelope
        from marivo.runtime.intents._helpers import build_envelope

        env = build_envelope(
            session_id="s1",
            step_id="step_obs_1",
            step_type="observe",
            artifact_id="art_1",
            artifact_payload={"value": 42.0, "observation_type": "scalar"},
            provenance={"query_hash": "abc"},
        )
        self.assertIsInstance(env, ExecutionEnvelope)
        self.assertEqual(env.artifact_id, "art_1")
        self.assertEqual(env.result["value"], 42.0)
