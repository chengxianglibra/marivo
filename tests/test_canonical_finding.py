"""Tests for canonical finding identity helpers and type contract.

Covers Phase 4a-2 acceptance criteria:
- finding persistence structure can express artifact_item_ref
- same (artifact_id, item boundary, finding_type) replay produces stable finding_id
- rank / projection_order / summary text do NOT enter canonical identity
- canonical_item_key priority: stable key preferred over index
"""

from __future__ import annotations

import json
import typing
import unittest

from app.evidence_engine.canonical_finding import (
    AnomalyCandidateFinding,
    AnyFinding,
    ArtifactItemRef,
    CorrelationResultFinding,
    DecompositionItemFinding,
    DeltaFinding,
    FindingBase,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    ForecastPointFinding,
    ObservationFinding,
    StepRef,
    TestResultFinding,
    make_canonical_item_key,
    make_finding_id,
)

# ---------------------------------------------------------------------------
# make_finding_id stability
# ---------------------------------------------------------------------------


class TestMakeFindingId(unittest.TestCase):
    def test_same_inputs_produce_same_id(self) -> None:
        id1 = make_finding_id("art_abc", "delta", "rows:device_ios")
        id2 = make_finding_id("art_abc", "delta", "rows:device_ios")
        self.assertEqual(id1, id2)

    def test_different_artifact_ids_produce_different_ids(self) -> None:
        id1 = make_finding_id("art_abc", "delta", "rows:device_ios")
        id2 = make_finding_id("art_xyz", "delta", "rows:device_ios")
        self.assertNotEqual(id1, id2)

    def test_different_finding_types_produce_different_ids(self) -> None:
        id1 = make_finding_id("art_abc", "delta", "rows:device_ios")
        id2 = make_finding_id("art_abc", "observation", "rows:device_ios")
        self.assertNotEqual(id1, id2)

    def test_different_canonical_item_keys_produce_different_ids(self) -> None:
        id1 = make_finding_id("art_abc", "delta", "rows:device_ios")
        id2 = make_finding_id("art_abc", "delta", "rows:device_android")
        self.assertNotEqual(id1, id2)

    def test_output_has_expected_prefix(self) -> None:
        fid = make_finding_id("art_abc", "observation", "value")
        self.assertTrue(fid.startswith("fnd_"), f"Expected 'fnd_' prefix, got: {fid!r}")

    def test_output_length_is_stable(self) -> None:
        fid1 = make_finding_id("art_abc", "observation", "value")
        fid2 = make_finding_id("art_xyz", "delta", "rows:seg_us")
        # prefix (4) + 24 hex chars
        self.assertEqual(len(fid1), 28)
        self.assertEqual(len(fid2), 28)

    def test_extractor_version_not_in_identity(self) -> None:
        """Upgrading extractor_version for the same item boundary must not shift finding_id.

        The public API of make_finding_id deliberately excludes extractor_version.
        This test verifies both the signature contract and that two runs with
        different extractor versions (same canonical triple) produce the same id.
        """
        import inspect

        params = list(inspect.signature(make_finding_id).parameters)
        self.assertEqual(
            params,
            ["artifact_id", "finding_type", "canonical_item_key"],
            "make_finding_id must not accept extractor_version as a parameter",
        )
        # Same canonical triple regardless of extractor version → same id
        canonical_key = "rows:device_ios"
        id_extractor_v1 = make_finding_id("art_abc", "delta", canonical_key)
        id_extractor_v2 = make_finding_id("art_abc", "delta", canonical_key)
        self.assertEqual(id_extractor_v1, id_extractor_v2)

    def test_rank_not_in_identity(self) -> None:
        """Items with the same stable segment key but different ranks produce identical finding_ids.

        Rank changes when re-running compare with a different period or ordering, but
        the segment identifier stays stable.  The extractor must use the segment value
        as the key, NOT the rank position.

        The counter-example (using rank as key) produces divergent ids — demonstrating
        why rank must be excluded from canonical_item_key.
        """
        segment_value = "device_ios"

        # Correct approach: stable segment key ignores rank
        canonical_key = make_canonical_item_key("rows", key=segment_value)
        id_rank1 = make_finding_id("art_abc", "delta", canonical_key)
        id_rank5 = make_finding_id("art_abc", "delta", canonical_key)
        self.assertEqual(
            id_rank1, id_rank5, "Same stable key must yield same id regardless of rank"
        )

        # Wrong approach: if rank were used as the key, ids would diverge
        bad_key_rank1 = make_canonical_item_key("rows", key="1")
        bad_key_rank5 = make_canonical_item_key("rows", key="5")
        id_bad_rank1 = make_finding_id("art_abc", "delta", bad_key_rank1)
        id_bad_rank5 = make_finding_id("art_abc", "delta", bad_key_rank5)
        self.assertNotEqual(
            id_bad_rank1, id_bad_rank5, "Rank-based keys must diverge — this is the anti-pattern"
        )


# ---------------------------------------------------------------------------
# make_canonical_item_key priority rules
# ---------------------------------------------------------------------------


