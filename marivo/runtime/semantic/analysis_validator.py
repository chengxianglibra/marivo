# DEPRECATED: Pure validation logic extracted to app.core.semantic.validator.
# Data classes (ValidationIssue, ValidationResult), pure gate functions, and
# helpers live in the core module.  This file retains the orchestrator
# validate_compiler_inputs and I/O-bound gates (_gate_cross_entity_composition,
# _gate_predicate_contracts, _gate_scope_validation, _gate_predicate_conflict,
# _gate_lowering_precheck).
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from marivo.runtime.semantic.resolution_orchestrator import (
    ResolvedCompilerInputs,
    ResolvedRelationship,
)

if TYPE_CHECKING:
    from marivo.runtime.evidence.semantic_repository import SemanticRuntimeRepository
    from marivo.runtime.semantic.compile_step import DerivedCompilerState, PredicateRefWithUsage


@dataclass(slots=True)
class ValidationIssue:
    code: str
    gate: str
    category: str
    severity: str
    message: str
    subject_ref: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "gate": self.gate,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "subject_ref": self.subject_ref,
            "details": self.details,
        }


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    resolved_filter_time_ref: str | None = None
    validated_dimension_refs: list[str] = field(default_factory=list)

    def error_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def issues_for_category(self, category: str) -> list[ValidationIssue]:
        return [issue for issue in self.error_issues() if issue.category == category]

    def primary_error_issue(self) -> ValidationIssue:
        errors = self.error_issues()
        for category in ("compiler", "readiness", "compatibility"):
            for issue in errors:
                if issue.category == category:
                    return issue
        return errors[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "resolved_filter_time_ref": self.resolved_filter_time_ref,
            "validated_dimension_refs": list(self.validated_dimension_refs),
        }


