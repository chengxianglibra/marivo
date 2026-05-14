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
            primary_time_ref="date",
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_observe)
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertTrue(caps.supports_test)
        self.assertTrue(caps.supports_detect)
        self.assertFalse(caps.supports_validate)
        self.assertTrue(caps.time_rollup_allowed)
        self.assertEqual(caps.additive_dimensions, ["country", "date"])
        self.assertIsNone(caps.blocker)
        self.assertIsNone(caps.remediation_hint)
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_additive_without_primary_time_ref(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref=None,
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.supports_compare)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.supports_detect)
        self.assertFalse(caps.time_rollup_allowed)

    # -- non-additive metric (empty additive_dimensions) ----------------------

    def test_non_additive_metric(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
            primary_time_ref="date",
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.additive_dimensions, [])
        self.assertEqual(caps.blocker, "ADDITIVITY_NONE")
        self.assertIn("additive_dimensions", caps.remediation_hint or "")
        self.assertIsNone(caps.capability_condition)

    def test_non_additive_without_primary_time_ref(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
            primary_time_ref=None,
            sample_kind="numeric",
        )
        self.assertFalse(caps.supports_compare)
        self.assertFalse(caps.supports_attribute)

    # -- subset: primary_time_ref membership controls time_rollup_allowed -----

    def test_time_rollup_allowed_when_primary_time_in_additive_dimensions(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country", "activity_date"],
            primary_time_ref="activity_date",
            sample_kind="numeric",
        )
        self.assertTrue(caps.time_rollup_allowed)

    def test_time_rollup_not_allowed_when_primary_time_absent(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="activity_date",
            sample_kind="numeric",
        )
        self.assertFalse(caps.time_rollup_allowed)

    def test_time_rollup_not_allowed_without_primary_time_ref(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref=None,
            sample_kind="numeric",
        )
        self.assertFalse(caps.time_rollup_allowed)

    # -- supports_attribute = supports_compare AND supports_decompose ---------

    def test_attribute_requires_both_compare_and_decompose(self) -> None:
        # additive_dimensions non-empty + primary_time_ref -> both true -> attribute true
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="date",
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)

        # additive_dimensions non-empty but no primary_time_ref -> compare false -> attribute false
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref=None,
            sample_kind="numeric",
        )
        self.assertFalse(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)

        # empty additive_dimensions with primary_time_ref -> compare true but decompose false
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
            primary_time_ref="date",
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)

    # -- supports_test (sample_kind) ------------------------------------------

    def test_supports_test_numeric(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="numeric",
        )
        self.assertTrue(caps.supports_test)

    def test_supports_test_rate(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="rate",
        )
        self.assertTrue(caps.supports_test)

    def test_supports_test_binary(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="binary",
        )
        self.assertTrue(caps.supports_test)

    def test_no_supports_test_survival(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="survival",
        )
        self.assertFalse(caps.supports_test)

    # -- supports_detect (primary_time_ref + process_anchor_time_ref) ---------

    def test_supports_detect_from_primary_time(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="date",
        )
        self.assertTrue(caps.supports_detect)

    def test_supports_detect_from_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref=None,
            process_anchor_time_ref="experiment_start",
        )
        self.assertTrue(caps.supports_detect)

    def test_no_supports_detect_without_any_time_ref(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref=None,
        )
        self.assertFalse(caps.supports_detect)

    # -- supports_validate (rate only) ----------------------------------------

    def test_supports_validate_rate_with_process(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="rate",
            process_anchor_time_ref="experiment_start",
        )
        self.assertTrue(caps.supports_validate)

    def test_supports_validate_rate_without_process(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="rate",
        )
        self.assertTrue(caps.supports_validate)

    def test_no_supports_validate_numeric_with_process(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            sample_kind="numeric",
            process_anchor_time_ref="experiment_start",
        )
        self.assertFalse(caps.supports_validate)

    # -- additive_dimensions always a list (never None) -----------------------

    def test_additive_dimensions_always_list_when_non_empty(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country", "region"],
            primary_time_ref="date",
        )
        self.assertEqual(caps.additive_dimensions, ["country", "region"])

    def test_additive_dimensions_always_list_when_empty(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
            primary_time_ref="date",
        )
        self.assertIsInstance(caps.additive_dimensions, list)
        self.assertEqual(caps.additive_dimensions, [])

    # -- capability_condition --------------------------------------------------

    def test_capability_condition_subset(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="date",
        )
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_capability_condition_none_for_non_additive(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=[],
            primary_time_ref="date",
        )
        self.assertIsNone(caps.capability_condition)

    # -- to_dict ---------------------------------------------------------------

    def test_to_dict_roundtrip(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="date",
            sample_kind="numeric",
        )
        d = caps.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["supports_observe"], caps.supports_observe)
        self.assertEqual(d["supports_compare"], caps.supports_compare)
        self.assertEqual(d["supports_decompose"], caps.supports_decompose)
        self.assertEqual(d["supports_attribute"], caps.supports_attribute)
        self.assertEqual(d["supports_test"], caps.supports_test)
        self.assertEqual(d["supports_detect"], caps.supports_detect)
        self.assertEqual(d["supports_validate"], caps.supports_validate)
        self.assertEqual(d["time_rollup_allowed"], caps.time_rollup_allowed)
        self.assertEqual(d["additive_dimensions"], caps.additive_dimensions)
        self.assertEqual(d["blocker"], caps.blocker)
        self.assertEqual(d["remediation_hint"], caps.remediation_hint)
        self.assertEqual(d["capability_condition"], caps.capability_condition)

    def test_to_dict_contains_no_removed_fields(self) -> None:
        caps = derive_additivity_capabilities(
            additive_dimensions=["country"],
            primary_time_ref="date",
        )
        d = caps.to_dict()
        self.assertNotIn("dimension_policy", d)
        self.assertNotIn("time_axis_policy", d)
        self.assertNotIn("additivity_basis", d)

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

    def test_defaults_no_primary_time_no_sample_kind(self) -> None:
        caps = derive_additivity_capabilities(additive_dimensions=["country"])
        # supports_compare = bool(primary_time_ref) = False
        self.assertFalse(caps.supports_compare)
        # supports_test = sample_kind in {"numeric", "rate", "binary"} -> False (None)
        self.assertFalse(caps.supports_test)
        # supports_detect = bool(primary_time_ref or process_anchor_time_ref) -> False
        self.assertFalse(caps.supports_detect)
        # supports_validate = sample_kind == "rate" -> False
        self.assertFalse(caps.supports_validate)


if __name__ == "__main__":
    unittest.main()