class TestMakeCanonicalItemKey(unittest.TestCase):
    def test_stable_key_takes_priority_over_index(self) -> None:
        key = make_canonical_item_key("rows", key="device_ios", index=0)
        self.assertIn("device_ios", key)
        self.assertNotIn(":0", key)

    def test_stable_key_only(self) -> None:
        key = make_canonical_item_key("rows", key="device_ios")
        self.assertEqual(key, "rows:device_ios")

    def test_index_used_when_no_key(self) -> None:
        key = make_canonical_item_key("candidates", index=3)
        self.assertEqual(key, "candidates:3")

    def test_no_key_no_index_returns_collection(self) -> None:
        key = make_canonical_item_key("value")
        self.assertEqual(key, "value")

    def test_scalar_canonical_key(self) -> None:
        key = make_canonical_item_key("value")
        self.assertEqual(key, "value")

    def test_result_canonical_key(self) -> None:
        key = make_canonical_item_key("result")
        self.assertEqual(key, "result")

    def test_different_collections_produce_different_keys(self) -> None:
        k1 = make_canonical_item_key("rows", key="ios")
        k2 = make_canonical_item_key("buckets", key="ios")
        self.assertNotEqual(k1, k2)

    def test_index_zero_is_valid(self) -> None:
        key = make_canonical_item_key("rows", index=0)
        self.assertEqual(key, "rows:0")


# ---------------------------------------------------------------------------
# ArtifactItemRef structure
# ---------------------------------------------------------------------------


class TestArtifactItemRef(unittest.TestCase):
    def _make_ref(self, **kwargs) -> ArtifactItemRef:
        return ArtifactItemRef(**kwargs)  # type: ignore[misc]

    def test_stable_key_ref(self) -> None:
        ref = self._make_ref(collection="rows", index=None, key="device_ios")
        self.assertEqual(ref["collection"], "rows")
        self.assertIsNone(ref["index"])
        self.assertEqual(ref["key"], "device_ios")

    def test_index_ref(self) -> None:
        ref = self._make_ref(collection="candidates", index=3, key=None)
        self.assertEqual(ref["collection"], "candidates")
        self.assertEqual(ref["index"], 3)
        self.assertIsNone(ref["key"])

    def test_single_item_ref(self) -> None:
        ref = self._make_ref(collection="value", index=None, key=None)
        self.assertEqual(ref["collection"], "value")
        self.assertIsNone(ref["index"])
        self.assertIsNone(ref["key"])


# ---------------------------------------------------------------------------
# FindingProvenance round-trip and reserved fields
# ---------------------------------------------------------------------------


class TestFindingProvenance(unittest.TestCase):
    def _make_provenance(self) -> FindingProvenance:
        # artifact_item_ref.key must be non-null when a stable key exists (schema rule:
        # "有稳定 key 时，key 必须非空"). canonical_item_key and artifact_item_ref.key
        # must agree on whether a stable key is available.
        return FindingProvenance(
            source_step_type="compare",
            extractor_name="compare_delta_extractor",
            extractor_version="v1",
            artifact_schema_version="v1",
            canonical_item_key="rows:device_ios",
            artifact_item_ref=ArtifactItemRef(collection="rows", index=None, key="device_ios"),
            projection_ref=None,
        )

    def test_json_roundtrip(self) -> None:
        prov = self._make_provenance()
        serialized = json.dumps(dict(prov))
        restored = json.loads(serialized)
        self.assertEqual(restored["source_step_type"], "compare")
        self.assertEqual(restored["extractor_name"], "compare_delta_extractor")
        self.assertEqual(restored["extractor_version"], "v1")
        self.assertEqual(restored["artifact_schema_version"], "v1")
        self.assertEqual(restored["canonical_item_key"], "rows:device_ios")
        self.assertIsNone(restored["projection_ref"])

    def test_artifact_item_ref_preserved(self) -> None:
        prov = self._make_provenance()
        ref = prov["artifact_item_ref"]
        self.assertEqual(ref["collection"], "rows")
        self.assertIsNone(ref["index"])  # index must be None when a stable key is available
        self.assertEqual(ref["key"], "device_ios")

    def test_canonical_item_key_is_reserved_field(self) -> None:
        """canonical_item_key must be a top-level field in FindingProvenance."""
        prov = self._make_provenance()
        self.assertIn("canonical_item_key", prov)
        self.assertEqual(prov["canonical_item_key"], "rows:device_ios")

    def test_extractor_version_in_provenance_not_identity(self) -> None:
        """extractor_version lives in provenance, not in finding_id inputs."""
        prov = self._make_provenance()
        self.assertEqual(prov["extractor_version"], "v1")
        # Changing extractor_version does not change the finding_id because
        # finding_id is computed from (artifact_id, finding_type, canonical_item_key)
        # — neither of which include extractor_version.
        fid = make_finding_id("art_cmp_01", "delta", prov["canonical_item_key"])
        self.assertTrue(fid.startswith("fnd_"))


