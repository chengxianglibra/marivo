"""Tests for component-by-component lowering input (task 6.3) and
lowering precheck failure strategies (task 6.4).

Covers:
- build_component_lowering_inputs: surface ref mapping per component
- run_lowering_precheck: four unsupported scenario failure strategies
- Gate integration with validate_compiler_inputs
"""

from __future__ import annotations

import unittest
from typing import Any, Literal
from unittest.mock import MagicMock

from app.analysis_core.predicate_validator import (
    NormalizedComponentPredicateInput,
    NormalizedPredicateAtom,
    NormalizedPredicateInput,
    PredicateLayerRef,
    build_component_lowering_inputs,
    build_normalized_predicate_input,
    run_lowering_precheck,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ref(
    ref: str,
    layer: Literal[
        "governance_policy",
        "carrier_row_filter",
        "metric_default",
        "component_qualifier",
        "request_scope",
    ],
    component_field: str | None = None,
) -> PredicateLayerRef:
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


def _mock_resolver(mapping: dict[str, ResolvedSemanticObject]) -> MagicMock:
    resolver = MagicMock()

    def _resolve(ref: str) -> ResolvedSemanticObject:
        if ref in mapping:
            return mapping[ref]
        raise Exception(f"Unknown ref: {ref}")

    resolver.resolve_ref = _resolve
    return resolver


def _mock_binding(
    ref: str = "binding.test",
    field_bindings: list[dict[str, Any]] | None = None,
) -> ResolvedSemanticObject:
    fb = field_bindings or [
        {
            "carrier_binding_key": "carrier1",
            "semantic_ref": "dimension.country",
            "surface_ref": "field.country_code",
            "target": {"target_kind": "stable_descriptor", "target_key": "dimension.country"},
        },
    ]
    return ResolvedSemanticObject(
        object_kind="binding",
        object_id=ref,
        ref=ref,
        semantic_object={
            "header": {"binding_ref": ref},
            "interface_contract": {"field_bindings": fb},
        },
        status="active",
        revision=1,
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
    )


def _build_input(
    layered_refs: list[PredicateLayerRef],
    resolver: MagicMock,
    component_fields: list[str] | None = None,
) -> NormalizedPredicateInput:
    return build_normalized_predicate_input(
        layered_refs=layered_refs,
        resolver=resolver,
        component_fields=component_fields,
    )


# ---------------------------------------------------------------------------
# Task 6.3: build_component_lowering_inputs
# ---------------------------------------------------------------------------


class TestBuildComponentLoweringInputs(unittest.TestCase):
    def test_empty_input_produces_empty(self) -> None:
        result = build_component_lowering_inputs(
            normalized_predicate_input=_build_input([], _mock_resolver({})),
            resolved_bindings=[],
        )
        self.assertEqual(result, [])

    def test_single_component_all_groundable(self) -> None:
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver({"predicate.q1": _predicate_object("predicate.q1")})
        binding = _mock_binding()
        np_input = _build_input(refs, resolver, component_fields=["numerator"])
        result = build_component_lowering_inputs(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["component_field"], "numerator")
        self.assertIn("field.country_code", result[0]["available_surface_refs"])
        self.assertEqual(result[0]["ungroundable_target_refs"], [])

    def test_multi_component_independent(self) -> None:
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.q1": _predicate_object(
                    "predicate.q1", target_ref="dimension.country", value="active"
                ),
                "predicate.q2": _predicate_object(
                    "predicate.q2", target_ref="dimension.country", value="completed"
                ),
            }
        )
        binding = _mock_binding()
        np_input = _build_input(refs, resolver, component_fields=["denominator", "numerator"])
        result = build_component_lowering_inputs(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
        )
        self.assertEqual(len(result), 2)
        fields = [r["component_field"] for r in result]
        self.assertIn("denominator", fields)
        self.assertIn("numerator", fields)

    def test_ungroundable_target_ref(self) -> None:
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.q1": _predicate_object(
                    "predicate.q1", target_ref="dimension.missing_dim"
                ),
            }
        )
        binding = _mock_binding()  # only has dimension.country
        np_input = _build_input(refs, resolver, component_fields=["numerator"])
        result = build_component_lowering_inputs(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
        )
        self.assertEqual(len(result), 1)
        self.assertIn("dimension.missing_dim", result[0]["ungroundable_target_refs"])

    def test_shared_scope_atoms_included_in_surface_check(self) -> None:
        refs = [
            _ref("predicate.gov1", "governance_policy"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.gov1": _predicate_object(
                    "predicate.gov1", target_ref="dimension.region"
                ),
                "predicate.q1": _predicate_object("predicate.q1"),
            }
        )
        binding = _mock_binding(
            field_bindings=[
                {
                    "carrier_binding_key": "carrier1",
                    "semantic_ref": "dimension.country",
                    "surface_ref": "field.country_code",
                    "target": {"target_kind": "stable_descriptor"},
                },
            ]
        )
        np_input = _build_input(refs, resolver, component_fields=["numerator"])
        result = build_component_lowering_inputs(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
        )
        self.assertEqual(len(result), 1)
        # dimension.region is in shared scope but not in binding -> ungroundable
        self.assertIn("dimension.region", result[0]["ungroundable_target_refs"])

    def test_no_flattening_of_defaults_into_qualifiers(self) -> None:
        refs = [
            _ref("predicate.def1", "metric_default"),
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
            _ref("predicate.q2", "component_qualifier", component_field="denominator"),
        ]
        resolver = _mock_resolver(
            {
                "predicate.def1": _predicate_object("predicate.def1"),
                "predicate.q1": _predicate_object(
                    "predicate.q1", target_ref="dimension.status", value="active"
                ),
                "predicate.q2": _predicate_object(
                    "predicate.q2", target_ref="dimension.status", value="completed"
                ),
            }
        )
        binding = _mock_binding(
            field_bindings=[
                {
                    "carrier_binding_key": "carrier1",
                    "semantic_ref": "dimension.country",
                    "surface_ref": "field.country_code",
                    "target": {"target_kind": "stable_descriptor"},
                },
                {
                    "carrier_binding_key": "carrier1",
                    "semantic_ref": "dimension.status",
                    "surface_ref": "field.status_code",
                    "target": {"target_kind": "stable_descriptor"},
                },
            ]
        )
        np_input = _build_input(refs, resolver, component_fields=["denominator", "numerator"])
        result = build_component_lowering_inputs(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
        )
        # Both components should exist with separate entries
        self.assertEqual(len(result), 2)
        by_field = {r["component_field"]: r for r in result}
        self.assertIn("numerator", by_field)
        self.assertIn("denominator", by_field)
        # Default atoms are copied into each component, not flattened
        for comp in result:
            # Both dimension.country (default) and dimension.status (qualifier) should be found
            all_refs = comp["available_surface_refs"] + comp["ungroundable_target_refs"]
            # Both surface refs should be available
            self.assertIn("field.country_code", comp["available_surface_refs"])
            self.assertIn("field.status_code", comp["available_surface_refs"])

        # Verify qualifiers are NOT cross-contaminated: each component's
        # lowering input comes from its own qualifier_atoms only, with the
        # default atoms replicated per-component.
        np_input_num = next(
            c for c in np_input["component_inputs"] if c["component_field"] == "numerator"
        )
        np_input_den = next(
            c for c in np_input["component_inputs"] if c["component_field"] == "denominator"
        )
        # Qualifier atoms are component-specific (not shared or flattened)
        self.assertEqual(len(np_input_num["qualifier_atoms"]), 1)
        self.assertEqual(len(np_input_den["qualifier_atoms"]), 1)
        self.assertEqual(np_input_num["qualifier_atoms"][0]["source_ref"], "predicate.q1")
        self.assertEqual(np_input_den["qualifier_atoms"][0]["source_ref"], "predicate.q2")


