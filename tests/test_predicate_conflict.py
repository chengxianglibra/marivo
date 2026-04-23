"""Tests for predicate conflict detection (task 4.5).

Covers:
- Governance vs metric_default / component_qualifier conflicts
- Carrier row_filter vs component_qualifier conflicts
- Within-metric: metric_default vs component_qualifier conflicts
- Cross-component: component_qualifier vs component_qualifier conflicts
- Gate integration: empty refs, single layer, unresolvable refs
"""

from __future__ import annotations

import unittest
from typing import Any

from app.analysis_core.predicate_validator import (
    PredicateLayerRef,
    ResolvedAtom,
    _check_carrier_vs_qualifier_conflict,
    _check_cross_component_conflict,
    _check_governance_vs_metric_conflict,
    _check_within_metric_conflict,
    validate_predicate_conflicts,
)
from app.semantic_runtime.errors import SemanticRuntimeNotFoundError
from app.semantic_runtime.resolution import ResolvedSemanticObject, RuntimeSemanticAvailability

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved_predicate(
    predicate_ref: str,
    *,
    expression: dict[str, Any] | None = None,
) -> ResolvedSemanticObject:
    return ResolvedSemanticObject(
        object_kind="predicate",
        object_id=f"pred_{predicate_ref}",
        ref=predicate_ref,
        semantic_object={
            "header": {
                "predicate_ref": predicate_ref,
                "subject_ref": "entity.test_entity",
                "predicate_contract_version": "predicate.v1",
            },
            "interface_contract": {
                "expression": expression
                or {
                    "op": "and",
                    "items": [{"op": "eq", "target_ref": "dimension.country", "value": "US"}],
                },
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
            "status": "published",
        },
        status="published",
        revision=1,
        created_at="2026-04-23T00:00:00Z",
        updated_at="2026-04-23T00:00:00Z",
    )


class _StubResolver:
    def __init__(
        self,
        *,
        resolved: dict[str, ResolvedSemanticObject] | None = None,
    ) -> None:
        self._resolved = resolved or {}

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        if semantic_ref in self._resolved:
            return self._resolved[semantic_ref]
        raise SemanticRuntimeNotFoundError(f"Not found: {semantic_ref}", semantic_ref=semantic_ref)

    def inspect_ref(self, semantic_ref: str) -> RuntimeSemanticAvailability:
        resolved = self._resolved.get(semantic_ref)
        if resolved is not None:
            return RuntimeSemanticAvailability(
                resolved=resolved,
                lifecycle_status="active",
                readiness_status="ready",
            )
        raise SemanticRuntimeNotFoundError(f"Not found: {semantic_ref}", semantic_ref=semantic_ref)


def _atom(
    *,
    target_ref: str = "dimension.country",
    op: str = "eq",
    value: Any = "US",
    source_ref: str = "predicate.test",
    source_layer: str = "metric_default",
    component_field: str | None = None,
) -> ResolvedAtom:
    return ResolvedAtom(
        target_ref=target_ref,
        op=op,
        value=value,
        source_ref=source_ref,
        source_layer=source_layer,
        component_field=component_field,
    )


# ---------------------------------------------------------------------------
# Governance vs metric conflict
# ---------------------------------------------------------------------------


class TestGovernanceVsMetricConflict(unittest.TestCase):
    def test_same_value_passes(self):
        gov = _atom(
            source_layer="governance_policy", source_ref="predicate.gov1", op="eq", value="US"
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="US"
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(issues, [])

    def test_eq_eq_contradiction(self):
        gov = _atom(
            source_layer="governance_policy", source_ref="predicate.gov1", op="eq", value="US"
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="CN"
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_GOVERNANCE_METRIC_CONFLICT")
        self.assertEqual(issues[0].category, "readiness")

    def test_in_eq_narrowing_passes(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="in",
            value=["US", "CN"],
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="US"
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(issues, [])

    def test_gte_gte_narrowing_passes(self):
        gov = _atom(
            source_layer="governance_policy", source_ref="predicate.gov1", op="gte", value=18
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="gte", value=25
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(issues, [])

    def test_in_in_partial_overlap_fails_closed(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="in",
            value=["US", "CN"],
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="in", value=["CN", "JP"]
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_GOVERNANCE_METRIC_CONFLICT")
        self.assertIn("unprovable", issues[0].message)
        self.assertEqual(issues[0].severity, "warning")

    def test_component_qualifier_also_checked(self):
        gov = _atom(
            source_layer="governance_policy", source_ref="predicate.gov1", op="eq", value="US"
        )
        comp = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="CN",
            component_field="count_target",
        )
        issues = _check_governance_vs_metric_conflict([gov, comp])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_GOVERNANCE_METRIC_CONFLICT")

    def test_no_governance_no_issues(self):
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="US"
        )
        issues = _check_governance_vs_metric_conflict([metric])
        self.assertEqual(issues, [])

    def test_no_metric_no_issues(self):
        gov = _atom(
            source_layer="governance_policy", source_ref="predicate.gov1", op="eq", value="US"
        )
        issues = _check_governance_vs_metric_conflict([gov])
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Carrier vs qualifier conflict
# ---------------------------------------------------------------------------


class TestCarrierVsQualifierConflict(unittest.TestCase):
    def test_same_value_passes(self):
        carrier = _atom(
            source_layer="carrier_row_filter", source_ref="predicate.car1", op="eq", value="active"
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="active",
            component_field="count_target",
        )
        issues = _check_carrier_vs_qualifier_conflict([carrier, qualifier])
        self.assertEqual(issues, [])

    def test_eq_eq_contradiction(self):
        carrier = _atom(
            source_layer="carrier_row_filter", source_ref="predicate.car1", op="eq", value="active"
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="deleted",
            component_field="count_target",
        )
        issues = _check_carrier_vs_qualifier_conflict([carrier, qualifier])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_CARRIER_QUALIFIER_CONFLICT")
        self.assertEqual(issues[0].category, "compiler")

    def test_in_eq_narrowing_passes(self):
        carrier = _atom(
            source_layer="carrier_row_filter",
            source_ref="predicate.car1",
            op="in",
            value=["US", "CN"],
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        issues = _check_carrier_vs_qualifier_conflict([carrier, qualifier])
        self.assertEqual(issues, [])

    def test_eq_in_contradiction(self):
        carrier = _atom(
            source_layer="carrier_row_filter", source_ref="predicate.car1", op="eq", value="JP"
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="in",
            value=["US", "CN"],
            component_field="count_target",
        )
        issues = _check_carrier_vs_qualifier_conflict([carrier, qualifier])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_CARRIER_QUALIFIER_CONFLICT")


# ---------------------------------------------------------------------------
# Within-metric conflict (default vs qualifier)
# ---------------------------------------------------------------------------


class TestWithinMetricConflict(unittest.TestCase):
    def test_same_value_passes(self):
        default = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="US"
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        issues = _check_within_metric_conflict([default, qualifier])
        self.assertEqual(issues, [])

    def test_eq_eq_contradiction(self):
        default = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="US"
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="CN",
            component_field="count_target",
        )
        issues = _check_within_metric_conflict([default, qualifier])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_WITHIN_METRIC_CONFLICT")
        self.assertEqual(issues[0].category, "compiler")

    def test_in_eq_narrowing_passes(self):
        default = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="in", value=["US", "CN"]
        )
        qualifier = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        issues = _check_within_metric_conflict([default, qualifier])
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Cross-component conflict
# ---------------------------------------------------------------------------


class TestCrossComponentConflict(unittest.TestCase):
    def test_same_value_passes(self):
        a = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        b = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual2",
            op="eq",
            value="US",
            component_field="denominator",
        )
        issues = _check_cross_component_conflict([a, b])
        self.assertEqual(issues, [])

    def test_contradiction(self):
        a = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        b = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual2",
            op="eq",
            value="CN",
            component_field="denominator",
        )
        issues = _check_cross_component_conflict([a, b])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_CROSS_COMPONENT_CONFLICT")
        self.assertEqual(issues[0].category, "readiness")

    def test_same_component_field_no_conflict(self):
        # Same component_field: not a cross-component check
        a = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="numerator",
        )
        b = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual2",
            op="eq",
            value="CN",
            component_field="numerator",
        )
        issues = _check_cross_component_conflict([a, b])
        self.assertEqual(issues, [])

    def test_single_component_no_conflict(self):
        a = _atom(
            source_layer="component_qualifier",
            source_ref="predicate.qual1",
            op="eq",
            value="US",
            component_field="count_target",
        )
        issues = _check_cross_component_conflict([a])
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Gate integration
# ---------------------------------------------------------------------------


