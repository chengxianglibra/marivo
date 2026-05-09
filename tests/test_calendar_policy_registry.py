from __future__ import annotations

import unittest

from marivo.analysis_core.calendar_policy import (
    CalendarPolicyResolutionError,
    get_calendar_policy,
    policy_registry_summary,
    resolve_calendar_policy,
    validate_calendar_policy_ref,
)


class CalendarPolicyRegistryTests(unittest.TestCase):
    def test_registry_summary_includes_v1_catalog(self) -> None:
        summary = policy_registry_summary()

        self.assertEqual(len(summary), 7)
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
                "calendar_policy.calendar_mom",
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
                "event_cluster",
                "year_relative_event_key",
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )
        self.assertEqual(policy.fallback_strategy, ("same_weekday_nearest", "natural_date_shift"))
        self.assertEqual(policy.matching_strategy[4].tie_breaker, "prefer_backward")
        self.assertEqual(policy.matching_strategy[4].max_shift_days, 3)

    def test_get_calendar_policy_returns_calendar_mom_strategy(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_mom")

        self.assertEqual(policy.comparison_basis, "mom")
        self.assertEqual(policy.resolved_baseline_generation_rule.strategy, "previous_period")
        self.assertEqual(
            [step.matcher for step in policy.matching_strategy],
            [
                "event_cluster",
                "year_relative_event_key",
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )
        self.assertEqual(policy.fallback_strategy, ("same_weekday_nearest", "natural_date_shift"))
        self.assertEqual(policy.matching_strategy[4].tie_breaker, "prefer_backward")
        self.assertEqual(policy.matching_strategy[4].max_shift_days, 3)

    def test_natural_policy_does_not_expose_weekday_matcher_options(self) -> None:
        policy = get_calendar_policy("calendar_policy.natural_yoy")

        self.assertIsNone(policy.matching_strategy[0].tie_breaker)
        self.assertIsNone(policy.matching_strategy[0].max_shift_days)

    def test_validate_calendar_policy_ref_rejects_unknown_ref(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            validate_calendar_policy_ref("calendar_policy.unknown")

        self.assertEqual(ctx.exception.code, "calendar_policy_unknown")

    def test_validate_calendar_policy_ref_rejects_basis_mismatch(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            validate_calendar_policy_ref(
                "calendar_policy.weekday_wow",
                comparison_basis="yoy",
            )

        self.assertEqual(ctx.exception.code, "calendar_policy_basis_mismatch")

    def test_resolve_calendar_policy_prefers_explicit_then_injected(self) -> None:
        resolved = resolve_calendar_policy(
            explicit_policy_ref="calendar_policy.weekday_yoy",
            injected_policy_ref="calendar_policy.calendar_yoy",
            planner_candidate_refs=["calendar_policy.natural_yoy"],
            comparison_basis="yoy",
        )

        assert resolved is not None
        self.assertEqual(resolved.policy.policy_ref, "calendar_policy.weekday_yoy")
        self.assertEqual(resolved.resolution_source, "explicit_request")

    def test_resolve_calendar_policy_accepts_injected_when_explicit_missing(self) -> None:
        resolved = resolve_calendar_policy(
            injected_policy_ref="calendar_policy.calendar_mom",
            planner_candidate_refs=["calendar_policy.natural_mom"],
            comparison_basis="mom",
        )

        assert resolved is not None
        self.assertEqual(resolved.policy.policy_ref, "calendar_policy.calendar_mom")
        self.assertEqual(resolved.resolution_source, "injected_binding")

    def test_resolve_calendar_policy_rejects_ambiguous_planner_candidates(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            resolve_calendar_policy(
                planner_candidate_refs=[
                    "calendar_policy.calendar_yoy",
                    "calendar_policy.weekday_yoy",
                ],
                comparison_basis="yoy",
            )

        self.assertEqual(ctx.exception.code, "calendar_policy_ambiguous")

    def test_resolve_calendar_policy_accepts_unique_planner_candidate_after_dedup(self) -> None:
        resolved = resolve_calendar_policy(
            planner_candidate_refs=[
                "calendar_policy.calendar_yoy",
                "calendar_policy.calendar_yoy",
            ],
            comparison_basis="yoy",
        )

        assert resolved is not None
        self.assertEqual(resolved.policy.policy_ref, "calendar_policy.calendar_yoy")
        self.assertEqual(resolved.resolution_source, "planner_candidate")

    def test_resolve_calendar_policy_rejects_planner_candidate_basis_mismatch(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            resolve_calendar_policy(
                planner_candidate_refs=["calendar_policy.calendar_mom"],
                comparison_basis="yoy",
            )

        self.assertEqual(ctx.exception.code, "calendar_policy_basis_mismatch")

    def test_resolve_calendar_policy_can_require_policy(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            resolve_calendar_policy(comparison_basis="wow", required=True)

        self.assertEqual(ctx.exception.code, "calendar_policy_missing")

    def test_calendar_yoy_has_calendar_aware_alignment(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        self.assertEqual(policy.resolved_alignment_mode, "calendar_aware")
        self.assertEqual(
            policy.window_tags,
            ("calendar_aware", "event_cluster", "holiday_cluster", "same_weekday_fallback"),
        )
        self.assertEqual(
            policy.coverage_behavior, "warn_when_calendar_annotation_missing_or_fallback_used"
        )
        self.assertEqual(policy.avoid_when, ())

    def test_calendar_mom_has_calendar_aware_alignment(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_mom")

        self.assertEqual(policy.resolved_alignment_mode, "calendar_aware")
        self.assertEqual(
            policy.window_tags,
            ("calendar_aware", "event_cluster", "holiday_cluster"),
        )
        self.assertEqual(
            policy.coverage_behavior, "warn_when_calendar_annotation_missing_or_fallback_used"
        )
        self.assertEqual(policy.avoid_when, ("普通自然月环比",))

    def test_old_policy_refs_are_removed(self) -> None:
        for ref in (
            "calendar_policy.holiday_yoy",
            "calendar_policy.event_yoy",
            "calendar_policy.event_mom",
        ):
            with self.assertRaises(CalendarPolicyResolutionError):
                get_calendar_policy(ref)
