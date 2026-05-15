from __future__ import annotations

import unittest

from marivo.core.semantic.calendar import (
    CalendarPolicyResolutionError,
    compare_type_to_alignment_plan,
    get_calendar_policy,
    policy_registry_summary,
)


class CalendarPolicyRegistryTests(unittest.TestCase):
    def test_registry_summary_includes_v1_catalog(self) -> None:
        summary = policy_registry_summary()

        self.assertEqual(len(summary), 6)
        self.assertEqual(summary[0]["policy_ref"], "calendar_policy.natural_yoy")
        self.assertIn("comparison_basis", summary[0])
        self.assertIn("window_tags", summary[0])
        self.assertIn("use_when", summary[0])
        self.assertIn("avoid_when", summary[0])

    def test_registry_summary_filters_by_comparison_basis(self) -> None:
        yoy_summary = policy_registry_summary(comparison_basis="yoy")
        mom_summary = policy_registry_summary(comparison_basis="mom")
        wow_summary = policy_registry_summary(comparison_basis="wow")

        self.assertEqual(
            [item["policy_ref"] for item in yoy_summary],
            [
                "calendar_policy.natural_yoy",
                "calendar_policy.weekday_yoy",
                "calendar_policy.calendar_yoy",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in mom_summary],
            [
                "calendar_policy.natural_mom",
                "calendar_policy.weekday_mom",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in wow_summary],
            ["calendar_policy.weekday_wow"],
        )

    def test_get_calendar_policy_returns_calendar_yoy_strategy(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        self.assertEqual(policy.comparison_basis, "yoy")
        self.assertEqual(policy.resolved_baseline_generation_rule.strategy, "previous_year")
        self.assertEqual(
            [step.matcher for step in policy.matching_strategy],
            [
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )
        self.assertEqual(policy.fallback_strategy, ("same_weekday_nearest", "natural_date_shift"))
        self.assertEqual(policy.matching_strategy[2].tie_breaker, "prefer_backward")
        self.assertEqual(policy.matching_strategy[2].max_shift_days, 3)

    def test_natural_policy_does_not_expose_weekday_matcher_options(self) -> None:
        policy = get_calendar_policy("calendar_policy.natural_yoy")

        self.assertIsNone(policy.matching_strategy[0].tie_breaker)
        self.assertIsNone(policy.matching_strategy[0].max_shift_days)

    def test_compare_type_to_alignment_plan_rejects_unknown_ref(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            compare_type_to_alignment_plan("not_real")

        self.assertEqual(ctx.exception.code, "compare_type_unknown")

    def test_compare_type_to_alignment_plan_maps_supported_types(self) -> None:
        self.assertIsNone(compare_type_to_alignment_plan("normal"))
        self.assertEqual(
            compare_type_to_alignment_plan("holiday_aligned_yoy").resolved_alignment_mode,
            "calendar_aware",
        )
        self.assertTrue(
            compare_type_to_alignment_plan("holiday_aligned_yoy").requires_calendar_data
        )
        self.assertEqual(
            compare_type_to_alignment_plan("weekday_aligned_mom").comparison_basis,
            "mom",
        )

    def test_calendar_yoy_has_calendar_aware_alignment(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        self.assertEqual(policy.resolved_alignment_mode, "calendar_aware")
        self.assertEqual(
            policy.window_tags,
            ("calendar_aware", "holiday_cluster", "same_weekday_fallback"),
        )
        self.assertEqual(
            policy.coverage_behavior, "warn_when_calendar_annotation_missing_or_fallback_used"
        )
        self.assertEqual(policy.avoid_when, ())

    def test_old_policy_refs_are_removed(self) -> None:
        for ref in (
            "calendar_policy.holiday_yoy",
            "calendar_policy.event_yoy",
            "calendar_policy.event_mom",
            "calendar_policy.calendar_mom",
        ):
            with self.assertRaises(CalendarPolicyResolutionError):
                get_calendar_policy(ref)