class TestValidatePredicateConflicts(unittest.TestCase):
    def test_no_refs_no_issues(self):
        resolver = _StubResolver()
        issues = validate_predicate_conflicts(layered_refs=[], resolver=resolver)
        self.assertEqual(issues, [])

    def test_single_layer_no_conflict(self):
        resolver = _StubResolver(
            resolved={
                "predicate.gov1": _resolved_predicate(
                    "predicate.gov1",
                    expression={"op": "eq", "target_ref": "dimension.country", "value": "US"},
                ),
            }
        )
        refs = [PredicateLayerRef(ref="predicate.gov1", layer="governance_policy")]
        issues = validate_predicate_conflicts(layered_refs=refs, resolver=resolver)
        self.assertEqual(issues, [])

    def test_unresolvable_ref_skipped(self):
        resolver = _StubResolver()
        refs = [PredicateLayerRef(ref="predicate.missing", layer="metric_default")]
        issues = validate_predicate_conflicts(layered_refs=refs, resolver=resolver)
        self.assertEqual(issues, [])

    def test_governance_metric_conflict_detected(self):
        resolver = _StubResolver(
            resolved={
                "predicate.gov1": _resolved_predicate(
                    "predicate.gov1",
                    expression={"op": "eq", "target_ref": "dimension.country", "value": "US"},
                ),
                "predicate.def1": _resolved_predicate(
                    "predicate.def1",
                    expression={"op": "eq", "target_ref": "dimension.country", "value": "CN"},
                ),
            }
        )
        refs = [
            PredicateLayerRef(ref="predicate.gov1", layer="governance_policy"),
            PredicateLayerRef(ref="predicate.def1", layer="metric_default"),
        ]
        issues = validate_predicate_conflicts(layered_refs=refs, resolver=resolver)
        self.assertTrue(
            any(i.code == "COMPILER_PREDICATE_GOVERNANCE_METRIC_CONFLICT" for i in issues)
        )

    def test_no_conflict_across_different_targets(self):
        resolver = _StubResolver(
            resolved={
                "predicate.gov1": _resolved_predicate(
                    "predicate.gov1",
                    expression={"op": "eq", "target_ref": "dimension.country", "value": "US"},
                ),
                "predicate.def1": _resolved_predicate(
                    "predicate.def1",
                    expression={"op": "eq", "target_ref": "dimension.region", "value": "CN"},
                ),
            }
        )
        refs = [
            PredicateLayerRef(ref="predicate.gov1", layer="governance_policy"),
            PredicateLayerRef(ref="predicate.def1", layer="metric_default"),
        ]
        issues = validate_predicate_conflicts(layered_refs=refs, resolver=resolver)
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Mixed-operator conflict tests
# ---------------------------------------------------------------------------