def validate_compiler_inputs(
    *,
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
    semantic_repository: SemanticRuntimeRepository | None = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    issues.extend(_gate_profile_integrity(derived_state))
    issues.extend(_gate_request_shape(step_type, resolved_inputs))
    issues.extend(_gate_intent_support(step_type, resolved_inputs, derived_state))
    issues.extend(_gate_metric_process_compatibility(resolved_inputs, derived_state))
    issues.extend(_gate_entity_field_resolution(resolved_inputs))
    issues.extend(_gate_field_usage_compatibility(resolved_inputs, derived_state))
    issues.extend(
        _gate_cross_entity_composition(
            resolved_inputs,
            derived_state,
            semantic_repository,
        )
    )
    issues.extend(_gate_binding_compatibility(step_type, resolved_inputs))
    issues.extend(_gate_predicate_contracts(resolved_inputs, semantic_repository))
    issues.extend(_gate_scope_validation(resolved_inputs, semantic_repository))
    issues.extend(_gate_predicate_conflict(resolved_inputs, semantic_repository))
    issues.extend(_gate_dimension_compatibility(resolved_inputs))
    issues.extend(_gate_intent_specific(step_type, resolved_inputs, derived_state))
    issues.extend(_gate_dimension_additivity_condition(step_type, resolved_inputs, derived_state))
    issues.extend(_gate_lowering_precheck(resolved_inputs, semantic_repository))

    errors = [issue for issue in issues if issue.severity == "error"]
    return ValidationResult(
        ok=not errors,
        issues=issues,
        resolved_filter_time_ref=resolved_inputs.resolved_filter_time.ref
        if resolved_inputs.resolved_filter_time is not None
        else None,
        validated_dimension_refs=list(resolved_inputs.resolved_dimension_refs),
    )


def validation_error_message(result: ValidationResult) -> str:
    error = result.primary_error_issue()
    return f"{error.code}: {error.message}"


def _gate_profile_integrity(derived_state: DerivedCompilerState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for issue in derived_state.profile_validation_issues:
        issues.append(
            ValidationIssue(
                code=issue.code,
                gate="profile_integrity",
                category="readiness",
                severity="error",
                message=issue.message,
                subject_ref=issue.subject_ref,
                details=dict(issue.details),
            )
        )
    return issues


def _gate_request_shape(
    step_type: str, resolved_inputs: ResolvedCompilerInputs
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    normalized = resolved_inputs.normalized_request
    if step_type == "metric_query" and normalized.metric_ref is None:
        issues.append(
            ValidationIssue(
                code="COMPILER_REQUEST_INVALID",
                gate="request_shape",
                category="compiler",
                severity="error",
                message="metric_query requires a normalized metric_ref",
            )
        )
    request_time_scope = normalized.request_time_scope or {}
    if (
        normalized.metric_ref is not None
        and request_time_scope
        and resolved_inputs.resolved_filter_time is None
        and (
            resolved_inputs.resolved_metric is not None
            or resolved_inputs.resolved_process is not None
        )
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_TIME_REF_UNRESOLVED",
                gate="request_shape",
                category="compiler",
                severity="error",
                message="Request time_scope could not be resolved to a published time ref",
                subject_ref=normalized.metric_ref,
            )
        )
    return issues


def _gate_intent_support(
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    capabilities = derived_state.metric_capabilities
    if capabilities is None or resolved_inputs.resolved_metric is None:
        return issues
    if (
        step_type == "metric_query"
        and resolved_inputs.normalized_request.request_time_scope
        and not capabilities.supports_compare
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_INTENT_UNSUPPORTED",
                gate="intent_support",
                category="compatibility",
                severity="error",
                message="Metric does not support time-window comparison semantics",
                subject_ref=resolved_inputs.resolved_metric.ref,
            )
        )
    return issues


def _gate_metric_process_compatibility(
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    metric = resolved_inputs.resolved_metric
    process = resolved_inputs.resolved_process
    requirement = derived_state.metric_requirements
    if metric is None:
        return issues
    if process is None:
        if resolved_inputs.normalized_request.intent_kind in {"test", "validate"} and (
            requirement.contract_modes
            or requirement.context_kinds
            or requirement.entity_refs
            or requirement.population_subject_refs
        ):
            issues.append(
                ValidationIssue(
                    code="COMPILER_PROCESS_REQUIRED",
                    gate="metric_process_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Metric requirement profile demands a process but none was provided",
                    subject_ref=metric.ref,
                )
            )
        return issues
    metric_header = dict(metric.semantic_object.get("header") or {})
    process_contract = dict(process.semantic_object.get("interface_contract") or {})
    metric_subject_ref = _optional_str(metric_header.get("population_subject_ref"))
    process_subject_ref = _optional_str(process_contract.get("population_subject_ref"))
    if (
        metric_subject_ref is not None
        and process_subject_ref is not None
        and metric_subject_ref != process_subject_ref
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_METRIC_PROCESS_INCOMPATIBLE",
                gate="metric_process_compatibility",
                category="compatibility",
                severity="error",
                message="Metric and process population_subject_ref do not match",
                subject_ref=metric.ref,
                details={
                    "metric_subject_ref": metric_subject_ref,
                    "process_subject_ref": process_subject_ref,
                },
            )
        )
    contract_mode = _optional_str(process_contract.get("contract_mode"))
    context_kind = _optional_str(process_contract.get("context_kind"))
    entity_ref = _optional_str(process_contract.get("entity_ref"))
    if requirement.contract_modes and contract_mode not in requirement.contract_modes:
        issues.append(
            ValidationIssue(
                code="COMPILER_PROFILE_NOT_SATISFIED",
                gate="metric_process_compatibility",
                category="compatibility",
                severity="error",
                message="Process contract_mode does not satisfy metric requirement profile",
                subject_ref=metric.ref,
                details={
                    "required_contract_modes": requirement.contract_modes,
                    "actual_contract_mode": contract_mode,
                },
            )
        )
    if requirement.context_kinds and context_kind not in requirement.context_kinds:
        issues.append(
            ValidationIssue(
                code="COMPILER_PROFILE_NOT_SATISFIED",
                gate="metric_process_compatibility",
                category="compatibility",
                severity="error",
                message="Process context_kind does not satisfy metric requirement profile",
                subject_ref=metric.ref,
                details={
                    "required_context_kinds": requirement.context_kinds,
                    "actual_context_kind": context_kind,
                },
            )
        )
    if requirement.entity_refs and entity_ref not in requirement.entity_refs:
        issues.append(
            ValidationIssue(
                code="COMPILER_PROFILE_NOT_SATISFIED",
                gate="metric_process_compatibility",
                category="compatibility",
                severity="error",
                message="Process entity_ref does not satisfy metric requirement profile",
                subject_ref=metric.ref,
                details={
                    "required_entity_refs": requirement.entity_refs,
                    "actual_entity_ref": entity_ref,
                },
            )
        )
    if (
        requirement.population_subject_refs
        and process_subject_ref not in requirement.population_subject_refs
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_PROFILE_NOT_SATISFIED",
                gate="metric_process_compatibility",
                category="compatibility",
                severity="error",
                message="Process population_subject_ref does not satisfy metric requirement profile",
                subject_ref=metric.ref,
                details={
                    "required_population_subject_refs": requirement.population_subject_refs,
                    "actual_population_subject_ref": process_subject_ref,
                },
            )
        )
    if (
        resolved_inputs.normalized_request.intent_kind in {"test", "validate"}
        and derived_state.process_capabilities is not None
        and derived_state.process_capabilities.inferential_ready is None
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_PROFILE_MISSING",
                gate="metric_process_compatibility",
                category="readiness",
                severity="error",
                message="Inferential intent requires a published process capability profile",
                subject_ref=process.ref,
            )
        )
    return issues


