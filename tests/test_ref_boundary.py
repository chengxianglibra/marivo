from __future__ import annotations

import unittest

from app.evidence_engine.ref_boundary import (
    RefBoundaryError,
    assert_no_canonical_refs_in_semantic_payload,
    assert_no_semantic_refs_in_canonical_payload,
    is_canonical_ref_mapping,
    is_semantic_ref,
)


class TestRefBoundaryHelpers(unittest.TestCase):
    def test_is_semantic_ref_accepts_supported_runtime_families(self) -> None:
        self.assertTrue(is_semantic_ref("metric.watch_time"))
        self.assertTrue(is_semantic_ref("dimension.country"))
        self.assertTrue(is_semantic_ref("binding.watch_time"))
        self.assertFalse(is_semantic_ref("watch_time"))
        self.assertFalse(is_semantic_ref("finding.watch_time"))

    def test_is_canonical_ref_mapping_detects_known_shapes(self) -> None:
        self.assertTrue(is_canonical_ref_mapping({"session_id": "sess_1", "finding_id": "find_1"}))
        self.assertTrue(
            is_canonical_ref_mapping(
                {"assessment_id": "assess_1", "proposition_id": "prop_1", "snapshot_seq": 1}
            )
        )
        self.assertTrue(is_canonical_ref_mapping({"artifact_id": "art_1"}))
        self.assertFalse(is_canonical_ref_mapping({"metric_ref": "metric.watch_time"}))

    def test_canonical_payload_rejects_semantic_ref_fields(self) -> None:
        payload = {
            "artifact_refs": [{"artifact_id": "art_1", "step_ref": {"session_id": "sess_1"}}],
            "metric_ref": "metric.watch_time",
        }

        with self.assertRaises(RefBoundaryError) as error:
            assert_no_semantic_refs_in_canonical_payload(payload, surface="state_view")

        self.assertIn("metric_ref", str(error.exception))

    def test_canonical_payload_allows_typed_semantic_identifiers_inside_subject_payload(
        self,
    ) -> None:
        payload = {
            "proposition": {
                "subject_json": {
                    "metric": "metric.watch_time",
                    "slice": {"dimension.country": "US"},
                }
            }
        }

        assert_no_semantic_refs_in_canonical_payload(payload, surface="state_view")

    def test_semantic_payload_rejects_canonical_ref_fields(self) -> None:
        payload = {
            "plan": {"inputs": {"metric_ref": "metric.watch_time"}},
            "compile_report": {
                "validation_summary": {"passed_gate_count": 1, "warning_count": 0},
                "finding_ref": {"session_id": "sess_1", "finding_id": "find_1"},
            },
        }

        with self.assertRaises(RefBoundaryError) as error:
            assert_no_canonical_refs_in_semantic_payload(payload, surface="compiler_ir_bundle")

        self.assertIn("finding_ref", str(error.exception))


if __name__ == "__main__":
    unittest.main()
