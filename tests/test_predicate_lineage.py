"""Tests for predicate filter lineage builder (task 4.6).

Covers:
- Empty refs produce empty lineage
- Governance and carrier in shared scope
- Request scope in shared scope
- Metric defaults in default lineage
- Component qualifiers grouped by field
- Component effective scope composition
- Fingerprint determinism
- Different components produce different fingerprints
"""

from __future__ import annotations

import unittest

from app.analysis_core.predicate_validator import (
    PredicateLayerRef,
    build_predicate_filter_lineage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ref(ref: str, layer: str, component_field: str | None = None) -> PredicateLayerRef:
    return PredicateLayerRef(ref=ref, layer=layer, component_field=component_field)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildPredicateFilterLineage(unittest.TestCase):
    def test_empty_refs_produces_empty_lineage(self):
        lineage = build_predicate_filter_lineage([])
        self.assertEqual(lineage["shared_effective_scope"]["governance_policy_refs"], [])
        self.assertEqual(lineage["shared_effective_scope"]["carrier_row_filter_refs"], [])
        self.assertNotIn("request_scope_ref", lineage["shared_effective_scope"])
        self.assertEqual(lineage["metric_default_lineage"]["default_predicate_refs"], [])
        self.assertEqual(lineage["component_qualifier_lineages"], [])
        self.assertEqual(lineage["component_effective_scopes"], [])

    def test_governance_in_shared_scope(self):
        refs = [_ref("predicate.gov1", "governance_policy")]
        lineage = build_predicate_filter_lineage(refs)
        self.assertEqual(
            lineage["shared_effective_scope"]["governance_policy_refs"], ["predicate.gov1"]
        )

    def test_carrier_in_shared_scope(self):
        refs = [_ref("predicate.car1", "carrier_row_filter")]
        lineage = build_predicate_filter_lineage(refs)
        self.assertEqual(
            lineage["shared_effective_scope"]["carrier_row_filter_refs"], ["predicate.car1"]
        )

    def test_request_scope_in_shared_scope(self):
        refs = [_ref("predicate.scope1", "request_scope")]
        lineage = build_predicate_filter_lineage(refs)
        self.assertEqual(lineage["shared_effective_scope"]["request_scope_ref"], "predicate.scope1")

    def test_metric_defaults_in_default_lineage(self):
        refs = [_ref("predicate.def1", "metric_default"), _ref("predicate.def2", "metric_default")]
        lineage = build_predicate_filter_lineage(refs)
        self.assertEqual(
            lineage["metric_default_lineage"]["default_predicate_refs"],
            ["predicate.def1", "predicate.def2"],
        )

    def test_component_qualifiers_grouped_by_field(self):
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
            _ref("predicate.q3", "component_qualifier", component_field="numerator"),
        ]
        lineage = build_predicate_filter_lineage(refs)
        lineages = lineage["component_qualifier_lineages"]
        self.assertEqual(len(lineages), 2)
        # Sorted by component_field: denominator, numerator
        self.assertEqual(lineages[0]["component_field"], "denominator")
        self.assertEqual(lineages[0]["qualifier_refs"], ["predicate.q2"])
        self.assertEqual(lineages[1]["component_field"], "numerator")
        self.assertEqual(lineages[1]["qualifier_refs"], ["predicate.q1", "predicate.q3"])

    def test_component_effective_scope_includes_shared_plus_default_plus_qualifier(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.car1", "carrier_row_filter"),
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
        ]
        lineage = build_predicate_filter_lineage(refs)
        scopes = lineage["component_effective_scopes"]
        self.assertEqual(len(scopes), 2)
        # denominator component: gov1 + car1 + def1 + q2
        self.assertEqual(scopes[0]["component_field"], "denominator")
        self.assertIn("predicate.gov1", scopes[0]["effective_scope_refs"])
        self.assertIn("predicate.car1", scopes[0]["effective_scope_refs"])
        self.assertIn("predicate.def1", scopes[0]["effective_scope_refs"])
        self.assertIn("predicate.q2", scopes[0]["effective_scope_refs"])
        self.assertNotIn("predicate.q1", scopes[0]["effective_scope_refs"])
        # numerator component: gov1 + car1 + def1 + q1
        self.assertEqual(scopes[1]["component_field"], "numerator")
        self.assertIn("predicate.q1", scopes[1]["effective_scope_refs"])
        self.assertNotIn("predicate.q2", scopes[1]["effective_scope_refs"])

    def test_scope_fingerprint_is_deterministic(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        lineage1 = build_predicate_filter_lineage(refs)
        lineage2 = build_predicate_filter_lineage(refs)
        self.assertEqual(
            lineage1["component_effective_scopes"][0]["scope_fingerprint"],
            lineage2["component_effective_scopes"][0]["scope_fingerprint"],
        )

    def test_different_components_produce_different_fingerprints(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
        ]
        lineage = build_predicate_filter_lineage(refs)
        scopes = lineage["component_effective_scopes"]
        self.assertEqual(len(scopes), 2)
        self.assertNotEqual(scopes[0]["scope_fingerprint"], scopes[1]["scope_fingerprint"])

    def test_request_scope_included_in_component_effective_scope(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.scope1", "request_scope"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        lineage = build_predicate_filter_lineage(refs)
        scope = lineage["component_effective_scopes"][0]
        self.assertIn("predicate.scope1", scope["effective_scope_refs"])

    # --- component_fields parameter (tasks 5.3 / 5.4) ---

    def test_component_fields_produces_entries_without_qualifiers(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        lineage = build_predicate_filter_lineage(
            refs, component_fields=["denominator", "numerator"]
        )
        lineages = lineage["component_qualifier_lineages"]
        self.assertEqual(len(lineages), 2)
        self.assertEqual(lineages[0]["component_field"], "denominator")
        self.assertEqual(lineages[0]["qualifier_refs"], [])
        self.assertEqual(lineages[1]["component_field"], "numerator")
        self.assertEqual(lineages[1]["qualifier_refs"], ["predicate.q1"])

    def test_component_fields_none_preserves_backward_compatibility(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        lineage = build_predicate_filter_lineage(refs, component_fields=None)
        lineages = lineage["component_qualifier_lineages"]
        self.assertEqual(len(lineages), 1)
        self.assertEqual(lineages[0]["component_field"], "numerator")

    def test_effective_scope_without_qualifiers_is_shared_plus_defaults(self):
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        lineage = build_predicate_filter_lineage(
            refs, component_fields=["denominator", "numerator"]
        )
        scopes = lineage["component_effective_scopes"]
        denom_scope = scopes[0]
        self.assertEqual(denom_scope["component_field"], "denominator")
        self.assertIn("predicate.gov1", denom_scope["effective_scope_refs"])
        self.assertIn("predicate.def1", denom_scope["effective_scope_refs"])
        self.assertNotIn("predicate.q1", denom_scope["effective_scope_refs"])
        num_scope = scopes[1]
        self.assertIn("predicate.q1", num_scope["effective_scope_refs"])

    def test_empty_refs_with_component_fields_produces_component_entries(self):
        lineage = build_predicate_filter_lineage([], component_fields=["count_target"])
        self.assertEqual(lineage["shared_effective_scope"]["governance_policy_refs"], [])
        self.assertEqual(lineage["metric_default_lineage"]["default_predicate_refs"], [])
        self.assertEqual(len(lineage["component_qualifier_lineages"]), 1)
        self.assertEqual(lineage["component_qualifier_lineages"][0]["qualifier_refs"], [])
        self.assertEqual(len(lineage["component_effective_scopes"]), 1)
        self.assertEqual(lineage["component_effective_scopes"][0]["effective_scope_refs"], [])
        self.assertTrue(len(lineage["component_effective_scopes"][0]["scope_fingerprint"]) > 0)


if __name__ == "__main__":
    unittest.main()