def _gate_binding_compatibility(
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
) -> list[ValidationIssue]:
    _ = step_type
    _ = resolved_inputs
    return []


def _gate_entity_field_resolution(resolved_inputs: ResolvedCompilerInputs) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field_issue in resolved_inputs.field_resolution_issues:
        issues.append(
            ValidationIssue(
                code=field_issue.code,
                gate="entity_field_resolution",
                category="readiness"
                if field_issue.code in {"missing_entity_binding", "missing_entity_field"}
                else "compatibility",
                severity="error",
                message=field_issue.message,
                subject_ref=field_issue.field_ref,
                details={
                    **dict(field_issue.details),
                    "usage_path": field_issue.usage_path,
                },
            )
        )
    return issues


def _gate_field_usage_compatibility(
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for entity_field in resolved_inputs.resolved_entity_fields.values():
        for usage_path in entity_field.usage_paths:
            if usage_path.startswith("metric.") and usage_path.endswith(".input_field_ref"):
                aggregation = _metric_component_aggregation(
                    resolved_inputs,
                    usage_path=usage_path,
                )
                expected = _expected_metric_input_types(aggregation)
                if entity_field.value_type is not None and entity_field.value_type not in expected:
                    issues.append(
                        _field_usage_issue(
                            code="invalid_metric_input_type",
                            field=entity_field,
                            usage_path=usage_path,
                            message="Metric component input field has incompatible value_type",
                            details={
                                "aggregation": aggregation,
                                "actual_field_value_type": entity_field.value_type,
                                "expected_field_value_types": sorted(expected),
                            },
                        )
                    )
            if (
                (usage_path == "time.source_field_ref" or usage_path.endswith(".time_ref"))
                and entity_field.value_type is not None
                and entity_field.value_type not in {"date", "datetime"}
            ):
                issues.append(
                    _field_usage_issue(
                        code="invalid_time_field_type",
                        field=entity_field,
                        usage_path=usage_path,
                        message="Time semantic source field must be date/datetime compatible",
                        details={
                            "actual_field_value_type": entity_field.value_type,
                            "expected_field_value_types": ["date", "datetime"],
                        },
                    )
                )
            if usage_path.endswith(".expression.target_ref"):
                operator = _predicate_operator_for_usage(resolved_inputs, usage_path)
                expected = _expected_predicate_operand_types(operator)
                if (
                    expected
                    and entity_field.value_type is not None
                    and entity_field.value_type not in expected
                ):
                    issues.append(
                        _field_usage_issue(
                            code="invalid_predicate_operand_type",
                            field=entity_field,
                            usage_path=usage_path,
                            message="Predicate operand field has incompatible value_type",
                            details={
                                "operator": operator,
                                "actual_field_value_type": entity_field.value_type,
                                "expected_field_value_types": sorted(expected),
                            },
                        )
                    )
        for requirement in derived_state.metric_requirements.field_profile_requirements:
            required_field_ref = _optional_str(requirement.get("field_ref"))
            if required_field_ref != entity_field.field_ref:
                continue
            required_value_type = _optional_str(requirement.get("required_value_type"))
            if (
                required_value_type is not None
                and entity_field.value_type is not None
                and entity_field.value_type != required_value_type
            ):
                issues.append(
                    _field_usage_issue(
                        code="invalid_metric_input_type",
                        field=entity_field,
                        usage_path="compatibility_profile.field_profile_requirements",
                        message="Field value_type does not satisfy compatibility profile",
                        details={
                            "actual_field_value_type": entity_field.value_type,
                            "required_value_type": required_value_type,
                        },
                    )
                )
            nullable_allowed = requirement.get("nullable_allowed")
            if nullable_allowed is False and entity_field.nullable is True:
                issues.append(
                    _field_usage_issue(
                        code="invalid_metric_input_type",
                        field=entity_field,
                        usage_path="compatibility_profile.field_profile_requirements",
                        message="Nullable field does not satisfy compatibility profile",
                        details={
                            "nullable": entity_field.nullable,
                            "nullable_allowed": nullable_allowed,
                        },
                    )
                )
            required_tags = {str(tag) for tag in requirement.get("required_sensitivity_tags") or []}
            if required_tags and not required_tags.issubset(set(entity_field.sensitivity_tags)):
                issues.append(
                    _field_usage_issue(
                        code="invalid_metric_input_type",
                        field=entity_field,
                        usage_path="compatibility_profile.field_profile_requirements",
                        message="Field sensitivity_tags do not satisfy compatibility profile",
                        details={
                            "required_sensitivity_tags": sorted(required_tags),
                            "actual_sensitivity_tags": sorted(entity_field.sensitivity_tags),
                        },
                    )
                )
    return issues


def _field_usage_issue(
    *,
    code: str,
    field: Any,
    usage_path: str,
    message: str,
    details: dict[str, Any],
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        gate="field_usage_compatibility",
        category="compatibility",
        severity="error",
        message=message,
        subject_ref=field.field_ref,
        details={
            "entity_ref": field.entity_ref,
            "local_field_ref": field.local_field_ref,
            "usage_path": usage_path,
            "nullable": field.nullable,
            "unit": field.unit,
            "enum_hint": field.enum_hint,
            "profile_summary": field.profile_summary,
            "sensitivity_tags": list(field.sensitivity_tags),
            **details,
        },
    )


def _metric_component_aggregation(
    resolved_inputs: ResolvedCompilerInputs,
    *,
    usage_path: str,
) -> str | None:
    metric = resolved_inputs.resolved_metric
    if metric is None:
        return None
    parts = usage_path.split(".")
    if len(parts) < 3:
        return None
    component_name = parts[1]
    payload = dict(metric.semantic_object.get("payload") or {})
    component = payload.get(component_name)
    if not isinstance(component, dict):
        return None
    return _optional_str(component.get("aggregation"))


def _expected_metric_input_types(aggregation: str | None) -> set[str]:
    if aggregation in {"sum", "mean"}:
        return {"integer", "number"}
    if aggregation in {"boolean_any", "boolean_all"}:
        return {"boolean"}
    return {"string", "integer", "number", "boolean", "date", "datetime"}


def _predicate_operator_for_usage(
    resolved_inputs: ResolvedCompilerInputs,
    usage_path: str,
) -> str | None:
    for entity_field in resolved_inputs.resolved_entity_fields.values():
        for details in resolved_inputs.entity_field_usage_details.get(entity_field.field_ref, []):
            if details.get("usage_path") == usage_path:
                return _optional_str(details.get("operator"))
    return None


def _expected_predicate_operand_types(operator: str | None) -> set[str]:
    if operator in {"gt", "gte", "lt", "lte", "between"}:
        return {"integer", "number", "date", "datetime"}
    return set()


def _gate_cross_entity_composition(
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ValidationIssue]:
    _ = semantic_repository
    issues: list[ValidationIssue] = []
    composition = resolved_inputs.entity_composition
    if not composition.is_cross_entity:
        return issues
    metric_ref = resolved_inputs.resolved_metric.ref if resolved_inputs.resolved_metric else None
    required_relationship_refs = list(derived_state.metric_requirements.required_relationship_refs)
    if not required_relationship_refs:
        issues.append(
            ValidationIssue(
                code="missing_entity_relationship",
                gate="cross_entity_composition",
                category="compatibility",
                severity="error",
                message="Cross-entity metric requires an explicit entity relationship",
                subject_ref=metric_ref,
                details={"entity_refs": composition.all_entity_refs},
            )
        )
        return issues
    for relationship_ref in required_relationship_refs:
        relationship = _resolve_relationship_for_validator(semantic_repository, relationship_ref)
        if relationship is None:
            issues.append(
                ValidationIssue(
                    code="missing_entity_relationship",
                    gate="cross_entity_composition",
                    category="compatibility",
                    severity="error",
                    message="Required entity relationship could not be resolved",
                    subject_ref=relationship_ref,
                    details={
                        "relationship_ref": relationship_ref,
                        "entity_refs": composition.all_entity_refs,
                    },
                )
            )
            continue
        _record_resolved_relationship(resolved_inputs, relationship)
        relationship_entities = {
            _optional_str(relationship.semantic_object.get("left_entity_ref")),
            _optional_str(relationship.semantic_object.get("right_entity_ref")),
        }
        relationship_entities.discard(None)
        if not set(composition.all_entity_refs).issubset(relationship_entities):
            issues.append(
                ValidationIssue(
                    code="missing_entity_relationship",
                    gate="cross_entity_composition",
                    category="compatibility",
                    severity="error",
                    message="Required entity relationship does not cover all composed entities",
                    subject_ref=relationship_ref,
                    details={
                        "relationship_ref": relationship_ref,
                        "entity_refs": composition.all_entity_refs,
                        "relationship_entity_refs": sorted(relationship_entities),
                    },
                )
            )
            continue
        issues.extend(
            _relationship_profile_issues(
                relationship,
                derived_state=derived_state,
                metric_ref=metric_ref,
            )
        )
    return issues


def _resolve_relationship_for_validator(
    semantic_repository: SemanticRuntimeRepository | None,
    relationship_ref: str,
) -> Any | None:
    if semantic_repository is None:
        return None
    try:
        return semantic_repository.resolve_relationship_ref(relationship_ref)
    except Exception:
        return None


def _record_resolved_relationship(
    resolved_inputs: ResolvedCompilerInputs,
    relationship: Any,
) -> None:
    semantic_object = dict(relationship.semantic_object)
    resolved_inputs.resolved_relationships[relationship.ref] = ResolvedRelationship(
        relationship_ref=relationship.ref,
        left_entity_ref=str(semantic_object.get("left_entity_ref") or ""),
        right_entity_ref=str(semantic_object.get("right_entity_ref") or ""),
        key_alignment=dict(semantic_object.get("key_alignment") or {}),
        time_alignment=dict(semantic_object.get("time_alignment") or {})
        if isinstance(semantic_object.get("time_alignment"), dict)
        else None,
        cardinality=_optional_str(semantic_object.get("cardinality")),
        grain_compatibility=dict(semantic_object.get("grain_compatibility") or {})
        if isinstance(semantic_object.get("grain_compatibility"), dict)
        else None,
        snapshot_effective_window_alignment=dict(
            semantic_object.get("snapshot_effective_window_alignment") or {}
        )
        if isinstance(semantic_object.get("snapshot_effective_window_alignment"), dict)
        else None,
        revision=getattr(relationship, "revision", None),
    )


def _relationship_profile_issues(
    relationship: Any,
    *,
    derived_state: DerivedCompilerState,
    metric_ref: str | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    semantic_object = dict(relationship.semantic_object)
    grain_compatibility = dict(semantic_object.get("grain_compatibility") or {})
    profile_grain = derived_state.metric_requirements.grain_compatibility or {}
    required_grain_refs = {
        str(ref) for ref in profile_grain.get("required_grain_refs") or [] if str(ref).strip()
    }
    if required_grain_refs:
        relationship_grains = {
            _optional_str(grain_compatibility.get("left_grain_ref")),
            _optional_str(grain_compatibility.get("right_grain_ref")),
        }
        relationship_grains.discard(None)
        if not required_grain_refs.issubset(relationship_grains):
            issues.append(
                ValidationIssue(
                    code="incompatible_grain",
                    gate="cross_entity_composition",
                    category="compatibility",
                    severity="error",
                    message="Relationship grain compatibility does not satisfy metric profile",
                    subject_ref=relationship.ref,
                    details={
                        "metric_ref": metric_ref,
                        "required_grain_refs": sorted(required_grain_refs),
                        "relationship_grain_refs": sorted(relationship_grains),
                        "left_grain_ref": grain_compatibility.get("left_grain_ref"),
                        "right_grain_ref": grain_compatibility.get("right_grain_ref"),
                    },
                )
            )
        elif (
            profile_grain.get("compatibility") == "same_grain"
            and grain_compatibility.get("compatibility") == "same_grain"
            and grain_compatibility.get("left_grain_ref")
            != grain_compatibility.get("right_grain_ref")
        ):
            issues.append(
                ValidationIssue(
                    code="incompatible_grain",
                    gate="cross_entity_composition",
                    category="compatibility",
                    severity="error",
                    message="same_grain relationship declares different left/right grain refs",
                    subject_ref=relationship.ref,
                    details={
                        "metric_ref": metric_ref,
                        "left_grain_ref": grain_compatibility.get("left_grain_ref"),
                        "right_grain_ref": grain_compatibility.get("right_grain_ref"),
                    },
                )
            )
    return issues


def _gate_predicate_contracts(
    resolved_inputs: ResolvedCompilerInputs,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ValidationIssue]:
    if semantic_repository is None:
        return []
    predicate_refs = _collect_predicate_refs(resolved_inputs)
    if not predicate_refs:
        return []
    from marivo.runtime.semantic.compile_step import validate_predicate_contracts

    return validate_predicate_contracts(
        predicate_refs=predicate_refs,
        resolver=semantic_repository,
    )


def _gate_scope_validation(
    resolved_inputs: ResolvedCompilerInputs,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ValidationIssue]:
    if semantic_repository is None:
        return []
    request_scope_ref = resolved_inputs.normalized_request.request_scope_predicate_ref
    if not request_scope_ref:
        return []
    from marivo.runtime.semantic.compile_step import validate_request_scope

    upstream_predicates = [
        ref
        for ref in _collect_predicate_refs(resolved_inputs)
        if ref.required_usage != "request_scope"
    ]
    return validate_request_scope(
        request_scope_ref=request_scope_ref,
        upstream_predicates=upstream_predicates,
        resolver=semantic_repository,
    )


def _gate_predicate_conflict(
    resolved_inputs: ResolvedCompilerInputs,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ValidationIssue]:
    if semantic_repository is None:
        return []
    from marivo.runtime.semantic.compile_step import (
        collect_layered_predicate_refs,
        validate_predicate_conflicts,
    )

    layered_refs = collect_layered_predicate_refs(resolved_inputs)
    if not layered_refs:
        return []
    return validate_predicate_conflicts(
        layered_refs=layered_refs,
        resolver=semantic_repository,
    )


def _collect_predicate_refs(
    resolved_inputs: ResolvedCompilerInputs,
) -> list[PredicateRefWithUsage]:
    """Extract all predicate refs from resolved metric, bindings, and request scope,
    tagged with their usage context."""
    from marivo.api.models.base import PredicateUsage
    from marivo.runtime.semantic.compile_step import PredicateRefWithUsage

    refs: list[PredicateRefWithUsage] = []
    seen: set[tuple[str, str]] = set()

    def _add(ref: str | None, usage: PredicateUsage) -> None:
        if ref and ref.startswith("predicate.") and (ref, usage) not in seen:
            seen.add((ref, usage))
            refs.append(PredicateRefWithUsage(ref=ref, required_usage=usage))

    metric = resolved_inputs.resolved_metric
    if metric is not None:
        header = dict(metric.semantic_object.get("header") or {})
        payload = dict(metric.semantic_object.get("payload") or {})
        # default_predicate_refs lives on the header (service path) or payload
        # (runtime _row_to_typed_metric omits it from header).
        for ref in (
            header.get("default_predicate_refs") or payload.get("default_predicate_refs") or []
        ):
            _add(ref, "metric_qualifier")
        # Component qualifier_refs live under family-specific payload fields.
        _component_fields = (
            "count_target",
            "measure",
            "numerator",
            "denominator",
            "value_component",
            "score_source",
        )
        for field in _component_fields:
            component = payload.get(field)
            if component is not None:
                for ref in component.get("qualifier_refs") or []:
                    _add(ref, "metric_qualifier")

    request_predicate = resolved_inputs.normalized_request.request_scope_predicate_ref
    _add(request_predicate, "request_scope")

    return refs


def _gate_dimension_compatibility(resolved_inputs: ResolvedCompilerInputs) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    requested_dimensions = list(resolved_inputs.normalized_request.request_dimensions)
    resolved_dimensions = {
        dimension.ref: dimension for dimension in resolved_inputs.resolved_dimensions
    }
    metric_dimension_refs = _metric_consumable_dimension_refs(resolved_inputs)
    imported_dimension_refs = {
        bridge.dimension_ref for bridge in resolved_inputs.resolved_imported_dimensions
    }
    unresolved_dimension_refs = {
        str(warning.get("dimension_ref"))
        for warning in resolved_inputs.warnings
        if warning.get("code") == "dimension_ref_unresolved"
    }
    for dimension_ref in requested_dimensions:
        if (
            dimension_ref.startswith("dimension.")
            and dimension_ref not in resolved_dimensions
            and dimension_ref in unresolved_dimension_refs
        ):
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_UNRESOLVED",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Explicit typed dimension ref could not be resolved",
                    subject_ref=dimension_ref,
                )
            )
            continue
        if dimension_ref in resolved_inputs.imported_dimension_conflicts:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_IMPORT_AMBIGUOUS",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Imported dimension bridge is ambiguous for the requested metric",
                    subject_ref=dimension_ref,
                    details={
                        "metric_ref": resolved_inputs.resolved_metric.ref
                        if resolved_inputs.resolved_metric is not None
                        else None,
                        "metric_entity_anchor_ref": resolved_inputs.metric_entity_anchor_ref,
                        "candidates": [
                            {
                                "dimension_ref": bridge.dimension_ref,
                                "source_binding_ref": bridge.source_binding_ref,
                                "source_entity_ref": bridge.source_entity_ref,
                                "import_key": bridge.import_key,
                            }
                            for bridge in resolved_inputs.imported_dimension_conflicts[
                                dimension_ref
                            ]
                        ],
                    },
                )
            )
            continue
        if (
            dimension_ref.startswith("dimension.")
            and dimension_ref not in metric_dimension_refs
            and dimension_ref not in imported_dimension_refs
        ):
            if resolved_inputs.metric_entity_anchor_ref is not None:
                issues.append(
                    ValidationIssue(
                        code="COMPILER_DIMENSION_IMPORT_MISSING",
                        gate="dimension_compatibility",
                        category="compatibility",
                        severity="error",
                        message="Requested dimension requires an imported entity dimension bridge",
                        subject_ref=dimension_ref,
                        details={
                            "metric_ref": resolved_inputs.resolved_metric.ref
                            if resolved_inputs.resolved_metric is not None
                            else None,
                            "metric_entity_anchor_ref": resolved_inputs.metric_entity_anchor_ref,
                            "available_imported_dimension_refs": sorted(imported_dimension_refs),
                        },
                    )
                )
                continue
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_NOT_EXPORTED",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Requested dimension is not exported by the resolved metric",
                    subject_ref=dimension_ref,
                    details={
                        "metric_ref": resolved_inputs.resolved_metric.ref
                        if resolved_inputs.resolved_metric is not None
                        else None,
                        "available_metric_dimension_refs": sorted(metric_dimension_refs),
                    },
                )
            )
            continue
        resolved_dimension = resolved_dimensions.get(dimension_ref)
        if resolved_dimension is None:
            continue
        interface_contract = dict(
            resolved_dimension.semantic_object.get("interface_contract") or {}
        )
        time_requirement = dict(interface_contract.get("time_derived_requirement") or {})
        required_time_anchor_ref = _optional_str(time_requirement.get("required_time_anchor_ref"))
        if required_time_anchor_ref is not None:
            metric_header = (
                dict(resolved_inputs.resolved_metric.semantic_object.get("header") or {})
                if resolved_inputs.resolved_metric is not None
                else {}
            )
            process_contract = (
                dict(
                    resolved_inputs.resolved_process.semantic_object.get("interface_contract") or {}
                )
                if resolved_inputs.resolved_process is not None
                else {}
            )
            available_anchor_refs = {
                _optional_str(metric_header.get("primary_time_ref")),
                _optional_str(process_contract.get("anchor_time_ref")),
                resolved_inputs.resolved_filter_time.ref
                if resolved_inputs.resolved_filter_time
                else None,
            }
            available_anchor_refs.discard(None)
            if required_time_anchor_ref not in available_anchor_refs:
                issues.append(
                    ValidationIssue(
                        code="COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
                        gate="dimension_compatibility",
                        category="compatibility",
                        severity="error",
                        message="Time-derived dimension anchor is incompatible with metric/process time anchors",
                        subject_ref=dimension_ref,
                        details={
                            "required_time_anchor_ref": required_time_anchor_ref,
                            "available_anchor_refs": sorted(available_anchor_refs),
                        },
                    )
                )
    return issues


