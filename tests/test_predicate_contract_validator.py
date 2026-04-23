from __future__ import annotations

import unittest
from typing import Any

from app.analysis_core.predicate_validator import (
    _check_allowed_usage_nonempty,
    _check_expression_deterministic,
    _check_predicate_ref_prefix,
    _check_predicate_resolved,
    _check_subject_ref_resolvable,
    _check_target_refs_resolvable,
    _check_time_policy,
    _contains_dynamic_value,
    _extract_target_refs,
    _resolve_entity_ref_from_alias,
    validate_predicate_contracts,
)
from app.analysis_core.validator import (
    validate_compiler_inputs,
)
from app.semantic_runtime.errors import (
    SemanticRuntimeNotFoundError,
)
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
    header: dict[str, Any] | None = None,
    interface_contract: dict[str, Any] | None = None,
    status: str = "published",
) -> ResolvedSemanticObject:
    return ResolvedSemanticObject(
        object_kind="predicate",
        object_id="predicate_1",
        ref=predicate_ref,
        semantic_object={
            "header": header
            or {
                "predicate_ref": predicate_ref,
                "subject_ref": "entity.test_entity",
                "predicate_contract_version": "predicate.v1",
            },
            "interface_contract": interface_contract
            or {
                "expression": {
                    "op": "and",
                    "items": [
                        {
                            "op": "eq",
                            "target_ref": "dimension.test_dim",
                            "value": "active",
                        }
                    ],
                },
                "allowed_usage": ["metric_qualifier"],
                "time_policy": "non_time_only",
            },
            "status": status,
        },
        status=status,
        revision=1,
        created_at="2026-04-23T00:00:00Z",
        updated_at="2026-04-23T00:00:00Z",
    )


class _StubResolver:
    """Minimal resolver stub for unit testing predicate checks."""

    def __init__(
        self,
        *,
        resolved: dict[str, ResolvedSemanticObject] | None = None,
        availability: dict[str, RuntimeSemanticAvailability] | None = None,
    ) -> None:
        self._resolved = resolved or {}
        self._availability = availability or {}

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        if semantic_ref in self._resolved:
            return self._resolved[semantic_ref]
        raise SemanticRuntimeNotFoundError(f"Not found: {semantic_ref}", semantic_ref=semantic_ref)

    def inspect_ref(self, semantic_ref: str) -> RuntimeSemanticAvailability:
        if semantic_ref in self._availability:
            return self._availability[semantic_ref]
        resolved = self._resolved.get(semantic_ref)
        if resolved is not None:
            return RuntimeSemanticAvailability(
                resolved=resolved,
                lifecycle_status="active",
                readiness_status="ready",
            )
        raise SemanticRuntimeNotFoundError(f"Not found: {semantic_ref}", semantic_ref=semantic_ref)


# ---------------------------------------------------------------------------
# Unit tests — individual check functions
# ---------------------------------------------------------------------------


class TestPredicateRefPrefix(unittest.TestCase):
    def test_valid_prefix(self):
        issues = _check_predicate_ref_prefix("predicate.exclude_test")
        self.assertEqual(issues, [])

    def test_invalid_prefix(self):
        issues = _check_predicate_ref_prefix("metric.something")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_REF_INVALID")
        self.assertEqual(issues[0].severity, "error")


class TestPredicateResolved(unittest.TestCase):
    def test_active_ready(self):
        resolved = _resolved_predicate("predicate.active_one")
        availability = RuntimeSemanticAvailability(
            resolved=resolved, lifecycle_status="active", readiness_status="ready"
        )
        resolver = _StubResolver(
            resolved={"predicate.active_one": resolved},
            availability={"predicate.active_one": availability},
        )
        issues = _check_predicate_resolved("predicate.active_one", resolved, resolver)
        self.assertEqual(issues, [])

    def test_not_found(self):
        resolver = _StubResolver()
        issues = _check_predicate_resolved("predicate.missing", None, resolver)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_REF_UNRESOLVED")

    def test_not_ready(self):
        resolved = _resolved_predicate("predicate.draft_one")
        availability = RuntimeSemanticAvailability(
            resolved=resolved, lifecycle_status="active", readiness_status="blocked"
        )
        resolver = _StubResolver(
            resolved={"predicate.draft_one": resolved},
            availability={"predicate.draft_one": availability},
        )
        issues = _check_predicate_resolved("predicate.draft_one", resolved, resolver)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_REF_UNRESOLVED")


