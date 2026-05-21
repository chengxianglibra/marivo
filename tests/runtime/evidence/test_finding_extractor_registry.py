"""Tests for the canonical finding extractor registry (Phase 4b-2).

Covers acceptance criteria:
- Extractor routing uses (artifact_type, artifact_schema_version) as dispatch key
- NOT step_type — two artifacts of the same step may differ in schema version
- NULL artifact_schema_version falls back to "v1" by find(), NOT by get()
- register() raises ValueError on duplicate unless override=True
- snapshot() returns stable, sorted, auditable output
- default_finding_registry is an importable module-level singleton, starts empty
"""

from __future__ import annotations

import contextlib
import unittest
from typing import Any

from marivo.core.evidence.canonical_finding import (
    AnyFinding,
    FindingExtractionResult,
    StepRef,
)
from marivo.core.evidence.family_contract import FamilyEmptyError
from marivo.runtime.evidence.finding_extractor_registry import (
    FindingExtractor,
    FindingExtractorRegistry,
    default_finding_registry,
    validate_extraction_result,
    validate_for_commit,
)

# ---------------------------------------------------------------------------
# Stub extractors used across multiple test classes
# ---------------------------------------------------------------------------


class _ObsV1Extractor(FindingExtractor):
    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "obs_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        findings: list[AnyFinding] = []
        return {
            "findings": findings,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


class _ObsV2Extractor(FindingExtractor):
    artifact_type = "observation_artifact"
    artifact_schema_version = "v2"
    family = "observe"
    extractor_name = "obs_v2"
    extractor_version = "2.0.0"
    finding_schema_version = "v2"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        findings: list[AnyFinding] = []
        return {
            "findings": findings,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


class _CompareV1Extractor(FindingExtractor):
    artifact_type = "compare_artifact"
    artifact_schema_version = "v1"
    family = "compare"
    extractor_name = "compare_v1"
    extractor_version = "1.0.0"
    # finding_schema_version intentionally not set — inherits None from ABC

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        findings: list[AnyFinding] = []
        return {
            "findings": findings,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


class _ObsNoneExtractor(FindingExtractor):
    artifact_type = "observation_artifact"
    artifact_schema_version = None
    family = "observe"
    extractor_name = "obs_none"
    extractor_version = "1.0.0"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        findings: list[AnyFinding] = []
        return {
            "findings": findings,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


# ---------------------------------------------------------------------------
# TestFindingExtractorABC
# ---------------------------------------------------------------------------


class TestFindingExtractorABC(unittest.TestCase):
    def test_cannot_instantiate_abstract_extractor(self) -> None:
        with self.assertRaises(TypeError):
            FindingExtractor()  # type: ignore[abstract]

    def test_concrete_subclass_without_extract_raises_type_error(self) -> None:
        class _Partial(FindingExtractor):
            artifact_type = "x"
            artifact_schema_version = "v1"
            family = "observe"
            extractor_name = "x"
            extractor_version = "0.0.1"
            # extract() not implemented

        with self.assertRaises(TypeError):
            _Partial()  # type: ignore[abstract]

    def test_concrete_subclass_with_extract_can_be_instantiated(self) -> None:
        extractor = _ObsV1Extractor()
        self.assertIsInstance(extractor, FindingExtractor)

    def test_finding_schema_version_defaults_to_none_when_not_declared(self) -> None:
        # _CompareV1Extractor does not declare finding_schema_version
        extractor = _CompareV1Extractor()
        self.assertIsNone(extractor.finding_schema_version)

    def test_finding_schema_version_can_be_declared_by_subclass(self) -> None:
        extractor = _ObsV1Extractor()
        self.assertEqual(extractor.finding_schema_version, "v1")

    def test_missing_artifact_type_raises_type_error_at_class_definition(self) -> None:
        with self.assertRaises(TypeError):

            class _MissingArtifactType(FindingExtractor):
                artifact_schema_version = "v1"
                extractor_name = "x"
                extractor_version = "0.0.1"

                def extract(self, *a: Any, **kw: Any) -> FindingExtractionResult:  # type: ignore[override]
                    return {
                        "findings": [],
                        "extractor_name": "x",
                        "extractor_version": "0.0.1",
                        "artifact_schema_version": "v1",
                        "finding_count": 0,
                    }

    def test_missing_extractor_name_raises_type_error_at_class_definition(self) -> None:
        with self.assertRaises(TypeError):

            class _MissingExtractorName(FindingExtractor):
                artifact_type = "observation_artifact"
                artifact_schema_version = "v1"
                extractor_version = "0.0.1"

                def extract(self, *a: Any, **kw: Any) -> FindingExtractionResult:  # type: ignore[override]
                    return {
                        "findings": [],
                        "extractor_name": "",
                        "extractor_version": "0.0.1",
                        "artifact_schema_version": "v1",
                        "finding_count": 0,
                    }

    def test_type_error_message_names_the_missing_attribute(self) -> None:
        try:

            class _MissingVersion(FindingExtractor):
                artifact_type = "observation_artifact"
                artifact_schema_version = "v1"
                extractor_name = "x"
                # extractor_version intentionally omitted

                def extract(self, *a: Any, **kw: Any) -> FindingExtractionResult:  # type: ignore[override]
                    return {
                        "findings": [],
                        "extractor_name": "x",
                        "extractor_version": "",
                        "artifact_schema_version": "v1",
                        "finding_count": 0,
                    }

        except TypeError as exc:
            self.assertIn("extractor_version", str(exc))
        else:
            self.fail("Expected TypeError was not raised")

    def test_all_required_classvars_present_does_not_raise(self) -> None:
        # Defining a complete subclass must not raise even before instantiation
        class _Complete(FindingExtractor):
            artifact_type = "observation_artifact"
            artifact_schema_version = "v1"
            family = "observe"
            extractor_name = "complete"
            extractor_version = "1.0.0"

            def extract(self, *a: Any, **kw: Any) -> FindingExtractionResult:  # type: ignore[override]
                return {
                    "findings": [],
                    "extractor_name": self.extractor_name,
                    "extractor_version": self.extractor_version,
                    "artifact_schema_version": self.artifact_schema_version,
                    "finding_count": 0,
                }

        # No exception → definition succeeded
        self.assertTrue(issubclass(_Complete, FindingExtractor))


# ---------------------------------------------------------------------------
# TestFindingExtractorRegistryBasic
# ---------------------------------------------------------------------------


class TestFindingExtractorRegistryBasic(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FindingExtractorRegistry()

    def test_register_and_get_round_trip(self) -> None:
        extractor = _ObsV1Extractor()
        self.registry.register(extractor)
        result = self.registry.get("observation_artifact", "v1")
        self.assertIs(result, extractor)

    def test_find_with_explicit_schema_version(self) -> None:
        extractor = _ObsV1Extractor()
        self.registry.register(extractor)
        result = self.registry.find("observation_artifact", "v1")
        self.assertIs(result, extractor)

    def test_find_returns_none_for_unregistered(self) -> None:
        result = self.registry.find("observation_artifact", "v1")
        self.assertIsNone(result)

    def test_get_raises_key_error_for_unregistered(self) -> None:
        with self.assertRaises(KeyError):
            self.registry.get("observation_artifact", "v1")

    def test_key_error_message_includes_artifact_type(self) -> None:
        try:
            self.registry.get("observation_artifact", "v1")
        except KeyError as exc:
            self.assertIn("observation_artifact", str(exc))

    def test_key_error_message_includes_schema_version(self) -> None:
        try:
            self.registry.get("observation_artifact", "v1")
        except KeyError as exc:
            self.assertIn("v1", str(exc))

    def test_key_error_message_lists_registered_keys(self) -> None:
        self.registry.register(_CompareV1Extractor())
        try:
            self.registry.get("observation_artifact", "v1")
        except KeyError as exc:
            self.assertIn("compare_artifact", str(exc))

    def test_key_error_message_lists_mixed_none_and_string_versions(self) -> None:
        self.registry.register(_ObsNoneExtractor())
        self.registry.register(_CompareV1Extractor())
        try:
            self.registry.get("not_registered", "v1")
        except KeyError as exc:
            message = str(exc)
            self.assertIn("('observation_artifact', None)", message)
            self.assertIn("('compare_artifact', 'v1')", message)
        else:
            self.fail("Expected KeyError was not raised")

    def test_registered_keys_is_empty_on_new_registry(self) -> None:
        self.assertEqual(self.registry.registered_keys(), [])

    def test_registered_keys_after_registration(self) -> None:
        self.registry.register(_ObsV1Extractor())
        self.registry.register(_CompareV1Extractor())
        keys = self.registry.registered_keys()
        self.assertIn(("compare_artifact", "v1"), keys)
        self.assertIn(("observation_artifact", "v1"), keys)

    def test_registered_keys_is_sorted(self) -> None:
        self.registry.register(_ObsV1Extractor())
        self.registry.register(_CompareV1Extractor())
        keys = self.registry.registered_keys()
        self.assertEqual(keys, sorted(keys))


# ---------------------------------------------------------------------------
# TestFindingExtractorRegistryNullVersion
# ---------------------------------------------------------------------------


class TestFindingExtractorRegistryNullVersion(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObsV1Extractor())

    def test_find_with_none_schema_version_falls_back_to_v1(self) -> None:
        result = self.registry.find("observation_artifact", None)
        self.assertIsInstance(result, _ObsV1Extractor)

    def test_artifact_registered_under_v1_found_via_find_none(self) -> None:
        extractor = self.registry.find("observation_artifact", None)
        self.assertIsNotNone(extractor)
        assert extractor is not None
        self.assertEqual(extractor.extractor_name, "obs_v1")

    def test_find_with_empty_string_is_not_treated_as_none(self) -> None:
        # Empty string "" is looked up literally — NOT treated as the "v1" fallback
        result = self.registry.find("observation_artifact", "")
        self.assertIsNone(result)

    def test_get_does_not_apply_null_fallback(self) -> None:
        # get() is strict — passing "v99" must raise KeyError even if "v1" exists
        with self.assertRaises(KeyError):
            self.registry.get("observation_artifact", "v99")

    def test_find_none_does_not_match_empty_string_key(self) -> None:
        # Register under "" (unusual but legal), then find(None) must NOT hit it
        class _EmptyVersionExtractor(FindingExtractor):
            artifact_type = "observation_artifact"
            artifact_schema_version = ""
            family = "observe"
            extractor_name = "obs_empty"
            extractor_version = "0.0.1"

            def extract(self, *args: Any, **kwargs: Any) -> FindingExtractionResult:
                return {
                    "findings": [],
                    "extractor_name": self.extractor_name,
                    "extractor_version": self.extractor_version,
                    "artifact_schema_version": self.artifact_schema_version,
                    "finding_count": 0,
                }

        fresh = FindingExtractorRegistry()
        fresh.register(_EmptyVersionExtractor())
        result = fresh.find("observation_artifact", None)
        # None falls back to "v1", not ""; should not find the "" extractor
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestFindingExtractorRegistryVersionRouting
# ---------------------------------------------------------------------------


class TestFindingExtractorRegistryVersionRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObsV1Extractor())
        self.registry.register(_ObsV2Extractor())
        self.registry.register(_CompareV1Extractor())

    def test_same_artifact_type_different_versions_route_independently(self) -> None:
        v1 = self.registry.get("observation_artifact", "v1")
        v2 = self.registry.get("observation_artifact", "v2")
        self.assertIsInstance(v1, _ObsV1Extractor)
        self.assertIsInstance(v2, _ObsV2Extractor)
        self.assertIsNot(v1, v2)

    def test_different_artifact_types_route_independently(self) -> None:
        obs = self.registry.get("observation_artifact", "v1")
        cmp = self.registry.get("compare_artifact", "v1")
        self.assertIsInstance(obs, _ObsV1Extractor)
        self.assertIsInstance(cmp, _CompareV1Extractor)

    def test_routing_is_by_artifact_type_and_schema_version_not_step_type(self) -> None:
        # Demonstrate: two extractors with same artifact_type but different schema
        # versions are distinct routes.  step_type is NOT in the dispatch key.
        class _AltStepObs(FindingExtractor):
            # Same artifact_type as _ObsV1Extractor, different schema version.
            # Even if the producing step_type were the same, the registry
            # distinguishes them by schema version alone.
            artifact_type = "observation_artifact"
            artifact_schema_version = "v3"
            family = "observe"
            extractor_name = "obs_alt_step"
            extractor_version = "1.0.0"

            def extract(self, *args: Any, **kwargs: Any) -> FindingExtractionResult:
                return {
                    "findings": [],
                    "extractor_name": self.extractor_name,
                    "extractor_version": self.extractor_version,
                    "artifact_schema_version": self.artifact_schema_version,
                    "finding_count": 0,
                }

        alt = _AltStepObs()
        self.registry.register(alt)
        result = self.registry.get("observation_artifact", "v3")
        self.assertIs(result, alt)
        # v1 is still routed to the original extractor
        self.assertIsInstance(self.registry.get("observation_artifact", "v1"), _ObsV1Extractor)

    def test_find_returns_none_for_missing_version(self) -> None:
        result = self.registry.find("observation_artifact", "v99")
        self.assertIsNone(result)

    def test_find_returns_none_for_missing_artifact_type(self) -> None:
        result = self.registry.find("forecast_artifact", "v1")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestFindingExtractorRegistryDuplicates
# ---------------------------------------------------------------------------


class TestFindingExtractorRegistryDuplicates(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FindingExtractorRegistry()
        self.original = _ObsV1Extractor()
        self.registry.register(self.original)

    def test_duplicate_registration_raises_value_error(self) -> None:
        duplicate = _ObsV1Extractor()
        with self.assertRaises(ValueError):
            self.registry.register(duplicate)

    def test_duplicate_value_error_names_existing_extractor(self) -> None:
        duplicate = _ObsV1Extractor()
        try:
            self.registry.register(duplicate)
        except ValueError as exc:
            self.assertIn("obs_v1", str(exc))

    def test_original_still_registered_after_failed_duplicate(self) -> None:
        duplicate = _ObsV1Extractor()
        with contextlib.suppress(ValueError):
            self.registry.register(duplicate)
        result = self.registry.get("observation_artifact", "v1")
        self.assertIs(result, self.original)

    def test_override_true_replaces_existing(self) -> None:
        replacement = _ObsV1Extractor()
        self.registry.register(replacement, override=True)
        result = self.registry.get("observation_artifact", "v1")
        self.assertIs(result, replacement)
        self.assertIsNot(result, self.original)

    def test_override_false_is_the_default(self) -> None:
        # Calling register() without override= should raise ValueError
        with self.assertRaises(ValueError):
            self.registry.register(_ObsV1Extractor())


# ---------------------------------------------------------------------------
# TestFindingExtractorRegistrySnapshot
# ---------------------------------------------------------------------------


class TestFindingExtractorRegistrySnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FindingExtractorRegistry()

    def test_empty_registry_snapshot_returns_empty_list(self) -> None:
        self.assertEqual(self.registry.snapshot(), [])

    def test_snapshot_includes_required_fields(self) -> None:
        self.registry.register(_ObsV1Extractor())
        entries = self.registry.snapshot()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIn("artifact_type", entry)
        self.assertIn("artifact_schema_version", entry)
        self.assertIn("family", entry)
        self.assertIn("extractor_name", entry)
        self.assertIn("extractor_version", entry)
        self.assertIn("finding_schema_version", entry)

    def test_snapshot_values_match_registered_extractor(self) -> None:
        self.registry.register(_ObsV1Extractor())
        entry = self.registry.snapshot()[0]
        self.assertEqual(entry["artifact_type"], "observation_artifact")
        self.assertEqual(entry["artifact_schema_version"], "v1")
        self.assertEqual(entry["family"], "observe")
        self.assertEqual(entry["extractor_name"], "obs_v1")
        self.assertEqual(entry["extractor_version"], "1.0.0")
        self.assertEqual(entry["finding_schema_version"], "v1")

    def test_snapshot_finding_schema_version_is_none_when_not_declared(self) -> None:
        self.registry.register(_CompareV1Extractor())
        entry = self.registry.snapshot()[0]
        self.assertIsNone(entry["finding_schema_version"])

    def test_snapshot_sorted_by_artifact_type_then_schema_version(self) -> None:
        self.registry.register(_ObsV2Extractor())
        self.registry.register(_CompareV1Extractor())
        self.registry.register(_ObsV1Extractor())
        entries = self.registry.snapshot()
        keys = [(e["artifact_type"], e["artifact_schema_version"]) for e in entries]
        self.assertEqual(keys, sorted(keys))

    def test_snapshot_stability_across_multiple_calls(self) -> None:
        self.registry.register(_ObsV1Extractor())
        self.registry.register(_CompareV1Extractor())
        snap1 = self.registry.snapshot()
        snap2 = self.registry.snapshot()
        self.assertEqual(snap1, snap2)

    def test_snapshot_does_not_contain_callable_extract(self) -> None:
        self.registry.register(_ObsV1Extractor())
        entry = self.registry.snapshot()[0]
        self.assertNotIn("extract", entry)


# ---------------------------------------------------------------------------
# TestDefaultFindingRegistry
# ---------------------------------------------------------------------------


class TestDefaultFindingRegistry(unittest.TestCase):
    def test_default_finding_registry_is_correct_type(self) -> None:
        self.assertIsInstance(default_finding_registry, FindingExtractorRegistry)

    def test_default_finding_registry_contains_observe_extractor(self) -> None:
        self.assertIn(("metric_frame", None), default_finding_registry.registered_keys())

    def test_default_finding_registry_contains_detect_extractor(self) -> None:
        # Phase 4d-2: detect extractor registered under ("anomaly_candidates", "v1").
        self.assertIn(("anomaly_candidates", "v1"), default_finding_registry.registered_keys())

    def test_default_finding_registry_is_module_level_singleton(self) -> None:
        from marivo.runtime.evidence.finding_extractor_registry import (
            default_finding_registry as dr2,
        )

        self.assertIs(default_finding_registry, dr2)


# ---------------------------------------------------------------------------
# TestValidateExtractionResult
# ---------------------------------------------------------------------------


class TestValidateExtractionResult(unittest.TestCase):
    def _make_result(
        self,
        findings: list[AnyFinding],
        finding_count: int,
        extractor_name: str = "test_extractor",
    ) -> FindingExtractionResult:
        return {
            "findings": findings,
            "extractor_name": extractor_name,
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": finding_count,
        }

    def test_consistent_empty_result_passes(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        validate_extraction_result(result)  # must not raise

    def test_consistent_nonempty_result_passes(self) -> None:
        # We only need a minimal AnyFinding-shaped dict for the count check
        findings: list[AnyFinding] = []  # empty list, count=0 path tested above
        result = self._make_result(findings=findings, finding_count=0)
        validate_extraction_result(result)  # must not raise

    def test_count_greater_than_findings_raises_value_error(self) -> None:
        result = self._make_result(findings=[], finding_count=1)
        with self.assertRaises(ValueError):
            validate_extraction_result(result)

    def test_count_less_than_findings_raises_value_error(self) -> None:
        # Build a minimal valid-looking finding to get len > 0
        # We bypass type checking since we're only testing the count invariant.
        fake_finding: Any = {"finding_id": "fnd_abc", "finding_type": "observation"}
        result: FindingExtractionResult = {
            "findings": [fake_finding],
            "extractor_name": "test_extractor",
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": 0,  # wrong: should be 1
        }
        with self.assertRaises(ValueError):
            validate_extraction_result(result)

    def test_error_message_includes_declared_count(self) -> None:
        result = self._make_result(findings=[], finding_count=3)
        try:
            validate_extraction_result(result)
        except ValueError as exc:
            self.assertIn("3", str(exc))
        else:
            self.fail("Expected ValueError was not raised")

    def test_error_message_includes_actual_count(self) -> None:
        result = self._make_result(findings=[], finding_count=3)
        try:
            validate_extraction_result(result)
        except ValueError as exc:
            self.assertIn("0", str(exc))
        else:
            self.fail("Expected ValueError was not raised")

    def test_error_message_includes_extractor_name(self) -> None:
        result = self._make_result(findings=[], finding_count=1, extractor_name="bad_extractor")
        try:
            validate_extraction_result(result)
        except ValueError as exc:
            self.assertIn("bad_extractor", str(exc))
        else:
            self.fail("Expected ValueError was not raised")


# ---------------------------------------------------------------------------
# TestValidateForCommit (Phase 4b-4)
# ---------------------------------------------------------------------------


class TestValidateForCommit(unittest.TestCase):
    """Tests for the unified commit-path validation gate validate_for_commit().

    Covers:
    - allow-empty families (observe, detect) pass with empty result
    - non-empty families raise FamilyEmptyError with empty result
    - any family passes with a non-empty result
    - count/len mismatch raises ValueError before family check
    - unknown family with empty result raises FamilyEmptyError (fail-safe)
    - ordering guarantee: ValueError raised before FamilyEmptyError
    """

    def _make_result(
        self,
        findings: list[Any],
        finding_count: int,
        extractor_name: str = "gate_test_extractor",
    ) -> FindingExtractionResult:
        return {
            "findings": findings,
            "extractor_name": extractor_name,
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": finding_count,
        }

    def _stub_finding(self) -> Any:
        return {"finding_id": "fnd_abc", "finding_type": "observation"}

    # ------------------------------------------------------------------
    # Allow-empty families: count=0 must pass
    # ------------------------------------------------------------------

    def test_observe_empty_passes(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        validate_for_commit("observe", result)  # must not raise

    def test_detect_empty_passes(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        validate_for_commit("detect", result)  # must not raise

    # ------------------------------------------------------------------
    # Non-empty families: count=0 must raise FamilyEmptyError
    # ------------------------------------------------------------------

    def test_compare_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError) as ctx:
            validate_for_commit("compare", result)
        self.assertEqual(ctx.exception.family, "compare")

    def test_decompose_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError) as ctx:
            validate_for_commit("decompose", result)
        self.assertEqual(ctx.exception.family, "decompose")

    def test_correlate_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("correlate", result)

    def test_test_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("test", result)

    def test_forecast_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("forecast", result)

    # ------------------------------------------------------------------
    # Non-empty result: all families must pass
    # ------------------------------------------------------------------

    def test_nonempty_result_passes_for_all_families(self) -> None:
        finding = self._stub_finding()
        result: FindingExtractionResult = {
            "findings": [finding],
            "extractor_name": "gate_test_extractor",
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": 1,
        }
        for family in (
            "observe",
            "detect",
            "compare",
            "decompose",
            "correlate",
            "test",
            "forecast",
        ):
            with self.subTest(family=family):
                validate_for_commit(family, result)  # must not raise

    # ------------------------------------------------------------------
    # Count/len mismatch: ValueError raised BEFORE family check
    # ------------------------------------------------------------------

    def test_count_greater_than_len_raises_value_error_for_allow_empty_family(self) -> None:
        # finding_count=1 but findings=[] — consistency error even for observe
        result = self._make_result(findings=[], finding_count=1)
        with self.assertRaises(ValueError) as ctx:
            validate_for_commit("observe", result)
        self.assertNotIsInstance(ctx.exception, FamilyEmptyError)

    def test_count_less_than_len_raises_value_error_for_non_empty_family(self) -> None:
        # finding_count=0 but findings=[...] — consistency error for compare too
        finding = self._stub_finding()
        result: FindingExtractionResult = {
            "findings": [finding],
            "extractor_name": "gate_test_extractor",
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": 0,  # wrong: should be 1
        }
        with self.assertRaises(ValueError) as ctx:
            validate_for_commit("compare", result)
        self.assertNotIsInstance(ctx.exception, FamilyEmptyError)

    def test_ordering_value_error_before_family_empty_error(self) -> None:
        # finding_count=1 but findings=[] for a non-empty family:
        # internal consistency fires first (ValueError), not FamilyEmptyError
        result = self._make_result(findings=[], finding_count=1)
        with self.assertRaises(ValueError) as ctx:
            validate_for_commit("compare", result)
        self.assertNotIsInstance(ctx.exception, FamilyEmptyError)

    # ------------------------------------------------------------------
    # Unknown family fail-safe
    # ------------------------------------------------------------------

    def test_unknown_family_empty_raises_family_empty_error(self) -> None:
        result = self._make_result(findings=[], finding_count=0)
        with self.assertRaises(FamilyEmptyError) as ctx:
            validate_for_commit("unknown_future_family", result)
        self.assertEqual(ctx.exception.family, "unknown_future_family")

    def test_unknown_family_nonempty_passes(self) -> None:
        finding = self._stub_finding()
        result: FindingExtractionResult = {
            "findings": [finding],
            "extractor_name": "gate_test_extractor",
            "extractor_version": "1.0.0",
            "artifact_schema_version": "v1",
            "finding_count": 1,
        }
        validate_for_commit("unknown_future_family", result)  # must not raise