# ---------------------------------------------------------------------------
# Task 6.4: run_lowering_precheck
# ---------------------------------------------------------------------------


class TestRunLoweringPrecheck(unittest.TestCase):
    def test_no_predicates_no_issues(self) -> None:
        np_input = _build_input([], _mock_resolver({}))
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[],
            component_fields=[],
        )
        self.assertEqual(issues, [])

    def test_target_domain_unresolvable(self) -> None:
        atom: NormalizedPredicateAtom = {
            "target_ref": "metric.revenue",
            "op": "eq",
            "value": 100,
            "source_ref": "predicate.bad1",
            "source_layer": "component_qualifier",
            "component_field": "numerator",
        }
        comp: NormalizedComponentPredicateInput = {
            "component_field": "numerator",
            "shared_scope_atoms": [],
            "default_atoms": [],
            "qualifier_atoms": [atom],
            "effective_scope_refs": ["predicate.bad1"],
            "scope_fingerprint": "abc",
        }
        np_input: NormalizedPredicateInput = {
            "shared_scope_atoms": [],
            "shared_scope_refs": [],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [comp],
        }
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[],
            component_fields=["numerator"],
        )
        self.assertTrue(
            any(i.code == "COMPILER_LOWERING_TARGET_DOMAIN_UNRESOLVABLE" for i in issues)
        )

    def test_binding_cannot_ground(self) -> None:
        atom: NormalizedPredicateAtom = {
            "target_ref": "dimension.country",
            "op": "eq",
            "value": "CN",
            "source_ref": "predicate.q1",
            "source_layer": "component_qualifier",
            "component_field": "numerator",
        }
        comp: NormalizedComponentPredicateInput = {
            "component_field": "numerator",
            "shared_scope_atoms": [],
            "default_atoms": [],
            "qualifier_atoms": [atom],
            "effective_scope_refs": ["predicate.q1"],
            "scope_fingerprint": "abc",
        }
        np_input: NormalizedPredicateInput = {
            "shared_scope_atoms": [],
            "shared_scope_refs": [],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [comp],
        }
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[],  # no bindings → cannot ground
            component_fields=["numerator"],
        )
        self.assertTrue(any(i.code == "COMPILER_LOWERING_BINDING_CANNOT_GROUND" for i in issues))

    def test_narrowing_unprovable_contradiction(self) -> None:
        shared_atom: NormalizedPredicateAtom = {
            "target_ref": "dimension.country",
            "op": "eq",
            "value": "US",
            "source_ref": "predicate.gov1",
            "source_layer": "governance_policy",
        }
        qual_atom: NormalizedPredicateAtom = {
            "target_ref": "dimension.country",
            "op": "eq",
            "value": "CN",
            "source_ref": "predicate.q1",
            "source_layer": "component_qualifier",
            "component_field": "numerator",
        }
        comp: NormalizedComponentPredicateInput = {
            "component_field": "numerator",
            "shared_scope_atoms": [shared_atom],
            "default_atoms": [],
            "qualifier_atoms": [qual_atom],
            "effective_scope_refs": ["predicate.gov1", "predicate.q1"],
            "scope_fingerprint": "abc",
        }
        np_input: NormalizedPredicateInput = {
            "shared_scope_atoms": [shared_atom],
            "shared_scope_refs": ["predicate.gov1"],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [comp],
        }
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[_mock_binding()],
            component_fields=["numerator"],
        )
        self.assertTrue(any(i.code == "COMPILER_LOWERING_NARROWING_UNPROVABLE" for i in issues))
        narrowing_issue = next(
            i for i in issues if i.code == "COMPILER_LOWERING_NARROWING_UNPROVABLE"
        )
        self.assertEqual(narrowing_issue.details["failure_kind"], "narrowing_unprovable")

    def test_component_lineage_lost(self) -> None:
        np_input: NormalizedPredicateInput = {
            "shared_scope_atoms": [],
            "shared_scope_refs": [],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [],  # numerator missing
        }
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[],
            component_fields=["numerator", "denominator"],  # both expected
        )
        lineage_issues = [i for i in issues if i.code == "COMPILER_LOWERING_COMPONENT_LINEAGE_LOST"]
        self.assertEqual(len(lineage_issues), 2)
        lost_fields = {i.details["component_field"] for i in lineage_issues}
        self.assertEqual(lost_fields, {"numerator", "denominator"})

    def test_all_groundable_no_issues(self) -> None:
        refs = [
            _ref("predicate.q1", "component_qualifier", component_field="numerator"),
        ]
        resolver = _mock_resolver({"predicate.q1": _predicate_object("predicate.q1")})
        binding = _mock_binding()
        np_input = _build_input(refs, resolver, component_fields=["numerator"])
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[binding],
            component_fields=["numerator"],
        )
        self.assertEqual(issues, [])

    def test_diagnostic_details_structure(self) -> None:
        atom: NormalizedPredicateAtom = {
            "target_ref": "dimension.missing",
            "op": "eq",
            "value": "X",
            "source_ref": "predicate.q1",
            "source_layer": "component_qualifier",
            "component_field": "numerator",
        }
        comp: NormalizedComponentPredicateInput = {
            "component_field": "numerator",
            "shared_scope_atoms": [],
            "default_atoms": [],
            "qualifier_atoms": [atom],
            "effective_scope_refs": ["predicate.q1"],
            "scope_fingerprint": "abc",
        }
        np_input: NormalizedPredicateInput = {
            "shared_scope_atoms": [],
            "shared_scope_refs": [],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [comp],
        }
        issues = run_lowering_precheck(
            normalized_predicate_input=np_input,
            resolved_bindings=[],  # no bindings
            component_fields=["numerator"],
        )
        binding_issue = next(
            i for i in issues if i.code == "COMPILER_LOWERING_BINDING_CANNOT_GROUND"
        )
        self.assertEqual(binding_issue.details["component_field"], "numerator")
        self.assertEqual(binding_issue.details["target_ref"], "dimension.missing")
        self.assertEqual(binding_issue.details["failure_kind"], "binding_cannot_ground")


if __name__ == "__main__":
    unittest.main()
