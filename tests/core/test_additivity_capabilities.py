"""Tests for marivo.core.semantic.additivity."""

from __future__ import annotations

import unittest

from marivo.core.semantic.additivity import (
    AdditivityCapabilityResult,
    derive_additivity_capabilities,
)


class DeriveAdditivityCapabilitiesTests(unittest.TestCase):
    """Test the shared additivity capability derivation helper."""

    # -- additive metric (non-empty additive_dimensions) -----------------------

    def test_additive_metric_full_capabilities(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country", "date"],
            process_anchor_time_ref="event_time",
        )
        self.assertTrue(caps.supports_observe)
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertTrue(caps.supports_detect)
        self.assertEqual(caps.additive_dimensions, ["country", "date"])
        self.assertIsNone(caps.blocker)
        self.assertIsNone(caps.remediation_hint)
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_additive_without_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_attribute)
        self.assertFalse(caps.supports_detect)

    def test_all_additive_dimensions_sentinel(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["__all"],
        )
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertEqual(caps.additive_dimensions, ["__all"])
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    # -- non-additive metric (empty additive_dimensions) ----------------------

    def test_non_additive_metric(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertEqual(caps.additive_dimensions, [])
        self.assertEqual(caps.blocker, "ADDITIVITY_NONE")
        self.assertIn("additive_dimensions", caps.remediation_hint or "")
        self.assertIsNone(caps.capability_condition)

    # -- supports_attribute = supports_compare AND supports_decompose ---------

    def test_attribute_requires_decompose(self) -> None:
        # additive_dimensions non-empty -> decompose true, compare always true -> attribute true
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)

        # empty additive_dimensions -> decompose false -> attribute false
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)

    # -- supports_detect (process_anchor_time_ref only) -----------------------

    def test_supports_detect_from_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            process_anchor_time_ref="experiment_start",
        )
        self.assertTrue(caps.supports_detect)

    def test_no_supports_detect_without_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        self.assertFalse(caps.supports_detect)

    # -- additive_dimensions always a list (never None) -----------------------

    def test_additive_dimensions_always_list_when_non_empty(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country", "region"],
        )
        self.assertEqual(caps.additive_dimensions, ["country", "region"])

    def test_additive_dimensions_always_list_when_empty(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
        )
        self.assertIsInstance(caps.additive_dimensions, list)
        self.assertEqual(caps.additive_dimensions, [])

    # -- capability_condition --------------------------------------------------

    def test_capability_condition_subset(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_capability_condition_none_for_non_additive(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
        )
        self.assertIsNone(caps.capability_condition)

    # -- to_dict ---------------------------------------------------------------

    def test_to_dict_roundtrip(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        d = caps.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["supports_observe"], caps.supports_observe)
        self.assertEqual(d["supports_compare"], caps.supports_compare)
        self.assertEqual(d["supports_decompose"], caps.supports_decompose)
        self.assertEqual(d["supports_attribute"], caps.supports_attribute)
        self.assertEqual(d["supports_detect"], caps.supports_detect)
        self.assertEqual(d["additive_dimensions"], caps.additive_dimensions)
        self.assertEqual(d["blocker"], caps.blocker)
        self.assertEqual(d["remediation_hint"], caps.remediation_hint)
        self.assertEqual(d["capability_condition"], caps.capability_condition)

    def test_to_dict_contains_no_removed_fields(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
        )
        d = caps.to_dict()
        self.assertNotIn("dimension_policy", d)
        self.assertNotIn("time_axis_policy", d)
        self.assertNotIn("additivity_basis", d)
        self.assertNotIn("time_rollup_allowed", d)

    # -- supports_observe is always True --------------------------------------

    def test_supports_observe_always_true(self) -> None:
        for dims in [[], ["country"]]:
            with self.subTest(additive_dimensions=dims):
                caps = derive_additivity_capabilities(additive_dimensions=dims)
                self.assertTrue(caps.supports_observe)

    # -- result type -----------------------------------------------------------

    def test_result_is_frozen_dataclass(self) -> None:
        caps = derive_additivity_capabilities(additive_dimensions=["country"])
        self.assertIsInstance(caps, AdditivityCapabilityResult)
        with self.assertRaises(AttributeError):
            caps.supports_observe = False  # type: ignore[misc]

    # -- default parameter values ----------------------------------------------

    def test_defaults_no_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(additive_dimensions=["country"])
        # supports_compare always True (time_scope.field guaranteed at request level)
        self.assertTrue(caps.supports_compare)
        # supports_detect = bool(process_anchor_time_ref) -> False
        self.assertFalse(caps.supports_detect)


if __name__ == "__main__":
    unittest.main()
