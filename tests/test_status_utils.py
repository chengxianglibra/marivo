"""Tests for shared status derivation utilities."""

import unittest

from app.semantic_runtime.status_utils import (
    derive_lifecycle_status,
    derive_readiness_status,
)


class TestStatusDerivation(unittest.TestCase):
    """Tests for lifecycle and readiness status derivation."""

    def test_derive_lifecycle_status_draft(self):
        self.assertEqual(derive_lifecycle_status("draft"), "draft")

    def test_derive_lifecycle_status_published(self):
        self.assertEqual(derive_lifecycle_status("published"), "active")

    def test_derive_lifecycle_status_deprecated(self):
        self.assertEqual(derive_lifecycle_status("deprecated"), "deprecated")

    def test_derive_lifecycle_status_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            derive_lifecycle_status("unknown")
        self.assertIn("Unknown storage status", str(ctx.exception))
        self.assertIn("'unknown'", str(ctx.exception))

    def test_derive_readiness_status_draft(self):
        self.assertEqual(derive_readiness_status("draft"), "not_ready")

    def test_derive_readiness_status_published(self):
        self.assertEqual(derive_readiness_status("published"), "ready")

    def test_derive_readiness_status_deprecated(self):
        self.assertEqual(derive_readiness_status("deprecated"), "not_ready")

    def test_derive_lifecycle_status_active_raises(self):
        with self.assertRaises(ValueError) as ctx:
            derive_lifecycle_status("active")
        self.assertIn("Unknown storage status", str(ctx.exception))

    def test_derive_readiness_status_active_raises(self):
        with self.assertRaises(ValueError) as ctx:
            derive_readiness_status("active")
        self.assertIn("Unknown storage status", str(ctx.exception))

    def test_derive_readiness_status_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            derive_readiness_status("archived")
        self.assertIn("Unknown storage status", str(ctx.exception))
        self.assertIn("'archived'", str(ctx.exception))

    def test_reserved_values_not_produced(self):
        """Phase A reserved values are never produced by derivation."""
        # validated is never produced
        for status in ("draft", "published", "deprecated"):
            lifecycle = derive_lifecycle_status(status)
            self.assertNotEqual(lifecycle, "validated")

        # stale is never produced
        for status in ("draft", "published", "deprecated"):
            readiness = derive_readiness_status(status)
            self.assertNotEqual(readiness, "stale")


if __name__ == "__main__":
    unittest.main()