# ---------------------------------------------------------------------------
# FindingBase completeness
# ---------------------------------------------------------------------------


class TestFindingBase(unittest.TestCase):
    def _make_finding_base(self) -> FindingBase:
        provenance = FindingProvenance(
            source_step_type="observe",
            extractor_name="observe_extractor",
            extractor_version="v1",
            artifact_schema_version="v1",
            canonical_item_key="value",
            artifact_item_ref=ArtifactItemRef(collection="value", index=None, key=None),
            projection_ref=None,
        )
        subject = FindingSubject(
            metric="avg_watch_time",
            entity=None,
            slice={},
            grain="day",
            analysis_axis="scalar",
        )
        quality = FindingQuality(
            data_complete=True,
            sample_size=5000,
            row_count=1,
            null_rate=0.0,
            quality_status="ready",
            quality_warnings=[],
        )
        step_ref = StepRef(session_id="sess_01", step_id="step_01", step_type="observe")
        fid = make_finding_id("art_obs_01", "observation", "value")
        return FindingBase(
            finding_id=fid,
            finding_type="observation",
            artifact_id="art_obs_01",
            step_ref=step_ref,
            subject=subject,
            observed_window=None,
            quality=quality,
            provenance=provenance,
            payload={"observation_kind": "scalar", "value": 12.4, "unit": "minutes"},
        )

    def test_all_required_fields_present(self) -> None:
        finding = self._make_finding_base()
        required = [
            "finding_id",
            "finding_type",
            "artifact_id",
            "step_ref",
            "subject",
            "observed_window",
            "quality",
            "provenance",
            "payload",
        ]
        for field in required:
            self.assertIn(field, finding, f"Missing field: {field}")

    def test_finding_id_uses_stable_hash(self) -> None:
        finding = self._make_finding_base()
        expected = make_finding_id("art_obs_01", "observation", "value")
        self.assertEqual(finding["finding_id"], expected)

    def test_subject_slice_is_dict_not_null(self) -> None:
        finding = self._make_finding_base()
        self.assertIsInstance(finding["subject"]["slice"], dict)

    def test_quality_warnings_is_list_not_null(self) -> None:
        finding = self._make_finding_base()
        self.assertIsInstance(finding["quality"]["quality_warnings"], list)

    def test_provenance_expresses_artifact_item_ref(self) -> None:
        finding = self._make_finding_base()
        ref = finding["provenance"]["artifact_item_ref"]
        self.assertIn("collection", ref)
        self.assertIn("index", ref)
        self.assertIn("key", ref)


# ---------------------------------------------------------------------------
# Concrete finding subtypes and AnyFinding union (Phase 4a-4)
# ---------------------------------------------------------------------------


class TestConcreteSubtypesAndAnyFinding(unittest.TestCase):
    """Phase 4a-4: concrete finding subtypes narrow finding_type/payload correctly."""

    def test_observation_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(ObservationFinding)
        self.assertIn("observation", typing.get_args(hints["finding_type"]))

    def test_delta_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(DeltaFinding)
        self.assertIn("delta", typing.get_args(hints["finding_type"]))

    def test_decomposition_item_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(DecompositionItemFinding)
        self.assertIn("decomposition_item", typing.get_args(hints["finding_type"]))

    def test_anomaly_candidate_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(AnomalyCandidateFinding)
        self.assertIn("anomaly_candidate", typing.get_args(hints["finding_type"]))

    def test_correlation_result_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(CorrelationResultFinding)
        self.assertIn("correlation_result", typing.get_args(hints["finding_type"]))

    def test_test_result_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(TestResultFinding)
        self.assertIn("test_result", typing.get_args(hints["finding_type"]))

    def test_forecast_point_finding_type_annotation(self) -> None:
        hints = typing.get_type_hints(ForecastPointFinding)
        self.assertIn("forecast_point", typing.get_args(hints["finding_type"]))

    def test_any_finding_covers_all_seven_subtypes(self) -> None:
        expected = {
            ObservationFinding,
            DeltaFinding,
            DecompositionItemFinding,
            AnomalyCandidateFinding,
            CorrelationResultFinding,
            TestResultFinding,
            ForecastPointFinding,
        }
        self.assertEqual(set(typing.get_args(AnyFinding)), expected)

    def test_observation_finding_payload_is_union_not_dict(self) -> None:
        """payload must be typed as ObservationPayload (a union of 4 subtypes)."""
        hints = typing.get_type_hints(ObservationFinding)
        self.assertGreater(len(typing.get_args(hints["payload"])), 1)

    def test_extraction_result_findings_typed_as_any_finding_list(self) -> None:
        """FindingExtractionResult.findings must be list[AnyFinding], not list[FindingBase]."""
        hints = typing.get_type_hints(FindingExtractionResult)
        list_args = typing.get_args(hints["findings"])
        elem_type = list_args[0]
        self.assertEqual(set(typing.get_args(elem_type)), set(typing.get_args(AnyFinding)))


if __name__ == "__main__":
    unittest.main()
