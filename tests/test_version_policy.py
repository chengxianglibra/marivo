"""Tests for Phase 4h-1: version bump classification and migration status vocabulary."""

from __future__ import annotations

import unittest

from app.evidence_engine.version_policy import (
    MIGRATION_STATUS_LABELS,
    VERSION_AXES,
    classify_version_bump,
)

# Expected axis names registered in VERSION_AXES.
_EXPECTED_AXES = {
    "artifact_schema_version",
    "extractor_version",
    "template_version",
    "derivation_version",
    "rule_version",
    "policy_version",
}


class TestVersionAxesCoverage(unittest.TestCase):
    """All expected version axes must be registered."""

    def test_all_expected_axes_registered(self) -> None:
        registered = {d["axis"] for d in VERSION_AXES}
        self.assertEqual(registered, _EXPECTED_AXES)

    def test_each_axis_has_required_fields(self) -> None:
        for decl in VERSION_AXES:
            with self.subTest(axis=decl["axis"]):
                self.assertIn(
                    decl["bump_class_on_change"],
                    ("forward_compatible", "replay_required", "identity_breaking"),
                )
                self.assertIsInstance(decl["current_version"], str)
                self.assertTrue(decl["current_version"])
                self.assertIsInstance(decl["description"], str)
                self.assertTrue(decl["description"])


class TestBumpClassification(unittest.TestCase):
    """classify_version_bump returns the correct class for each axis."""

    def test_artifact_schema_version_is_forward_compatible(self) -> None:
        self.assertEqual(
            classify_version_bump("artifact_schema_version", "v1", "v2"),
            "forward_compatible",
        )

    def test_extractor_version_is_replay_required(self) -> None:
        self.assertEqual(
            classify_version_bump("extractor_version", "v1", "v2"),
            "replay_required",
        )

    def test_rule_version_is_replay_required(self) -> None:
        self.assertEqual(
            classify_version_bump("rule_version", "v1", "v2"),
            "replay_required",
        )

    def test_policy_version_is_replay_required(self) -> None:
        self.assertEqual(
            classify_version_bump("policy_version", "v1", "v2"),
            "replay_required",
        )

    def test_template_version_is_identity_breaking(self) -> None:
        self.assertEqual(
            classify_version_bump("template_version", "v1", "v2"),
            "identity_breaking",
        )

    def test_derivation_version_is_identity_breaking(self) -> None:
        self.assertEqual(
            classify_version_bump("derivation_version", "v1", "v2"),
            "identity_breaking",
        )

    def test_same_version_pair_returns_class_unchanged(self) -> None:
        # from_version == to_version is not a real bump, but the function
        # still returns the axis class (no special "no_change" case in v1).
        result = classify_version_bump("extractor_version", "v1", "v1")
        self.assertEqual(result, "replay_required")


class TestMigrationStatusLabels(unittest.TestCase):
    """MIGRATION_STATUS_LABELS must enumerate the three runtime-truth labels."""

    def test_contains_migration_required(self) -> None:
        self.assertIn("migration_required", MIGRATION_STATUS_LABELS)

    def test_contains_migration_in_progress(self) -> None:
        self.assertIn("migration_in_progress", MIGRATION_STATUS_LABELS)

    def test_contains_migration_blocked(self) -> None:
        self.assertIn("migration_blocked", MIGRATION_STATUS_LABELS)

    def test_exactly_three_labels(self) -> None:
        self.assertEqual(len(MIGRATION_STATUS_LABELS), 3)

    def test_is_frozenset(self) -> None:
        self.assertIsInstance(MIGRATION_STATUS_LABELS, frozenset)


class TestUnknownAxisRaises(unittest.TestCase):
    """classify_version_bump raises ValueError for unknown axes."""

    def test_raises_value_error_for_unknown_axis(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            classify_version_bump("nonexistent_axis", "v1", "v2")
        self.assertIn("nonexistent_axis", str(ctx.exception))

    def test_error_message_lists_registered_axes(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            classify_version_bump("bad_axis", "v1", "v2")
        msg = str(ctx.exception)
        for axis in _EXPECTED_AXES:
            self.assertIn(axis, msg)


if __name__ == "__main__":
    unittest.main()
