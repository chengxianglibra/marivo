"""Tests for normalized predicate input builder (task 6.2).

Covers:
- Empty refs produce empty input
- Shared scope atoms (governance, carrier, request_scope)
- Default atoms grouping
- Component qualifier atoms grouped by field
- Effective scope refs and fingerprint consistency with PredicateFilterLineage
- Source ref and source layer tagging
- No physical names in output (boundary enforcement)
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from app.analysis_core.predicate_validator import (
    PredicateLayerRef,
    build_normalized_predicate_input,
    build_predicate_filter_lineage,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ref(ref: str, layer: str, component_field: str | None = None) -> PredicateLayerRef:
    return PredicateLayerRef(ref=ref, layer=layer, component_field=component_field)


def _predicate_object(
    ref: str,
    target_ref: str = "dimension.country",
    op: str = "eq",
    value: Any = "CN",
) -> ResolvedSemanticObject:
    return ResolvedSemanticObject(
        object_kind="predicate",
        object_id=ref,
        ref=ref,
        semantic_object={
            "header": {"predicate_ref": ref, "subject_ref": "entity.user"},
            "interface_contract": {
                "expression": {"target_ref": target_ref, "op": op, "value": value},
                "allowed_usage": ["metric_qualifier", "carrier_row_filter", "request_scope"],
                "time_policy": "non_time_only",
            },
        },
        status="active",
        revision=1,
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
    )


def _conjunction_predicate(
    ref: str,
    atoms: list[dict[str, Any]],
) -> ResolvedSemanticObject:
    return ResolvedSemanticObject(
        object_kind="predicate",
        object_id=ref,
        ref=ref,
        semantic_object={
            "header": {"predicate_ref": ref, "subject_ref": "entity.user"},
            "interface_contract": {
                "expression": {"op": "and", "items": atoms},
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
        },
        status="active",
        revision=1,
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
    )


def _mock_resolver(mapping: dict[str, ResolvedSemanticObject]) -> MagicMock:
    resolver = MagicMock()

    def _resolve(ref: str) -> ResolvedSemanticObject:
        if ref in mapping:
            return mapping[ref]
        raise Exception(f"Unknown ref: {ref}")

    resolver.resolve_ref = _resolve
    return resolver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildNormalizedPredicateInput(unittest.TestCase):
    def test_empty_refs_produces_empty_input(self) -> None:
        resolver = _mock_resolver({})
        result = build_normalized_predicate_input(
            layered_refs=[],
            resolver=resolver,
            component_fields=None,
        )
        self.assertEqual(result["shared_scope_atoms"], [])
        self.assertEqual(result["shared_scope_refs"], [])
        self.assertEqual(result["default_atoms"], [])
        self.assertEqual(result["default_refs"], [])
        self.assertEqual(result["component_inputs"], [])

    def test_governance_atoms_in_shared_scope(self) -> None:
        refs = [_ref("predicate.gov1", "governance_policy")]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1", value="US"),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["shared_scope_atoms"]), 1)
        atom = result["shared_scope_atoms"][0]
        self.assertEqual(atom["target_ref"], "dimension.country")
        self.assertEqual(atom["op"], "eq")
        self.assertEqual(atom["value"], "US")
        self.assertEqual(atom["source_ref"], "predicate.gov1")
        self.assertEqual(atom["source_layer"], "governance_policy")
        self.assertIn("predicate.gov1", result["shared_scope_refs"])

    def test_carrier_atoms_in_shared_scope(self) -> None:
        refs = [_ref("predicate.car1", "carrier_row_filter")]
        resolver = _mock_resolver(
            {
                "predicate.car1": _predicate_object("predicate.car1", op="is_null", value=None),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["shared_scope_atoms"]), 1)
        self.assertEqual(result["shared_scope_atoms"][0]["source_layer"], "carrier_row_filter")

    def test_request_scope_atoms_in_shared_scope(self) -> None:
        refs = [_ref("predicate.scope1", "request_scope")]
        resolver = _mock_resolver(
            {
                "predicate.scope1": _predicate_object("predicate.scope1", value="CN"),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["shared_scope_atoms"]), 1)
        self.assertEqual(result["shared_scope_atoms"][0]["source_layer"], "request_scope")

    def test_default_atoms_grouped(self) -> None:
        refs = [_ref("predicate.def1", "metric_default")]
        resolver = _mock_resolver(
            {
                "predicate.def1": _predicate_object("predicate.def1", value="CN"),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["default_atoms"]), 1)
        self.assertEqual(result["default_atoms"][0]["source_layer"], "metric_default")
        self.assertIn("predicate.def1", result["default_refs"])

    def test_qualifier_atoms_grouped_by_component(self) -> None:
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.q1": _predicate_object("predicate.q1", value="active"),
                "predicate.q2": _predicate_object("predicate.q2", value="completed"),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["component_inputs"]), 2)
        # Sorted: denominator, numerator
        denom = result["component_inputs"][0]
        numer = result["component_inputs"][1]
        self.assertEqual(denom["component_field"], "denominator")
        self.assertEqual(len(denom["qualifier_atoms"]), 1)
        self.assertEqual(denom["qualifier_atoms"][0]["value"], "completed")
        self.assertEqual(numer["component_field"], "numerator")
        self.assertEqual(len(numer["qualifier_atoms"]), 1)
        self.assertEqual(numer["qualifier_atoms"][0]["value"], "active")

    def test_conjunction_predicate_extracts_all_atoms(self) -> None:
        refs = [_ref("predicate.conj1", "metric_default")]
        resolver = _mock_resolver(
            {
                "predicate.conj1": _conjunction_predicate(
                    "predicate.conj1",
                    [
                        {"target_ref": "dimension.country", "op": "eq", "value": "CN"},
                        {
                            "target_ref": "dimension.platform",
                            "op": "in",
                            "value": ["ios", "android"],
                        },
                    ],
                ),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(len(result["default_atoms"]), 2)
        targets = [atom["target_ref"] for atom in result["default_atoms"]]
        self.assertIn("dimension.country", targets)
        self.assertIn("dimension.platform", targets)

    def test_fingerprint_consistency_with_predicate_filter_lineage(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1"),
                "predicate.def1": _predicate_object("predicate.def1"),
                "predicate.q1": _predicate_object("predicate.q1", value="active"),
            }
        )
        lineage = build_predicate_filter_lineage(refs, component_fields=["numerator"])
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["numerator"],
        )
        # Fingerprints should match between lineage and normalized input
        lineage_fingerprint = lineage["component_effective_scopes"][0]["scope_fingerprint"]
        normalized_fingerprint = result["component_inputs"][0]["scope_fingerprint"]
        self.assertEqual(lineage_fingerprint, normalized_fingerprint)

    def test_effective_scope_refs_consistency(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.car1", "carrier_row_filter"),
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1"),
                "predicate.car1": _predicate_object("predicate.car1"),
                "predicate.def1": _predicate_object("predicate.def1"),
                "predicate.q1": _predicate_object("predicate.q1", value="active"),
            }
        )
        lineage = build_predicate_filter_lineage(refs, component_fields=["numerator"])
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["numerator"],
        )
        lineage_refs = lineage["component_effective_scopes"][0]["effective_scope_refs"]
        normalized_refs = result["component_inputs"][0]["effective_scope_refs"]
        self.assertEqual(set(lineage_refs), set(normalized_refs))

    def test_component_fields_creates_entries_without_qualifiers(self) -> None:
        refs = [_ref("predicate.q1", "component_qualifier", component_field="numerator")]
        resolver = _mock_resolver(
            {
                "predicate.q1": _predicate_object("predicate.q1"),
            }
        )
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["denominator", "numerator"],
        )
        self.assertEqual(len(result["component_inputs"]), 2)
        denom = result["component_inputs"][0]
        numer = result["component_inputs"][1]
        self.assertEqual(denom["component_field"], "denominator")
        self.assertEqual(denom["qualifier_atoms"], [])
        self.assertEqual(numer["component_field"], "numerator")
        self.assertEqual(len(numer["qualifier_atoms"]), 1)

    def test_no_physical_names_in_output(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1"),
                "predicate.q1": _predicate_object("predicate.q1"),
            }
        )
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["numerator"],
        )
        # The build function runs assert_predicate_uses_no_physical_names internally
        # so if we get here without exception, the boundary is enforced.
        # Do a sanity check that target_ref uses semantic refs, not physical names.
        for atom in result["shared_scope_atoms"]:
            self.assertTrue(
                atom["target_ref"].startswith(("dimension.", "entity.", "key.", "field.")),
                f"Expected semantic target_ref, got: {atom['target_ref']}",
            )

    def test_source_ref_and_layer_tagging(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.car1", "carrier_row_filter"),
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object(
                    "predicate.gov1", target_ref="dimension.region"
                ),
                "predicate.car1": _predicate_object("predicate.car1", target_ref="key.user_id"),
                "predicate.def1": _predicate_object(
                    "predicate.def1", target_ref="dimension.country"
                ),
                "predicate.q1": _predicate_object("predicate.q1", target_ref="dimension.status"),
            }
        )
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["numerator"],
        )
        # Each atom should carry its source_ref and source_layer
        for atom in result["shared_scope_atoms"]:
            self.assertIn("source_ref", atom)
            self.assertIn("source_layer", atom)
            self.assertIn(
                atom["source_layer"], {"governance_policy", "carrier_row_filter", "request_scope"}
            )
        for atom in result["default_atoms"]:
            self.assertEqual(atom["source_layer"], "metric_default")
        for comp in result["component_inputs"]:
            for atom in comp["qualifier_atoms"]:
                self.assertEqual(atom["source_layer"], "component_qualifier")
                self.assertEqual(atom.get("component_field"), comp["component_field"])

    def test_shared_scope_atoms_in_component_input(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1"),
                "predicate.q1": _predicate_object("predicate.q1"),
            }
        )
        result = build_normalized_predicate_input(
            layered_refs=refs,
            resolver=resolver,
            component_fields=["numerator"],
        )
        comp = result["component_inputs"][0]
        # shared_scope_atoms in component should contain governance atom
        self.assertEqual(len(comp["shared_scope_atoms"]), 1)
        self.assertEqual(comp["shared_scope_atoms"][0]["source_ref"], "predicate.gov1")

    def test_unresolvable_predicate_skipped(self) -> None:
        from app.semantic_runtime.errors import SemanticRuntimeNotFoundError

        refs = [_ref("predicate.missing", "governance_policy")]
        resolver = MagicMock()
        resolver.resolve_ref = MagicMock(
            side_effect=SemanticRuntimeNotFoundError("not found", semantic_ref="predicate.missing")
        )
        # Should not raise — unresolvable refs are skipped
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        self.assertEqual(result["shared_scope_atoms"], [])

    def test_ref_deduplication(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.gov1", "governance_policy"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object("predicate.gov1"),
            }
        )
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        # Refs should be deduplicated in ref lists
        self.assertEqual(result["shared_scope_refs"], ["predicate.gov1"])

    def test_physical_key_in_source_expression_is_stripped_by_extract_atoms(self) -> None:
        # _extract_atoms only copies target_ref/op/value from leaf atoms, so
        # any physical_name in the source expression is never propagated to the
        # NormalizedPredicateInput.  The boundary guard inside build_*
        # validates the output, not the input — this test confirms the output
        # is clean even when the source carries extra keys.
        bad_predicate = ResolvedSemanticObject(
            object_kind="predicate",
            object_id="predicate.bad1",
            ref="predicate.bad1",
            semantic_object={
                "header": {"predicate_ref": "predicate.bad1", "subject_ref": "entity.user"},
                "interface_contract": {
                    "expression": {
                        "target_ref": "dimension.country",
                        "op": "eq",
                        "value": "CN",
                        "physical_name": "country_code",
                    },
                    "allowed_usage": ["metric_qualifier"],
                    "time_policy": "non_time_only",
                },
            },
            status="active",
            revision=1,
            created_at="2026-04-01T00:00:00Z",
            updated_at="2026-04-01T00:00:00Z",
        )
        refs = [_ref("predicate.bad1", "governance_policy")]
        resolver = _mock_resolver({"predicate.bad1": bad_predicate})
        # Should not raise — the physical_name is stripped by _extract_atoms
        # and never reaches the NormalizedPredicateInput.
        result = build_normalized_predicate_input(layered_refs=refs, resolver=resolver)
        atom = result["shared_scope_atoms"][0]
        self.assertNotIn("physical_name", atom)
        self.assertEqual(atom["target_ref"], "dimension.country")


if __name__ == "__main__":
    unittest.main()
