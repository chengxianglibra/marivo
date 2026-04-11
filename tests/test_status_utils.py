"""Tests for shared status derivation utilities."""

import unittest

from app.semantic_runtime.status_utils import (
    default_readiness_contract,
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

    def test_derive_readiness_status_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            derive_readiness_status("archived")
        self.assertIn("Unknown storage status", str(ctx.exception))
        self.assertIn("'archived'", str(ctx.exception))

    def test_default_readiness_contract_draft(self):
        contract = default_readiness_contract("draft")
        self.assertEqual(contract["lifecycle_status"], "draft")
        self.assertEqual(contract["readiness_status"], "not_ready")
        self.assertEqual(contract["blocking_requirements"], [])
        self.assertEqual(contract["capabilities"], {})

    def test_default_readiness_contract_published(self):
        contract = default_readiness_contract("published")
        self.assertEqual(contract["lifecycle_status"], "active")
        self.assertEqual(contract["readiness_status"], "ready")
        self.assertEqual(contract["blocking_requirements"], [])
        self.assertEqual(contract["capabilities"], {})

    def test_default_readiness_contract_deprecated(self):
        contract = default_readiness_contract("deprecated")
        self.assertEqual(contract["lifecycle_status"], "deprecated")
        self.assertEqual(contract["readiness_status"], "not_ready")
        self.assertEqual(contract["blocking_requirements"], [])
        self.assertEqual(contract["capabilities"], {})

    def test_default_readiness_contract_unknown_raises(self):
        with self.assertRaises(ValueError):
            default_readiness_contract("invalid_status")

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