class TestMixedOperatorConflict(unittest.TestCase):
    def test_eq_gte_compatible_passes(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="gte",
            value=18,
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value=25
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(issues, [])

    def test_eq_gte_contradiction_errors(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="gte",
            value=18,
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value=10
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")

    def test_eq_between_compatible(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="between",
            value=[18, 65],
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value=25
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(issues, [])

    def test_eq_between_contradiction_errors(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="between",
            value=[18, 65],
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value=70
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")

    def test_mixed_operator_unprovable_is_warning(self):
        # gte vs lte: not directional narrowing, returns None → severity=warning
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="gte",
            value=18,
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="lte", value=65
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertIn("unprovable", issues[0].message)

    def test_contradiction_is_error_not_warning(self):
        gov = _atom(
            source_layer="governance_policy",
            source_ref="predicate.gov1",
            op="eq",
            value="US",
        )
        metric = _atom(
            source_layer="metric_default", source_ref="predicate.def1", op="eq", value="CN"
        )
        issues = _check_governance_vs_metric_conflict([gov, metric])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertIn("contradiction", issues[0].message)


# ---------------------------------------------------------------------------
# Scope filtering in conflict gate
# ---------------------------------------------------------------------------


class TestScopeFilteredConflictGate(unittest.TestCase):
    def test_out_of_scope_policy_no_conflict(self):
        """An out-of-scope row_filter policy should not cause conflict issues."""
        from app.analysis_core.predicate_validator import collect_layered_predicate_refs

        class _ScopedGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.gov1"},
                        "scope": {"step_types": ["aggregate_query"]},
                        "enabled": True,
                    },
                ]

        class _StubInputs:
            class _Request:
                intent_kind = "metric_query"
                table_name = None
                request_scope_predicate_ref = None

            normalized_request = _Request()
            resolved_bindings = []
            resolved_metric = None

        refs = collect_layered_predicate_refs(_StubInputs(), _ScopedGovRepo())
        # The policy is scoped to aggregate_query, but our step is metric_query
        self.assertEqual(refs, [])


if __name__ == "__main__":
    unittest.main()
