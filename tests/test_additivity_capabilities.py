"""Tests for app.analysis_core.additivity_capabilities."""

from __future__ import annotations

import unittest

from marivo.core.semantic.additivity import (
    derive_additivity_capabilities,
)


class DeriveAdditivityCapabilitiesTests(unittest.TestCase):
    """Test the shared additivity capability derivation helper."""

    def _header(self, **overrides: object) -> dict:
        base = {
            "additivity_constraints": {
                "dimension_policy": "all",
                "time_axis_policy": "additive",
            },
            "primary_time_ref": "time.activity_date",
            "sample_kind": "numeric",
        }
        base.update(overrides)
        return base

    # -- additive metric (dimension_policy="all") -----------------------------

    def test_additive_metric_full_capabilities(self) -> None:
        caps = derive_additivity_capabilities(header=self._header())
        self.assertTrue(caps.supports_observe)
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertTrue(caps.supports_test)
        self.assertTrue(caps.supports_detect)
        self.assertFalse(caps.supports_validate)
        self.assertTrue(caps.time_rollup_allowed)
        self.assertEqual(caps.dimension_policy, "all")
        self.assertEqual(caps.time_axis_policy, "additive")
        self.assertIsNone(caps.blocker)
        self.assertIsNone(caps.remediation_hint)

    def test_additive_without_primary_time_ref(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(primary_time_ref=None))
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.supports_compare)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.supports_detect)

    # -- subset policy (semi-additive with declared dimensions) ---------------

    def test_subset_policy_with_additive_dimensions(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            )
        )
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.dimension_policy, "subset")
        self.assertEqual(caps.time_axis_policy, "non_additive")
        self.assertEqual(caps.additive_dimensions, ["dimension.country"])
        self.assertIsNone(caps.blocker)

    def test_subset_policy_with_additive_time_axis(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "additive",
                    "additive_dimensions": ["dimension.country", "dimension.region"],
                },
            )
        )
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.time_rollup_allowed)

    def test_subset_policy_without_additive_dimensions_fail_closed(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": [],
                },
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.blocker, "ADDITIVITY_SUBSET_NO_DIMENSIONS")

    # -- non_additive metric (dimension_policy="none") -----------------------

    def test_non_additive_metric(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.dimension_policy, "none")
        self.assertEqual(caps.time_axis_policy, "non_additive")
        self.assertIsNone(caps.blocker)

    def test_non_additive_without_primary_time_ref(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
                primary_time_ref=None,
            )
        )
        self.assertFalse(caps.supports_compare)
        self.assertFalse(caps.supports_attribute)

    # -- missing / empty additivity_constraints -------------------------------

    def test_missing_additivity_constraints(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(additivity_constraints=None))
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.supports_compare)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_MISSING")
        self.assertIn("additivity_constraints", caps.remediation_hint or "")

    def test_invalid_additivity_constraints_type(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(additivity_constraints="additive")
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_INVALID")

    def test_invalid_dimension_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "unknown",
                    "time_axis_policy": "additive",
                },
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_INVALID")
        self.assertIn("dimension_policy", caps.remediation_hint or "")

    # -- supports_attribute = supports_compare AND supports_decompose ---------

    def test_attribute_requires_both_compare_and_decompose(self) -> None:
        # additive + primary_time_ref -> both true -> attribute true
        caps = derive_additivity_capabilities(header=self._header())
        self.assertTrue(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)

        # additive but no primary_time_ref -> compare false -> attribute false
        caps = derive_additivity_capabilities(header=self._header(primary_time_ref=None))
        self.assertFalse(caps.supports_compare)
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)

        # non_additive with primary_time_ref -> compare true but decompose false -> attribute false
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertTrue(caps.supports_compare)
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)

    # -- supports_test (sample_kind) ------------------------------------------

    def test_supports_test_numeric(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(sample_kind="numeric"))
        self.assertTrue(caps.supports_test)

    def test_supports_test_rate(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(sample_kind="rate"))
        self.assertTrue(caps.supports_test)

    def test_supports_test_binary(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(sample_kind="binary"))
        self.assertTrue(caps.supports_test)

    def test_no_supports_test_survival(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(sample_kind="survival"))
        self.assertFalse(caps.supports_test)

    # -- supports_detect (primary_time_ref + process_anchor_time_ref) ---------

    def test_supports_detect_from_metric_time(self) -> None:
        caps = derive_additivity_capabilities(header=self._header())
        self.assertTrue(caps.supports_detect)

    def test_supports_detect_from_process_anchor(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(primary_time_ref=None),
            process_anchor_time_ref="time.experiment_start",
        )
        self.assertTrue(caps.supports_detect)

    def test_no_supports_detect_without_any_time_ref(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(primary_time_ref=None))
        self.assertFalse(caps.supports_detect)

    # -- supports_validate (rate only; process not required) -------------------

    def test_supports_validate_rate_with_process(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(sample_kind="rate"),
            process_anchor_time_ref="time.experiment_start",
        )
        self.assertTrue(caps.supports_validate)

    def test_supports_validate_rate_without_process(self) -> None:
        # Rate metrics support validate even without a process anchor.
        # The validate intent runs on rate metrics without requiring a process object.
        caps = derive_additivity_capabilities(header=self._header(sample_kind="rate"))
        self.assertTrue(caps.supports_validate)

    def test_no_supports_validate_numeric_with_process(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(sample_kind="numeric"),
            process_anchor_time_ref="time.experiment_start",
        )
        self.assertFalse(caps.supports_validate)

    # -- additivity_basis echoes inputs ---------------------------------------

    def test_additivity_basis_echoes_inputs(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(),
            process_anchor_time_ref="time.exp",
        )
        self.assertEqual(caps.additivity_basis["dimension_policy"], "all")
        self.assertEqual(caps.additivity_basis["time_axis_policy"], "additive")
        self.assertIsNone(caps.additivity_basis["additive_dimensions"])
        self.assertEqual(caps.additivity_basis["primary_time_ref"], "time.activity_date")
        self.assertEqual(caps.additivity_basis["sample_kind"], "numeric")
        self.assertEqual(caps.additivity_basis["process_anchor_time_ref"], "time.exp")

    def test_additivity_basis_includes_additive_dimensions(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            ),
        )
        self.assertEqual(caps.additivity_basis["dimension_policy"], "subset")
        self.assertEqual(caps.additivity_basis["additive_dimensions"], ["dimension.country"])

    # -- to_dict ---------------------------------------------------------------

    def test_to_dict_roundtrip(self) -> None:
        caps = derive_additivity_capabilities(header=self._header())
        d = caps.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["supports_observe"], caps.supports_observe)
        self.assertEqual(d["supports_compare"], caps.supports_compare)
        self.assertEqual(d["supports_decompose"], caps.supports_decompose)
        self.assertEqual(d["supports_attribute"], caps.supports_attribute)
        self.assertEqual(d["dimension_policy"], caps.dimension_policy)
        self.assertEqual(d["time_axis_policy"], caps.time_axis_policy)
        self.assertEqual(d["additive_dimensions"], caps.additive_dimensions)
        self.assertEqual(d["time_rollup_allowed"], caps.time_rollup_allowed)
        self.assertEqual(d["blocker"], caps.blocker)
        self.assertEqual(d["remediation_hint"], caps.remediation_hint)
        self.assertEqual(d["capability_condition"], caps.capability_condition)

    # -- fine-grained blockers ------------------------------------------------

    def test_missing_dimension_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(additivity_constraints={"time_axis_policy": "additive"})
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING")
        self.assertIn("dimension_policy", caps.remediation_hint or "")

    def test_empty_dimension_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={"dimension_policy": "", "time_axis_policy": "additive"}
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING")

    def test_missing_time_axis_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(additivity_constraints={"dimension_policy": "all"})
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING")
        self.assertIn("time_axis_policy", caps.remediation_hint or "")

    def test_empty_time_axis_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={"dimension_policy": "all", "time_axis_policy": ""}
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING")

    def test_both_missing_reports_dimension_policy_first(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(additivity_constraints={}))
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING")

    def test_valid_dimension_policy_invalid_time_axis_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={"dimension_policy": "all", "time_axis_policy": "bogus"}
            )
        )
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_INVALID")

    # -- capability_condition --------------------------------------------------

    def test_capability_condition_subset(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            )
        )
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_capability_condition_all(self) -> None:
        caps = derive_additivity_capabilities(header=self._header())
        self.assertIsNone(caps.capability_condition)

    def test_capability_condition_none(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertIsNone(caps.capability_condition)

    def test_capability_condition_blocked(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(additivity_constraints=None))
        self.assertIsNone(caps.capability_condition)


if __name__ == "__main__":
    unittest.main()