def _metric_consumable_dimension_refs(resolved_inputs: ResolvedCompilerInputs) -> set[str]:
    metric = resolved_inputs.resolved_metric
    if metric is None:
        return set()
    payload = dict(metric.semantic_object.get("payload") or {})
    raw_dimension_refs = list(payload.get("allowed_dimensions") or payload.get("dimensions") or [])
    normalized: set[str] = set()
    for raw_dimension_ref in raw_dimension_refs:
        normalized_ref = _normalize_metric_dimension_ref(raw_dimension_ref)
        if normalized_ref is not None:
            normalized.add(normalized_ref)
    return normalized


def _normalize_metric_dimension_ref(value: Any) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    if text.startswith("dimension."):
        return text
    if "." in text:
        return None
    return f"dimension.{text}"


def _gate_intent_specific(
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if (
        step_type == "validate"
        and derived_state.metric_capabilities is not None
        and not derived_state.metric_capabilities.supports_validate
    ):
        issues.append(
            ValidationIssue(
                code="COMPILER_INTENT_UNSUPPORTED",
                gate="intent_specific",
                category="compatibility",
                severity="error",
                message="Metric/process combination does not support validate intent",
                subject_ref=resolved_inputs.resolved_metric.ref
                if resolved_inputs.resolved_metric is not None
                else None,
            )
        )
    return issues


def _gate_dimension_additivity_condition(
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
    derived_state: DerivedCompilerState,
) -> list[ValidationIssue]:
    """Gate decompose/attribute dimensions against additive_dimensions when
    capability_condition is 'dimension_must_be_allowed'."""
    issues: list[ValidationIssue] = []
    caps = derived_state.metric_capabilities
    if caps is None:
        return issues
    if caps.capability_condition != "dimension_must_be_allowed":
        return issues
    if step_type not in ("decompose", "attribute"):
        return issues
    if caps.additive_dimensions is None:
        return issues

    # Check resolved canonical dimension refs against additive_dimensions.
    resolved_refs = set(resolved_inputs.resolved_dimension_refs)
    for dim in resolved_refs:
        if dim not in caps.additive_dimensions:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_NOT_ADDITIVE",
                    gate="dimension_additivity",
                    category="compatibility",
                    severity="error",
                    message=(
                        f"Dimension '{dim}' is not in additive_dimensions "
                        f"for this metric (dimension_policy='subset')."
                    ),
                    subject_ref=resolved_inputs.resolved_metric.ref
                    if resolved_inputs.resolved_metric is not None
                    else None,
                )
            )

    # Fail-closed: any requested dimension that wasn't resolved to a canonical
    # ref (e.g. plain names like "platform") cannot be verified against
    # additive_dimensions, so must be rejected.
    all_requested = set(resolved_inputs.normalized_request.request_dimensions)
    unresolved = all_requested - resolved_refs
    for dim in unresolved:
        issues.append(
            ValidationIssue(
                code="COMPILER_DIMENSION_NOT_ADDITIVE",
                gate="dimension_additivity",
                category="compatibility",
                severity="error",
                message=(
                    f"Dimension '{dim}' could not be resolved to a canonical ref "
                    f"and cannot be verified against additive_dimensions "
                    f"for this metric (dimension_policy='subset')."
                ),
                subject_ref=resolved_inputs.resolved_metric.ref
                if resolved_inputs.resolved_metric is not None
                else None,
            )
        )

    return issues


def _gate_lowering_precheck(
    resolved_inputs: ResolvedCompilerInputs,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ValidationIssue]:
    """Gate: validate that predicate atoms can be grounded through binding surfaces."""
    if semantic_repository is None or resolved_inputs.resolved_metric is None:
        return []

    # TODO: normalized_predicate_input is also computed in _measurement_node;
    # consider threading it through derived_state or resolved_inputs to avoid
    # the duplicate build_normalized_predicate_input call.
    from marivo.runtime.semantic.compile_step import (
        build_normalized_predicate_input,
        collect_component_fields,
        collect_layered_predicate_refs,
        run_lowering_precheck,
    )

    layered_refs = collect_layered_predicate_refs(resolved_inputs)
    component_fields = collect_component_fields(resolved_inputs)
    if not layered_refs and not component_fields:
        return []

    normalized_input = build_normalized_predicate_input(
        layered_refs=layered_refs,
        resolver=semantic_repository,
        component_fields=component_fields or None,
    )
    return run_lowering_precheck(
        normalized_predicate_input=normalized_input,
        resolved_bindings=[],
        component_fields=component_fields or [],
        resolved_entity_field_refs=resolved_inputs.resolved_entity_field_refs,
    )


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
