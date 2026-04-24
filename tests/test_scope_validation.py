"""Tests for request scope validation (tasks 4.3 and 4.4).

Covers:
- Scope expression shape validation: non-time, conjunctive, no dynamic values
- Scope target exclusion: governance/carrier targets off-limits to request scope
- Scope narrowing proof: decidable subset, fail-closed on unprovable pairs
- Gate integration: scope_validation gate in compiler pipeline
"""

from __future__ import annotations

import unittest
from typing import Any

from app.analysis_core.predicate_validator import (
    PredicateRefWithUsage,
    _check_scope_expression_shape,
    _check_scope_narrowing,
    _check_scope_target_exclusions,
    _extract_atoms,
    _values_overlap,
    validate_request_scope,
)
from app.semantic_runtime.errors import SemanticRuntimeNotFoundError
from app.semantic_runtime.resolution import (
    ResolvedSemanticObject,
    RuntimeSemanticAvailability,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved_predicate(
    predicate_ref: str,
    *,
    interface_contract: dict[str, Any] | None = None,
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
            "interface_contract": interface_contract
            or {
                "expression": {
                    "op": "and",
                    "items": [
                        {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                    ],
                },
                "allowed_usage": ["request_scope"],
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


# ---------------------------------------------------------------------------
# Unit tests — _extract_atoms
# ---------------------------------------------------------------------------


class TestExtractAtoms(unittest.TestCase):
    def test_single_atom(self):
        expr = {"op": "eq", "target_ref": "dimension.country", "value": "US"}
        atoms = _extract_atoms(expr)
        self.assertEqual(len(atoms), 1)
        self.assertEqual(atoms[0]["target_ref"], "dimension.country")

    def test_conjunction(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                {"op": "gte", "target_ref": "dimension.age", "value": 18},
            ],
        }
        atoms = _extract_atoms(expr)
        self.assertEqual(len(atoms), 2)

    def test_nested_conjunction(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                {
                    "op": "and",
                    "items": [
                        {"op": "gte", "target_ref": "dimension.age", "value": 18},
                    ],
                },
            ],
        }
        atoms = _extract_atoms(expr)
        self.assertEqual(len(atoms), 2)


# ---------------------------------------------------------------------------
# Unit tests — scope expression shape (4.3)
# ---------------------------------------------------------------------------


class TestScopeExpressionShape(unittest.TestCase):
    def test_valid_conjunction_passes(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            ],
        }
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_valid_single_atom_passes(self):
        expr = {"op": "eq", "target_ref": "dimension.country", "value": "US"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_or_op_emits_scope_disjunctive(self):
        expr = {"op": "or", "items": []}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_DISJUNCTIVE")
        self.assertEqual(issues[0].gate, "scope_validation")

    def test_not_op_emits_scope_negation(self):
        expr = {"op": "not", "items": []}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NEGATION")

    def test_time_target_emits_scope_time_condition(self):
        expr = {"op": "eq", "target_ref": "time.created_at", "value": "2024-01-01"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_TIME_CONDITION")
        self.assertEqual(issues[0].details["target_ref"], "time.created_at")

    def test_dynamic_now_emits_scope_dynamic_value(self):
        expr = {"op": "gt", "target_ref": "dimension.ts", "value": "now()"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_DYNAMIC_VALUE")

    def test_dynamic_variable_emits_scope_dynamic_value(self):
        expr = {"op": "eq", "target_ref": "dimension.env", "value": "${ENV}"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_DYNAMIC_VALUE")

    def test_nested_or_in_conjunction_emits_disjunctive(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "or", "items": []},
            ],
        }
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_DISJUNCTIVE")

    def test_multiple_violations_all_reported(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "or", "items": []},
                {"op": "eq", "target_ref": "time.created_at", "value": "2024"},
            ],
        }
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        codes = {i.code for i in issues}
        self.assertIn("COMPILER_SCOPE_DISJUNCTIVE", codes)
        self.assertIn("COMPILER_SCOPE_TIME_CONDITION", codes)

    def test_neq_op_emits_scope_forbidden_operator(self):
        expr = {"op": "neq", "target_ref": "dimension.status", "value": "deleted"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_FORBIDDEN_OPERATOR")
        self.assertEqual(issues[0].details["op"], "neq")

    def test_not_in_op_emits_scope_forbidden_operator(self):
        expr = {"op": "not_in", "target_ref": "dimension.platform", "value": ["web"]}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_FORBIDDEN_OPERATOR")
        self.assertEqual(issues[0].details["op"], "not_in")

    def test_neq_in_conjunction_emits_forbidden_operator(self):
        expr = {
            "op": "and",
            "items": [
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                {"op": "neq", "target_ref": "dimension.status", "value": "deleted"},
            ],
        }
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_FORBIDDEN_OPERATOR", codes)

    def test_eq_on_new_target_still_passes(self):
        expr = {"op": "eq", "target_ref": "dimension.region", "value": "east"}
        issues = _check_scope_expression_shape(expr, "predicate.scope1")
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Unit tests — _values_overlap (4.4 decidable subset)
# ---------------------------------------------------------------------------


class TestValuesOverlap(unittest.TestCase):
    # --- same-operator: eq vs eq ---
    def test_eq_eq_same_value(self):
        self.assertTrue(_values_overlap("eq", "US", "eq", "US"))

    def test_eq_eq_different_value(self):
        self.assertFalse(_values_overlap("eq", "CN", "eq", "US"))

    # --- only allowed cross-operator: eq scope vs in upstream ---
    def test_eq_in_value_present(self):
        self.assertTrue(_values_overlap("eq", "US", "in", ["US", "CN"]))

    def test_eq_in_value_absent(self):
        self.assertFalse(_values_overlap("eq", "JP", "in", ["US", "CN"]))

    # --- same-operator: in vs in (subset semantics) ---
    def test_in_in_subset(self):
        self.assertTrue(_values_overlap("in", ["US"], "in", ["US", "CN"]))

    def test_in_in_same_set(self):
        self.assertTrue(_values_overlap("in", ["US", "CN"], "in", ["US", "CN"]))

    def test_in_in_disjoint(self):
        self.assertFalse(_values_overlap("in", ["US"], "in", ["CN", "JP"]))

    def test_in_in_overlap_not_subset(self):
        self.assertIsNone(_values_overlap("in", ["US", "CN"], "in", ["CN", "JP"]))

    # --- same-operator: gte vs gte ---
    def test_gte_gte_request_higher(self):
        self.assertTrue(_values_overlap("gte", 25, "gte", 18))

    def test_gte_gte_request_lower(self):
        self.assertFalse(_values_overlap("gte", 10, "gte", 18))

    def test_gte_gte_equal(self):
        self.assertTrue(_values_overlap("gte", 18, "gte", 18))

    # --- same-operator: gt vs gt ---
    def test_gt_gt_request_higher(self):
        self.assertTrue(_values_overlap("gt", 25, "gt", 18))

    def test_gt_gt_request_equal(self):
        self.assertTrue(_values_overlap("gt", 18, "gt", 18))

    def test_gt_gt_request_lower(self):
        self.assertFalse(_values_overlap("gt", 10, "gt", 18))

    # --- same-operator: lte vs lte ---
    def test_lte_lte_request_lower(self):
        self.assertTrue(_values_overlap("lte", 50, "lte", 65))

    def test_lte_lte_request_higher(self):
        self.assertFalse(_values_overlap("lte", 70, "lte", 65))

    def test_lte_lte_equal(self):
        self.assertTrue(_values_overlap("lte", 65, "lte", 65))

    # --- same-operator: lt vs lt ---
    def test_lt_lt_request_lower(self):
        self.assertTrue(_values_overlap("lt", 50, "lt", 65))

    def test_lt_lt_request_equal(self):
        self.assertTrue(_values_overlap("lt", 65, "lt", 65))

    def test_lt_lt_request_higher(self):
        self.assertFalse(_values_overlap("lt", 70, "lt", 65))

    # --- same-operator: between vs between (range subset) ---
    def test_between_between_subset(self):
        self.assertTrue(_values_overlap("between", [20, 30], "between", [18, 65]))

    def test_between_between_same(self):
        self.assertTrue(_values_overlap("between", [18, 65], "between", [18, 65]))

    def test_between_between_disjoint(self):
        self.assertFalse(_values_overlap("between", [70, 90], "between", [18, 65]))

    def test_between_between_partial_overlap_not_subset(self):
        self.assertIsNone(_values_overlap("between", [20, 70], "between", [18, 65]))

    def test_between_between_adjacent_subset(self):
        self.assertTrue(_values_overlap("between", [18, 25], "between", [18, 65]))

    # --- same-operator: is_null / is_not_null ---
    def test_is_null_is_null(self):
        self.assertTrue(_values_overlap("is_null", None, "is_null", None))

    def test_is_null_is_not_null(self):
        self.assertFalse(_values_overlap("is_null", None, "is_not_null", None))

    def test_is_not_null_is_null(self):
        self.assertFalse(_values_overlap("is_not_null", None, "is_null", None))

    def test_is_not_null_is_not_null(self):
        self.assertTrue(_values_overlap("is_not_null", None, "is_not_null", None))

    # --- cross-operator pairs: eq vs comparison ops ---
    def test_eq_vs_gte_narrowing(self):
        self.assertTrue(_values_overlap("eq", 25, "gte", 18))

    def test_eq_vs_gte_contradiction(self):
        self.assertFalse(_values_overlap("eq", 10, "gte", 18))

    def test_eq_vs_gt_narrowing(self):
        self.assertTrue(_values_overlap("eq", 25, "gt", 18))

    def test_eq_vs_gt_contradiction(self):
        self.assertFalse(_values_overlap("eq", 18, "gt", 18))

    def test_eq_vs_lte_narrowing(self):
        self.assertTrue(_values_overlap("eq", 50, "lte", 65))

    def test_eq_vs_lte_contradiction(self):
        self.assertFalse(_values_overlap("eq", 70, "lte", 65))

    def test_eq_vs_lt_narrowing(self):
        self.assertTrue(_values_overlap("eq", 50, "lt", 65))

    def test_eq_vs_lt_contradiction(self):
        self.assertFalse(_values_overlap("eq", 65, "lt", 65))

    def test_gte_vs_eq_narrowing(self):
        self.assertIsNone(_values_overlap("gte", 18, "eq", 25))

    def test_gte_vs_eq_contradiction(self):
        self.assertFalse(_values_overlap("gte", 18, "eq", 10))

    def test_lte_vs_eq_narrowing(self):
        self.assertIsNone(_values_overlap("lte", 65, "eq", 50))

    def test_lte_vs_eq_contradiction(self):
        self.assertFalse(_values_overlap("lte", 65, "eq", 70))

    # --- cross-operator: eq vs between ---
    def test_eq_vs_between_in_range(self):
        self.assertTrue(_values_overlap("eq", 25, "between", [18, 65]))

    def test_eq_vs_between_out_of_range(self):
        self.assertFalse(_values_overlap("eq", 70, "between", [18, 65]))

    def test_between_vs_eq_in_range(self):
        self.assertIsNone(_values_overlap("between", [18, 65], "eq", 25))

    def test_between_vs_eq_out_of_range(self):
        self.assertFalse(_values_overlap("between", [18, 65], "eq", 70))

    # --- cross-operator: in vs comparison ops ---
    def test_in_vs_gte_all_pass(self):
        self.assertTrue(_values_overlap("in", [25, 30], "gte", 18))

    def test_in_vs_gte_none_pass(self):
        self.assertFalse(_values_overlap("in", [5, 10], "gte", 18))

    def test_in_vs_gte_partial(self):
        self.assertIsNone(_values_overlap("in", [10, 25], "gte", 18))

    def test_gte_vs_in_all_pass(self):
        self.assertTrue(_values_overlap("gte", 18, "in", [25, 30]))

    def test_gte_vs_in_none_pass(self):
        self.assertFalse(_values_overlap("gte", 18, "in", [5, 10]))

    def test_gte_vs_in_partial(self):
        self.assertIsNone(_values_overlap("gte", 18, "in", [10, 25]))

    # --- cross-operator: in vs between ---
    def test_in_vs_between_all_in_range(self):
        self.assertTrue(_values_overlap("in", [20, 25], "between", [18, 30]))

    def test_in_vs_between_none_in_range(self):
        self.assertFalse(_values_overlap("in", [5, 10], "between", [18, 30]))

    def test_in_vs_between_partial(self):
        self.assertIsNone(_values_overlap("in", [20, 30], "between", [18, 25]))

    def test_between_vs_in_all_in_range(self):
        self.assertTrue(_values_overlap("between", [18, 30], "in", [20, 25]))

    def test_between_vs_in_partial(self):
        self.assertIsNone(_values_overlap("between", [18, 30], "in", [25, 40]))

    # --- cross-operator pairs: still unprovable ---
    def test_in_vs_eq_unprovable(self):
        self.assertIsNone(_values_overlap("in", ["US", "CN"], "eq", "US"))

    def test_is_not_null_vs_eq_unprovable(self):
        self.assertIsNone(_values_overlap("is_not_null", None, "eq", "US"))

    def test_is_not_null_vs_in_unprovable(self):
        self.assertIsNone(_values_overlap("is_not_null", None, "in", ["US"]))

    # --- unsupported operators: always unprovable on same target ---
    def test_neq_vs_eq_unprovable(self):
        self.assertIsNone(_values_overlap("neq", "US", "eq", "US"))

    def test_not_in_vs_in_unprovable(self):
        self.assertIsNone(_values_overlap("not_in", ["US"], "in", ["US", "CN"]))

    # --- null-check vs value ops: unprovable (cross-operator) ---
    def test_is_null_vs_eq_unprovable(self):
        self.assertIsNone(_values_overlap("is_null", None, "eq", "US"))

    def test_is_null_vs_in_unprovable(self):
        self.assertIsNone(_values_overlap("is_null", None, "in", ["US"]))

    # --- mixed types — unprovable ---
    def test_eq_gte_mixed_types(self):
        self.assertIsNone(_values_overlap("eq", "US", "gte", 18))


# ---------------------------------------------------------------------------
# Unit tests — _check_scope_target_exclusions
# ---------------------------------------------------------------------------


class TestScopeTargetExclusions(unittest.TestCase):
    def test_governance_target_excluded(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.tenant_id", "value": "x"}]
        excluded = {"dimension.tenant_id": "governance_policy"}
        issues = _check_scope_target_exclusions(scope_atoms, excluded, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_TARGET_EXCLUDED")
        self.assertEqual(issues[0].details["governing_usage"], "governance_policy")

    def test_carrier_target_excluded(self):
        scope_atoms = [{"op": "is_not_null", "target_ref": "dimension.soft_delete_flag"}]
        excluded = {"dimension.soft_delete_flag": "carrier_row_filter"}
        issues = _check_scope_target_exclusions(scope_atoms, excluded, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_TARGET_EXCLUDED")
        self.assertEqual(issues[0].details["governing_usage"], "carrier_row_filter")

    def test_metric_qualifier_target_not_excluded(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        excluded: dict[str, str] = {}
        issues = _check_scope_target_exclusions(scope_atoms, excluded, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_no_overlap_passes(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.region", "value": "east"}]
        excluded = {"dimension.tenant_id": "governance_policy"}
        issues = _check_scope_target_exclusions(scope_atoms, excluded, "predicate.scope1")
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Unit tests — _check_scope_narrowing (4.4)
# ---------------------------------------------------------------------------


class TestScopeNarrowing(unittest.TestCase):
    def test_scope_target_not_in_upstream_passes(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.region", "value": "east"}]
        upstream_by_target = {
            "dimension.country": [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_eq_eq_same_value_passes(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        upstream_by_target = {
            "dimension.country": [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_eq_eq_different_value_contradicts(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "CN"}]
        upstream_by_target = {
            "dimension.country": [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_in_in_subset_passes(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.country", "value": ["US"]}]
        upstream_by_target = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_in_in_disjoint_contradicts(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.country", "value": ["US"]}]
        upstream_by_target = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["CN", "JP"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_in_in_overlap_not_subset_unprovable(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}]
        upstream_by_target = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["CN", "JP"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")
        self.assertEqual(issues[0].details["reason"], "not_subset")

    def test_gte_gte_request_greater_passes(self):
        scope_atoms = [{"op": "gte", "target_ref": "dimension.age", "value": 25}]
        upstream_by_target = {
            "dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_gte_gte_request_lower_contradicts(self):
        scope_atoms = [{"op": "gte", "target_ref": "dimension.age", "value": 10}]
        upstream_by_target = {
            "dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_between_between_subset_passes(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [25, 50]}]
        upstream_by_target = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_between_between_disjoint_contradicts(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [70, 90]}]
        upstream_by_target = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_between_between_partial_overlap_unprovable(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [25, 70]}]
        upstream_by_target = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")
        self.assertEqual(issues[0].details["reason"], "not_subset")

    def test_is_null_vs_is_not_null_contradicts(self):
        scope_atoms = [{"op": "is_null", "target_ref": "dimension.flag"}]
        upstream_by_target = {
            "dimension.flag": [{"op": "is_not_null", "target_ref": "dimension.flag"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_cross_operator_pair_unprovable(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.country", "value": ["CN", "US"]}]
        upstream_by_target = {
            "dimension.country": [{"op": "eq", "target_ref": "dimension.country", "value": "CN"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")
        self.assertEqual(issues[0].details["reason"], "cross_operator")

    def test_neq_scope_always_unprovable_on_same_target(self):
        scope_atoms = [{"op": "neq", "target_ref": "dimension.country", "value": "US"}]
        upstream_by_target = {
            "dimension.country": [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")

    def test_multiple_scope_targets_checked_independently(self):
        scope_atoms = [
            {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            {"op": "gte", "target_ref": "dimension.age", "value": 25},
        ]
        upstream_by_target = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ],
            "dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}],
        }
        issues = _check_scope_narrowing(scope_atoms, upstream_by_target, "predicate.scope1")
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Integration tests — validate_request_scope
# ---------------------------------------------------------------------------


class TestValidateRequestScope(unittest.TestCase):
    def test_no_scope_ref_returns_empty(self):
        issues = validate_request_scope(
            request_scope_ref=None,
            upstream_predicates=[],
            resolver=_StubResolver(),
        )
        self.assertEqual(issues, [])

    def test_unresolvable_scope_returns_empty(self):
        issues = validate_request_scope(
            request_scope_ref="predicate.nonexistent",
            upstream_predicates=[],
            resolver=_StubResolver(),
        )
        self.assertEqual(issues, [])

    def test_valid_scope_eq_narrows_in(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_cn",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.country", "value": "CN"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.upstream_region",
            interface_contract={
                "expression": {
                    "op": "in",
                    "target_ref": "dimension.country",
                    "value": ["US", "CN", "JP"],
                },
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={"predicate.scope_cn": scope_pred, "predicate.upstream_region": upstream_pred}
        )
        upstream_refs = [
            PredicateRefWithUsage(
                ref="predicate.upstream_region", required_usage="metric_qualifier"
            )
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_cn",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        self.assertEqual(issues, [])

    def test_scope_adds_new_target_passes(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_platform",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.platform", "value": "ios"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.upstream_region",
            interface_contract={
                "expression": {
                    "op": "in",
                    "target_ref": "dimension.country",
                    "value": ["US", "CN"],
                },
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={
                "predicate.scope_platform": scope_pred,
                "predicate.upstream_region": upstream_pred,
            }
        )
        upstream_refs = [
            PredicateRefWithUsage(
                ref="predicate.upstream_region", required_usage="metric_qualifier"
            )
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_platform",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        self.assertEqual(issues, [])

    def test_scope_time_condition_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_time",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "time.created_at", "value": "2024"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_time": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_time",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_TIME_CONDITION", codes)

    def test_scope_contradicts_upstream(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_cn",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.country", "value": "CN"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.upstream_us",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={"predicate.scope_cn": scope_pred, "predicate.upstream_us": upstream_pred}
        )
        upstream_refs = [
            PredicateRefWithUsage(ref="predicate.upstream_us", required_usage="metric_qualifier")
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_cn",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_CONTRADICTS_UPSTREAM", codes)

    def test_scope_cross_operator_unprovable(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_in",
            interface_contract={
                "expression": {
                    "op": "in",
                    "target_ref": "dimension.country",
                    "value": ["CN", "US"],
                },
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.upstream_eq",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.country", "value": "CN"},
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={"predicate.scope_in": scope_pred, "predicate.upstream_eq": upstream_pred}
        )
        upstream_refs = [
            PredicateRefWithUsage(ref="predicate.upstream_eq", required_usage="metric_qualifier")
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_in",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_NARROWING_UNPROVABLE", codes)

    def test_scope_governance_target_excluded(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_tenant",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.tenant_id", "value": "t1"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.governance_tenant",
            interface_contract={
                "expression": {"op": "is_not_null", "target_ref": "dimension.tenant_id"},
                "allowed_usage": ["governance_policy"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={
                "predicate.scope_tenant": scope_pred,
                "predicate.governance_tenant": upstream_pred,
            }
        )
        upstream_refs = [
            PredicateRefWithUsage(
                ref="predicate.governance_tenant", required_usage="governance_policy"
            )
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_tenant",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_TARGET_EXCLUDED", codes)

    def test_scope_carrier_target_excluded(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_delete",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.soft_delete", "value": "N"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        upstream_pred = _resolved_predicate(
            "predicate.carrier_delete",
            interface_contract={
                "expression": {"op": "eq", "target_ref": "dimension.soft_delete", "value": "N"},
                "allowed_usage": ["carrier_row_filter"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(
            resolved={
                "predicate.scope_delete": scope_pred,
                "predicate.carrier_delete": upstream_pred,
            }
        )
        upstream_refs = [
            PredicateRefWithUsage(
                ref="predicate.carrier_delete", required_usage="carrier_row_filter"
            )
        ]
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_delete",
            upstream_predicates=upstream_refs,
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_TARGET_EXCLUDED", codes)

    def test_scope_disjunctive_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_or",
            interface_contract={
                "expression": {
                    "op": "or",
                    "items": [
                        {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                        {"op": "eq", "target_ref": "dimension.country", "value": "CN"},
                    ],
                },
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_or": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_or",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_DISJUNCTIVE", codes)

    def test_scope_negation_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_not",
            interface_contract={
                "expression": {
                    "op": "not",
                    "items": [
                        {"op": "eq", "target_ref": "dimension.country", "value": "US"},
                    ],
                },
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_not": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_not",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_NEGATION", codes)

    def test_scope_dynamic_value_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_dyn",
            interface_contract={
                "expression": {"op": "gt", "target_ref": "dimension.ts", "value": "now()"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_dyn": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_dyn",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_DYNAMIC_VALUE", codes)

    def test_scope_neq_on_new_target_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_neq",
            interface_contract={
                "expression": {"op": "neq", "target_ref": "dimension.status", "value": "deleted"},
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_neq": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_neq",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_FORBIDDEN_OPERATOR", codes)

    def test_scope_not_in_on_new_target_fails(self):
        scope_pred = _resolved_predicate(
            "predicate.scope_not_in",
            interface_contract={
                "expression": {
                    "op": "not_in",
                    "target_ref": "dimension.platform",
                    "value": ["web"],
                },
                "allowed_usage": ["request_scope"],
                "time_policy": "non_time_only",
            },
        )
        resolver = _StubResolver(resolved={"predicate.scope_not_in": scope_pred})
        issues = validate_request_scope(
            request_scope_ref="predicate.scope_not_in",
            upstream_predicates=[],
            resolver=resolver,
        )
        codes = [i.code for i in issues]
        self.assertIn("COMPILER_SCOPE_FORBIDDEN_OPERATOR", codes)


# ---------------------------------------------------------------------------
# Unit tests — governance predicate collection (P1 fix)
# ---------------------------------------------------------------------------


class TestCollectGovernancePredicateRefs(unittest.TestCase):
    def test_none_repository_returns_empty(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        refs = _collect_governance_predicate_refs(None)
        self.assertEqual(refs, [])

    def test_row_filter_policy_predicate_ref_collected(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _StubGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.tenant_isolation"},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_StubGovRepo())
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].ref, "predicate.tenant_isolation")
        self.assertEqual(refs[0].required_usage, "governance_policy")

    def test_non_row_filter_policy_ignored(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _StubGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "aggregate_only",
                        "definition": {},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_StubGovRepo())
        self.assertEqual(refs, [])

    def test_duplicate_predicate_ref_deduped(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _StubGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.tenant_iso"},
                        "enabled": True,
                    },
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.tenant_iso"},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_StubGovRepo())
        self.assertEqual(len(refs), 1)

    def test_policy_without_predicate_ref_skipped(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _StubGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"sql": "tenant_id = 'x'"},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_StubGovRepo())
        self.assertEqual(refs, [])

    def test_scope_matching_step_type_included(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _ScopedGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.matching"},
                        "scope": {"step_types": ["metric_query"]},
                        "enabled": True,
                    },
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.wrong_step"},
                        "scope": {"step_types": ["aggregate_query"]},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_ScopedGovRepo(), step_type="metric_query")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].ref, "predicate.matching")

    def test_scope_matching_table_included(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _ScopedGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.orders_policy"},
                        "scope": {"tables": ["orders"]},
                        "enabled": True,
                    },
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.users_policy"},
                        "scope": {"tables": ["users"]},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(_ScopedGovRepo(), tables={"orders"})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].ref, "predicate.orders_policy")

    def test_no_scope_restriction_always_included(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _NoScopeGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.no_scope"},
                        "enabled": True,
                    },
                ]

        refs = _collect_governance_predicate_refs(
            _NoScopeGovRepo(), step_type="metric_query", tables={"orders"}
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].ref, "predicate.no_scope")

    def test_no_scope_context_excludes_scoped_policies(self):
        from app.analysis_core.validator import _collect_governance_predicate_refs

        class _ScopedGovRepo:
            def list_policies(self, enabled_only=True):
                return [
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.scoped"},
                        "scope": {"step_types": ["metric_query"]},
                        "enabled": True,
                    },
                    {
                        "policy_type": "row_filter",
                        "definition": {"predicate_ref": "predicate.unscoped"},
                        "enabled": True,
                    },
                ]

        # No step_type/tables context: scoped policy excluded, unscoped included
        refs = _collect_governance_predicate_refs(_ScopedGovRepo())
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].ref, "predicate.unscoped")


# ---------------------------------------------------------------------------
# Task 7.3 — Cross-operator narrowing at _check_scope_narrowing level
# ---------------------------------------------------------------------------


class TestScopeNarrowingCrossOperator(unittest.TestCase):
    """Cross-operator narrowing pairs validated through _check_scope_narrowing."""

    # --- narrowing success ---

    def test_eq_scope_narrows_gte_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.age", "value": 25}]
        upstream = {"dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_eq_scope_narrows_between_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.age", "value": 25}]
        upstream = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_eq_scope_narrows_in_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_between_scope_widens_eq_upstream_unprovable(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 25}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")

    def test_gte_scope_widens_eq_upstream_unprovable(self):
        scope_atoms = [{"op": "gte", "target_ref": "dimension.age", "value": 18}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 25}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")

    def test_lte_scope_widens_eq_upstream_unprovable(self):
        scope_atoms = [{"op": "lte", "target_ref": "dimension.age", "value": 65}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 50}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")

    def test_in_scope_narrows_between_upstream(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.age", "value": [20, 25]}]
        upstream = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 30]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_between_scope_narrows_in_upstream(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [18, 30]}]
        upstream = {
            "dimension.age": [{"op": "in", "target_ref": "dimension.age", "value": [20, 25]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    # --- contradiction (widening / refusal) ---

    def test_eq_scope_contradicts_gte_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.age", "value": 10}]
        upstream = {"dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_eq_scope_contradicts_between_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.age", "value": 70}]
        upstream = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_eq_scope_contradicts_in_upstream(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "JP"}]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_between_scope_contradicts_eq_upstream(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [70, 90]}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 25}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_gte_scope_contradicts_eq_upstream(self):
        scope_atoms = [{"op": "gte", "target_ref": "dimension.age", "value": 30}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 25}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_lte_scope_contradicts_eq_upstream(self):
        scope_atoms = [{"op": "lte", "target_ref": "dimension.age", "value": 40}]
        upstream = {"dimension.age": [{"op": "eq", "target_ref": "dimension.age", "value": 50}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_in_scope_contradicts_between_upstream(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.age", "value": [5, 10]}]
        upstream = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 30]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_between_scope_contradicts_in_upstream(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [70, 90]}]
        upstream = {
            "dimension.age": [{"op": "in", "target_ref": "dimension.age", "value": [20, 25]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")


# ---------------------------------------------------------------------------
# Task 7.3 — Scope relaxation (widening) tests
# ---------------------------------------------------------------------------


class TestScopeNarrowingRelaxation(unittest.TestCase):
    """Explicit 'widening fails' tests — scope tries to relax upstream constraints."""

    def test_gte_scope_lower_than_upstream_relaxes(self):
        scope_atoms = [{"op": "gte", "target_ref": "dimension.age", "value": 10}]
        upstream = {"dimension.age": [{"op": "gte", "target_ref": "dimension.age", "value": 18}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_lte_scope_higher_than_upstream_relaxes(self):
        scope_atoms = [{"op": "lte", "target_ref": "dimension.age", "value": 80}]
        upstream = {"dimension.age": [{"op": "lte", "target_ref": "dimension.age", "value": 65}]}
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_CONTRADICTS_UPSTREAM")

    def test_in_scope_superset_of_upstream_relaxes(self):
        scope_atoms = [{"op": "in", "target_ref": "dimension.country", "value": ["US", "CN", "JP"]}]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")
        self.assertEqual(issues[0].details["reason"], "not_subset")

    def test_between_scope_wider_than_upstream_relaxes(self):
        scope_atoms = [{"op": "between", "target_ref": "dimension.age", "value": [10, 80]}]
        upstream = {
            "dimension.age": [{"op": "between", "target_ref": "dimension.age", "value": [18, 65]}]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_SCOPE_NARROWING_UNPROVABLE")
        self.assertEqual(issues[0].details["reason"], "not_subset")


# ---------------------------------------------------------------------------
# Task 7.3 — Multi-target narrowing with mixed results
# ---------------------------------------------------------------------------


class TestScopeNarrowingMultiTarget(unittest.TestCase):
    """Mixed narrowing results across different targets."""

    def test_partial_narrowing_one_passes_one_contradicts(self):
        scope_atoms = [
            {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            {"op": "eq", "target_ref": "dimension.status", "value": "CN"},
        ]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ],
            "dimension.status": [{"op": "eq", "target_ref": "dimension.status", "value": "active"}],
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        contradicts = [i for i in issues if i.code == "COMPILER_SCOPE_CONTRADICTS_UPSTREAM"]
        self.assertEqual(len(contradicts), 1)
        self.assertEqual(contradicts[0].details["target_ref"], "dimension.status")

    def test_partial_narrowing_one_passes_one_unprovable(self):
        scope_atoms = [
            {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            {"op": "in", "target_ref": "dimension.region", "value": ["east", "west"]},
        ]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]}
            ],
            "dimension.region": [{"op": "eq", "target_ref": "dimension.region", "value": "east"}],
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        unprovable = [i for i in issues if i.code == "COMPILER_SCOPE_NARROWING_UNPROVABLE"]
        self.assertEqual(len(unprovable), 1)
        self.assertEqual(unprovable[0].details["target_ref"], "dimension.region")


# ---------------------------------------------------------------------------
# Task 7.3 — Multiple upstream predicates on same target
# ---------------------------------------------------------------------------


class TestScopeNarrowingMultipleUpstream(unittest.TestCase):
    """Scope must narrow against every upstream predicate on the same target."""

    def test_scope_narrows_both_upstreams(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "US"}]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]},
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        self.assertEqual(issues, [])

    def test_scope_contradicts_one_upstream_narrows_other(self):
        scope_atoms = [{"op": "eq", "target_ref": "dimension.country", "value": "JP"}]
        upstream = {
            "dimension.country": [
                {"op": "in", "target_ref": "dimension.country", "value": ["US", "CN"]},
                {"op": "eq", "target_ref": "dimension.country", "value": "US"},
            ]
        }
        issues = _check_scope_narrowing(scope_atoms, upstream, "predicate.scope1")
        contradicts = [i for i in issues if i.code == "COMPILER_SCOPE_CONTRADICTS_UPSTREAM"]
        self.assertGreaterEqual(len(contradicts), 1)
