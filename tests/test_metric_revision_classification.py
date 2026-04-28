from __future__ import annotations

import copy
import unittest

from app.semantic_revision.metric_diff import classify_metric_revision


def _metric_contract() -> dict[str, object]:
    return {
        "header": {
            "metric_ref": "metric.daily_active_users",
            "display_name": "Daily Active Users",
            "description": "Distinct active users per day.",
            "metric_family": "count_metric",
            "population_subject_ref": "entity.user",
            "observed_entity_ref": "entity.user",
            "observation_grain_ref": "time.day",
            "sample_kind": "snapshot",
            "value_semantics": "count",
            "aggregation_scope": "per_observed_entity",
            "primary_time_ref": "time.activity_date",
            "additivity_constraints": {
                "time_policy": "sum",
                "dimension_policy": "subset",
            },
            "default_predicate_refs": ["predicate.active_user", "predicate.valid_region"],
            "metric_contract_version": "1.0.0",
        },
        "payload": {
            "metric_family": "count_metric",
            "count_target": {
                "name": "user_id",
                "semantics": "distinct active user",
                "aggregation": "count_distinct",
            },
        },
    }


class MetricRevisionClassificationTests(unittest.TestCase):
    def test_display_metadata_change_is_compatible_and_can_activate_now(self) -> None:
        base = _metric_contract()
        replacement = copy.deepcopy(base)
        replacement["header"]["display_name"] = "DAU"
        replacement["header"]["description"] = "Daily active users."

        result = classify_metric_revision(base, replacement)

        self.assertEqual(result.classified_compatibility, "compatible")
        self.assertEqual(result.required_actions, [])
        self.assertIs(result.can_activate_now, True)
        self.assertEqual(
            [entry.path for entry in result.diff_summary],
            ["header.description", "header.display_name"],
        )
        self.assertTrue(all(entry.compatibility == "compatible" for entry in result.diff_summary))

    def test_adding_required_inputs_is_breaking_and_blocks_activation(self) -> None:
        base = _metric_contract()
        replacement = copy.deepcopy(base)
        replacement["payload"]["required_inputs"] = [
            {"input_ref": "input.activity_window", "input_kind": "time_window"}
        ]

        result = classify_metric_revision(base, replacement)

        self.assertEqual(result.classified_compatibility, "breaking")
        self.assertIn("payload.required_inputs", [entry.path for entry in result.diff_summary])
        self.assertIs(result.can_activate_now, False)
        self.assertTrue(result.required_actions)
        self.assertTrue(any(action.blocking for action in result.required_actions))

    def test_payload_component_semantics_change_is_breaking(self) -> None:
        base = _metric_contract()
        replacement = copy.deepcopy(base)
        replacement["payload"]["count_target"]["aggregation"] = "count"

        result = classify_metric_revision(base, replacement)

        self.assertEqual(result.classified_compatibility, "breaking")
        self.assertIn(
            "payload.count_target.aggregation",
            [entry.path for entry in result.diff_summary],
        )
        self.assertIs(result.can_activate_now, False)

    def test_default_predicate_ref_order_is_normalized(self) -> None:
        base = _metric_contract()
        replacement = copy.deepcopy(base)
        replacement["header"]["default_predicate_refs"] = [
            "predicate.valid_region",
            "predicate.active_user",
        ]

        result = classify_metric_revision(base, replacement)

        self.assertEqual(result.classified_compatibility, "compatible")
        self.assertEqual(result.diff_summary, [])
        self.assertEqual(result.required_actions, [])
        self.assertIs(result.can_activate_now, True)


if __name__ == "__main__":
    unittest.main()