class TestSubjectRefResolvable(unittest.TestCase):
    def test_entity_exists(self):
        entity = ResolvedSemanticObject(
            object_kind="entity",
            object_id="entity_1",
            ref="entity.test_entity",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        resolver = _StubResolver(resolved={"entity.test_entity": entity})
        header = {"subject_ref": "entity.test_entity"}
        issues = _check_subject_ref_resolvable(header, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_subject_alias_resolves(self):
        entity = ResolvedSemanticObject(
            object_kind="entity",
            object_id="entity_1",
            ref="entity.test_entity",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        resolver = _StubResolver(resolved={"entity.test_entity": entity})
        header = {"subject_ref": "subject.test_entity"}
        issues = _check_subject_ref_resolvable(header, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_deprecated_entity_fails(self):
        resolver = _StubResolver()
        header = {"subject_ref": "entity.deprecated"}
        issues = _check_subject_ref_resolvable(header, "predicate.test", resolver)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_SUBJECT_UNRESOLVED")


class TestTargetRefsResolvable(unittest.TestCase):
    def test_dimension_exists(self):
        dim = ResolvedSemanticObject(
            object_kind="dimension",
            object_id="dim_1",
            ref="dimension.test_dim",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        resolver = _StubResolver(resolved={"dimension.test_dim": dim})
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "dimension.test_dim",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_unknown_dimension_fails(self):
        resolver = _StubResolver()
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "dimension.missing",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_TARGET_UNRESOLVED")

    def test_key_prefix_only_check_passes(self):
        resolver = _StubResolver()
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "key.test_key",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_enum_prefix_only_check_passes(self):
        resolver = _StubResolver()
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "enum.status",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_field_prefix_only_check_passes(self):
        resolver = _StubResolver()
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "field.test_field",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(issues, [])

    def test_forbidden_time_prefix_fails(self):
        resolver = _StubResolver()
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "time.created_at",
                "value": "x",
            }
        }
        issues = _check_target_refs_resolvable(contract, "predicate.test", resolver)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_TARGET_UNRESOLVED")


class TestAllowedUsageNonempty(unittest.TestCase):
    def test_non_empty_passes(self):
        contract = {"allowed_usage": ["metric_qualifier"]}
        issues = _check_allowed_usage_nonempty(contract, "predicate.test")
        self.assertEqual(issues, [])

    def test_empty_fails(self):
        contract = {"allowed_usage": []}
        issues = _check_allowed_usage_nonempty(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_USAGE_EMPTY")


class TestTimePolicy(unittest.TestCase):
    def test_non_time_only_passes(self):
        contract = {"time_policy": "non_time_only"}
        issues = _check_time_policy(contract, "predicate.test")
        self.assertEqual(issues, [])

    def test_other_value_fails(self):
        contract = {"time_policy": "time_windowed"}
        issues = _check_time_policy(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_TIME_POLICY_INVALID")


class TestExpressionDeterministic(unittest.TestCase):
    def test_valid_atom_passes(self):
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "dimension.status",
                "value": "active",
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(issues, [])

    def test_valid_conjunction_passes(self):
        contract = {
            "expression": {
                "op": "and",
                "items": [
                    {"op": "eq", "target_ref": "dimension.a", "value": "1"},
                    {"op": "neq", "target_ref": "dimension.b", "value": "2"},
                ],
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(issues, [])

    def test_or_op_fails(self):
        contract = {
            "expression": {
                "op": "or",
                "items": [
                    {"op": "eq", "target_ref": "dimension.a", "value": "1"},
                    {"op": "eq", "target_ref": "dimension.b", "value": "2"},
                ],
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC")

    def test_not_op_fails(self):
        contract = {
            "expression": {
                "op": "not",
                "items": [
                    {"op": "eq", "target_ref": "dimension.a", "value": "1"},
                ],
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC")

    def test_time_target_fails(self):
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "time.created_at",
                "value": "2026-01-01",
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC")

    def test_dynamic_now_fails(self):
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "dimension.status",
                "value": "now()",
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC")

    def test_dynamic_current_timestamp_fails(self):
        contract = {
            "expression": {
                "op": "gte",
                "target_ref": "dimension.ts",
                "value": "current_timestamp()",
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)

    def test_dynamic_variable_fails(self):
        contract = {
            "expression": {
                "op": "eq",
                "target_ref": "dimension.env",
                "value": "${ENV_VAR}",
            }
        }
        issues = _check_expression_deterministic(contract, "predicate.test")
        self.assertEqual(len(issues), 1)


class TestContainsDynamicValue(unittest.TestCase):
    def test_plain_string(self):
        self.assertFalse(_contains_dynamic_value("hello"))

    def test_now(self):
        self.assertTrue(_contains_dynamic_value("now()"))

    def test_current_timestamp(self):
        self.assertTrue(_contains_dynamic_value("current_timestamp()"))

    def test_variable_ref(self):
        self.assertTrue(_contains_dynamic_value("${VAR}"))

    def test_list_contains_dynamic(self):
        self.assertTrue(_contains_dynamic_value(["a", "now()"]))

    def test_list_all_static(self):
        self.assertFalse(_contains_dynamic_value(["a", "b"]))


class TestExtractTargetRefs(unittest.TestCase):
    def test_single_atom(self):
        expr = {"target_ref": "dimension.x", "op": "eq", "value": "1"}
        self.assertEqual(_extract_target_refs(expr), ["dimension.x"])

    def test_conjunction(self):
        expr = {
            "op": "and",
            "items": [
                {"target_ref": "dimension.a", "op": "eq", "value": "1"},
                {"target_ref": "dimension.b", "op": "neq", "value": "2"},
            ],
        }
        self.assertEqual(_extract_target_refs(expr), ["dimension.a", "dimension.b"])

    def test_nested(self):
        expr = {
            "op": "and",
            "items": [
                {"target_ref": "dimension.a", "op": "eq", "value": "1"},
                {
                    "op": "and",
                    "items": [
                        {"target_ref": "dimension.b", "op": "eq", "value": "2"},
                    ],
                },
            ],
        }
        self.assertEqual(_extract_target_refs(expr), ["dimension.a", "dimension.b"])


class TestResolveEntityRefFromAlias(unittest.TestCase):
    def test_subject_alias(self):
        self.assertEqual(_resolve_entity_ref_from_alias("subject.user"), "entity.user")

    def test_population_alias(self):
        self.assertEqual(_resolve_entity_ref_from_alias("population.user"), "entity.user")

    def test_event_alias(self):
        self.assertEqual(_resolve_entity_ref_from_alias("event.click"), "entity.click")

    def test_entity_passthrough(self):
        self.assertEqual(_resolve_entity_ref_from_alias("entity.user"), "entity.user")

    def test_dimension_passthrough(self):
        self.assertEqual(_resolve_entity_ref_from_alias("dimension.status"), "dimension.status")


# ---------------------------------------------------------------------------
# Integration — validate_predicate_contracts end-to-end
# ---------------------------------------------------------------------------


class TestValidatePredicateContractsIntegration(unittest.TestCase):
    def test_valid_predicate_no_issues(self):
        predicate = _resolved_predicate("predicate.valid_one")
        entity = ResolvedSemanticObject(
            object_kind="entity",
            object_id="entity_1",
            ref="entity.test_entity",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        dim = ResolvedSemanticObject(
            object_kind="dimension",
            object_id="dim_1",
            ref="dimension.test_dim",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        availability = RuntimeSemanticAvailability(
            resolved=predicate, lifecycle_status="active", readiness_status="ready"
        )
        resolver = _StubResolver(
            resolved={
                "predicate.valid_one": predicate,
                "entity.test_entity": entity,
                "dimension.test_dim": dim,
            },
            availability={"predicate.valid_one": availability},
        )
        issues = validate_predicate_contracts(
            predicate_refs=["predicate.valid_one"],
            resolver=resolver,
        )
        self.assertEqual(issues, [])

    def test_invalid_ref_prefix(self):
        resolver = _StubResolver()
        issues = validate_predicate_contracts(
            predicate_refs=["metric.bad_ref"],
            resolver=resolver,
        )
        self.assertTrue(any(i.code == "COMPILER_PREDICATE_REF_INVALID" for i in issues))

    def test_unresolved_predicate(self):
        resolver = _StubResolver()
        issues = validate_predicate_contracts(
            predicate_refs=["predicate.missing"],
            resolver=resolver,
        )
        self.assertTrue(any(i.code == "COMPILER_PREDICATE_REF_UNRESOLVED" for i in issues))

    def test_multiple_invalid_predicates_all_reported(self):
        """Verify one failure does not suppress diagnostics for the rest."""
        resolver = _StubResolver()
        issues = validate_predicate_contracts(
            predicate_refs=["predicate.missing_a", "predicate.missing_b"],
            resolver=resolver,
        )
        unresolved = [i for i in issues if i.code == "COMPILER_PREDICATE_REF_UNRESOLVED"]
        self.assertEqual(len(unresolved), 2)
        reported_refs = {i.subject_ref for i in unresolved}
        self.assertEqual(reported_refs, {"predicate.missing_a", "predicate.missing_b"})


# ---------------------------------------------------------------------------
# Integration — compiler flow via validate_compiler_inputs
# ---------------------------------------------------------------------------


class TestCompilerPredicateGate(unittest.TestCase):
    """Test the predicate_contract gate wired into validate_compiler_inputs."""

    def _make_resolved_inputs(
        self,
        *,
        predicate_ref: str | None = None,
        metric_semantic_object: dict[str, Any] | None = None,
    ) -> Any:
        from app.analysis_core.typed_resolution import (
            NormalizedCompilerRequest,
            ResolvedCompilerInputs,
        )

        metric_obj = metric_semantic_object or {
            "header": {"metric_ref": "metric.test"},
            "payload": {},
        }
        metric = ResolvedSemanticObject(
            object_kind="metric",
            object_id="metric_1",
            ref="metric.test",
            semantic_object=metric_obj,
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        binding = ResolvedSemanticObject(
            object_kind="binding",
            object_id="binding_1",
            ref="binding.test",
            semantic_object={
                "header": {"binding_ref": "binding.test"},
                "interface_contract": {
                    "carrier_bindings": [{"binding_key": "primary", "carrier_kind": "table"}],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input"},
                        }
                    ],
                },
            },
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        return ResolvedCompilerInputs(
            normalized_request=NormalizedCompilerRequest(
                intent_kind="metric_query",
                request_class="root_metric_process",
                table_name=None,
                metric_ref="metric.test",
                request_scope_predicate_ref=predicate_ref,
            ),
            resolved_metric=metric,
            resolved_bindings=[binding],
        )

    def test_no_repository_skips_gate(self):
        from app.analysis_core.capability_profiles import DerivedCompilerState

        inputs = self._make_resolved_inputs(predicate_ref="predicate.test")
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=None,
        )
        # Gate is skipped; no predicate issues
        self.assertTrue(result.ok)

    def test_with_valid_predicate_passes(self):
        from app.analysis_core.capability_profiles import DerivedCompilerState

        predicate = _resolved_predicate("predicate.test")
        entity = ResolvedSemanticObject(
            object_kind="entity",
            object_id="entity_1",
            ref="entity.test_entity",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        dim = ResolvedSemanticObject(
            object_kind="dimension",
            object_id="dim_1",
            ref="dimension.test_dim",
            semantic_object={},
            status="published",
            revision=1,
            created_at="2026-04-23T00:00:00Z",
            updated_at="2026-04-23T00:00:00Z",
        )
        availability = RuntimeSemanticAvailability(
            resolved=predicate, lifecycle_status="active", readiness_status="ready"
        )
        resolver = _StubResolver(
            resolved={
                "predicate.test": predicate,
                "entity.test_entity": entity,
                "dimension.test_dim": dim,
            },
            availability={"predicate.test": availability},
        )
        inputs = self._make_resolved_inputs(predicate_ref="predicate.test")
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=resolver,
        )
        self.assertTrue(result.ok)

    def test_with_invalid_predicate_fails(self):
        from app.analysis_core.capability_profiles import DerivedCompilerState

        resolver = _StubResolver()
        inputs = self._make_resolved_inputs(predicate_ref="predicate.nonexistent")
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=resolver,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any(i.code == "COMPILER_PREDICATE_REF_UNRESOLVED" for i in result.issues))

    def test_metric_header_default_predicate_refs_collected(self):
        """default_predicate_refs on the metric header are collected and validated."""
        from app.analysis_core.capability_profiles import DerivedCompilerState

        metric_obj = {
            "header": {
                "metric_ref": "metric.test",
                "default_predicate_refs": ["predicate.bad_default"],
            },
            "payload": {},
        }
        resolver = _StubResolver()
        inputs = self._make_resolved_inputs(metric_semantic_object=metric_obj)
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=resolver,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any(i.code == "COMPILER_PREDICATE_REF_UNRESOLVED" for i in result.issues))

    def test_metric_payload_default_predicate_refs_collected(self):
        """default_predicate_refs on the metric payload (runtime path) are collected."""
        from app.analysis_core.capability_profiles import DerivedCompilerState

        metric_obj = {
            "header": {"metric_ref": "metric.test"},
            "payload": {"default_predicate_refs": ["predicate.payload_default"]},
        }
        resolver = _StubResolver()
        inputs = self._make_resolved_inputs(metric_semantic_object=metric_obj)
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=resolver,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any(i.code == "COMPILER_PREDICATE_REF_UNRESOLVED" for i in result.issues))

    def test_metric_component_qualifier_refs_collected(self):
        """Component qualifier_refs under family-specific payload fields are collected."""
        from app.analysis_core.capability_profiles import DerivedCompilerState

        metric_obj = {
            "header": {"metric_ref": "metric.test"},
            "payload": {
                "numerator": {"qualifier_refs": ["predicate.num_qualifier"]},
                "denominator": {"qualifier_refs": ["predicate.den_qualifier"]},
            },
        }
        resolver = _StubResolver()
        inputs = self._make_resolved_inputs(metric_semantic_object=metric_obj)
        derived = DerivedCompilerState(
            metric_capabilities=None,
            metric_requirements=None,
            process_capabilities=None,
            profile_validation_issues=[],
        )
        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=inputs,
            derived_state=derived,
            semantic_repository=resolver,
        )
        self.assertFalse(result.ok)
        unresolved = [i for i in result.issues if i.code == "COMPILER_PREDICATE_REF_UNRESOLVED"]
        reported_refs = {i.subject_ref for i in unresolved}
        self.assertTrue("predicate.num_qualifier" in reported_refs)
        self.assertTrue("predicate.den_qualifier" in reported_refs)


if __name__ == "__main__":
    unittest.main()
