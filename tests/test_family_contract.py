"""Tests for app/evidence_engine/family_contract.py — Phase 4a-4.

Covers:
- check_finding_count: allow-empty families pass with count=0
- check_finding_count: mandatory-non-empty families raise FamilyEmptyError
- check_finding_count: all families pass with count > 0
- FamilyEmptyError.family attribute correctness
- FAMILY_ALLOWS_EMPTY completeness (all 7 canonical families present)
- FAMILY_ALLOWS_EMPTY value correctness (observe/detect=True; rest=False)
- Unknown family defaults to non-empty-required (fail-safe)
- FindingExtractionResult TypedDict field presence
- ArtifactFamily Literal values align with FAMILY_ALLOWS_EMPTY keys
"""

import unittest

from marivo.evidence_engine.canonical_finding import FindingExtractionResult
from marivo.evidence_engine.family_contract import (
    FAMILY_ALLOWS_EMPTY,
    FamilyEmptyError,
    check_finding_count,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOW_EMPTY_FAMILIES = ("observe", "detect")
_NON_EMPTY_FAMILIES = ("compare", "decompose", "correlate", "test", "forecast")
_ALL_CANONICAL_FAMILIES = _ALLOW_EMPTY_FAMILIES + _NON_EMPTY_FAMILIES


class TestFamilyAllowsEmptyValues(unittest.TestCase):
    """FAMILY_ALLOWS_EMPTY encodes D4 correctly."""

    def test_observe_allows_empty(self) -> None:
        self.assertTrue(FAMILY_ALLOWS_EMPTY["observe"])

    def test_detect_allows_empty(self) -> None:
        self.assertTrue(FAMILY_ALLOWS_EMPTY["detect"])

    def test_compare_requires_non_empty(self) -> None:
        self.assertFalse(FAMILY_ALLOWS_EMPTY["compare"])

    def test_decompose_requires_non_empty(self) -> None:
        self.assertFalse(FAMILY_ALLOWS_EMPTY["decompose"])

    def test_correlate_requires_non_empty(self) -> None:
        self.assertFalse(FAMILY_ALLOWS_EMPTY["correlate"])

    def test_test_requires_non_empty(self) -> None:
        self.assertFalse(FAMILY_ALLOWS_EMPTY["test"])

    def test_forecast_requires_non_empty(self) -> None:
        self.assertFalse(FAMILY_ALLOWS_EMPTY["forecast"])

    def test_covers_all_seven_canonical_families(self) -> None:
        """FAMILY_ALLOWS_EMPTY must include every canonical artifact family."""
        self.assertEqual(
            set(FAMILY_ALLOWS_EMPTY.keys()),
            set(_ALL_CANONICAL_FAMILIES),
        )

    def test_exactly_two_allow_empty_families(self) -> None:
        allowed = [f for f, ok in FAMILY_ALLOWS_EMPTY.items() if ok]
        self.assertEqual(sorted(allowed), sorted(_ALLOW_EMPTY_FAMILIES))


class TestCheckFindingCountAllowEmpty(unittest.TestCase):
    """Allow-empty families must NOT raise when count == 0."""

    def test_observe_empty_is_ok(self) -> None:
        check_finding_count("observe", 0)  # must not raise

    def test_detect_empty_is_ok(self) -> None:
        check_finding_count("detect", 0)  # must not raise


class TestCheckFindingCountNonEmpty(unittest.TestCase):
    """Mandatory-non-empty families MUST raise FamilyEmptyError when count == 0."""

    def _assert_raises(self, family: str) -> FamilyEmptyError:
        with self.assertRaises(FamilyEmptyError) as ctx:
            check_finding_count(family, 0)
        return ctx.exception

    def test_compare_empty_raises(self) -> None:
        exc = self._assert_raises("compare")
        self.assertEqual(exc.family, "compare")

    def test_decompose_empty_raises(self) -> None:
        exc = self._assert_raises("decompose")
        self.assertEqual(exc.family, "decompose")

    def test_correlate_empty_raises(self) -> None:
        exc = self._assert_raises("correlate")
        self.assertEqual(exc.family, "correlate")

    def test_test_empty_raises(self) -> None:
        exc = self._assert_raises("test")
        self.assertEqual(exc.family, "test")

    def test_forecast_empty_raises(self) -> None:
        exc = self._assert_raises("forecast")
        self.assertEqual(exc.family, "forecast")


class TestCheckFindingCountPositive(unittest.TestCase):
    """All families must NOT raise when count > 0."""

    def test_all_families_pass_with_one_finding(self) -> None:
        for family in _ALL_CANONICAL_FAMILIES:
            with self.subTest(family=family):
                check_finding_count(family, 1)  # must not raise

    def test_all_families_pass_with_multiple_findings(self) -> None:
        for family in _ALL_CANONICAL_FAMILIES:
            with self.subTest(family=family):
                check_finding_count(family, 5)  # must not raise


class TestFamilyEmptyError(unittest.TestCase):
    """FamilyEmptyError behaves correctly as a ValueError subclass."""

    def test_is_value_error(self) -> None:
        exc = FamilyEmptyError("compare")
        self.assertIsInstance(exc, ValueError)

    def test_family_attribute(self) -> None:
        for family in _NON_EMPTY_FAMILIES:
            with self.subTest(family=family):
                exc = FamilyEmptyError(family)
                self.assertEqual(exc.family, family)

    def test_message_contains_family_name(self) -> None:
        exc = FamilyEmptyError("forecast")
        self.assertIn("forecast", str(exc))

    def test_message_mentions_observe_and_detect(self) -> None:
        """Error message should hint which families ARE allowed empty."""
        exc = FamilyEmptyError("compare")
        msg = str(exc)
        self.assertIn("observe", msg)
        self.assertIn("detect", msg)


class TestUnknownFamilyFailSafe(unittest.TestCase):
    """Unknown families default to non-empty-required (fail-safe)."""

    def test_unknown_family_empty_raises(self) -> None:
        with self.assertRaises(FamilyEmptyError) as ctx:
            check_finding_count("unknown_future_family", 0)
        self.assertEqual(ctx.exception.family, "unknown_future_family")

    def test_unknown_family_non_empty_passes(self) -> None:
        check_finding_count("unknown_future_family", 3)  # must not raise


class TestFindingExtractionResultContract(unittest.TestCase):
    """FindingExtractionResult TypedDict has the required fields."""

    def test_required_fields_present(self) -> None:
        required = {
            "findings",
            "extractor_name",
            "extractor_version",
            "artifact_schema_version",
            "finding_count",
        }
        annotations = FindingExtractionResult.__annotations__
        self.assertTrue(
            required.issubset(annotations.keys()),
            f"Missing fields: {required - annotations.keys()}",
        )

    def test_is_typed_dict(self) -> None:
        # TypedDict classes have __annotations__
        self.assertIsInstance(FindingExtractionResult.__annotations__, dict)

    def test_finding_count_field_exists(self) -> None:
        self.assertIn("finding_count", FindingExtractionResult.__annotations__)

    def test_artifact_schema_version_field_exists(self) -> None:
        self.assertIn("artifact_schema_version", FindingExtractionResult.__annotations__)


class TestCheckFindingCountNegative(unittest.TestCase):
    """Negative count is treated identically to zero (raises for non-empty families)."""

    def test_negative_count_raises_for_non_empty_family(self) -> None:
        with self.assertRaises(FamilyEmptyError) as ctx:
            check_finding_count("compare", -1)
        self.assertEqual(ctx.exception.family, "compare")

    def test_negative_count_is_ok_for_allow_empty_family(self) -> None:
        check_finding_count("observe", -1)  # must not raise

    def test_negative_count_raises_for_unknown_family(self) -> None:
        with self.assertRaises(FamilyEmptyError):
            check_finding_count("unknown_future_family", -1)


if __name__ == "__main__":
    unittest.main()
