"""Pure semantic validation logic for compiler inputs.

Extracted from ``marivo.analysis_core.validator`` as part of Phase 3c.

This module contains only pure computation:
- ValidationIssue and ValidationResult data classes
- Pure validation gate functions that inspect resolved inputs and derived state
- Validation error message formatting
- Pure helpers for metric component aggregation, dimension refs

Deferred (requires I/O via semantic_repository):
- ``validate_compiler_inputs``: orchestrates all gates including I/O-bound ones
- ``_gate_predicate_contracts``: imports and calls ``validate_predicate_contracts``
- ``_gate_scope_validation``: imports and calls ``validate_request_scope``
- ``_gate_predicate_conflict``: imports and calls ``validate_predicate_conflicts``
- ``_gate_lowering_precheck``: imports and calls ``run_lowering_precheck``
- ``_collect_predicate_refs``: pure data extraction but only used by I/O gates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Data classes ─────────────────────────────────────────────────────────


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


def validation_error_message(result: ValidationResult) -> str:
    """Format a human-readable error message from a ValidationResult."""
    error = result.primary_error_issue()
    return f"{error.code}: {error.message}"


# ── Pure validation gates ───────────────────────────────────────────────


def gate_profile_integrity(derived_state: Any) -> list[ValidationIssue]:
    """Check profile integrity from derived state.

    *derived_state* must have ``.profile_validation_issues`` (iterable of
    objects with ``.code``, ``.message``, ``.subject_ref``, ``.details``).
    """
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


def gate_request_shape(step_type: str, resolved_inputs: Any) -> list[ValidationIssue]:
    """Check request shape validity.

    *resolved_inputs* must have ``.normalized_request`` (with ``.metric_ref``,
    ``.request_time_scope``), ``.resolved_filter_time`` (with truthiness),
    ``.resolved_metric``, ``.resolved_process``.

    When the request explicitly specifies a time axis field via
    ``time_scope.field`` (stored as ``request_options.resolved_time_axis.override_analysis_time_column``),
    the COMPILER_TIME_REF_UNRESOLVED gate is bypassed because the time axis is
    determined at request level.
    """
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
        and not _request_has_explicit_time_field(normalized)
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


def gate_intent_support(
    step_type: str,
    resolved_inputs: Any,
    derived_state: Any,
) -> list[ValidationIssue]:
    """Check intent support from capabilities.

    *derived_state* must have ``.metric_capabilities`` (with ``.supports_compare``).
    *resolved_inputs* must have ``.resolved_metric`` (with ``.ref``) and
    ``.normalized_request`` (with ``.request_time_scope``).
    """
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


def gate_metric_process_compatibility(
    resolved_inputs: Any,
    derived_state: Any,
) -> list[ValidationIssue]:
    """Check metric-process compatibility.

    *resolved_inputs* must have ``.resolved_metric`` (with ``.ref``, ``.semantic_object``),
    ``.resolved_process`` (with ``.ref``, ``.semantic_object``),
    ``.normalized_request`` (with ``.intent_kind``).
    *derived_state* must have ``.metric_requirements`` (with ``.contract_modes``,
    ``.context_kinds``, ``.entity_refs``, ``.population_subject_refs``),
    ``.process_capabilities`` (with ``.inferential_ready``).
    """
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


def gate_binding_compatibility(
    step_type: str,
    resolved_inputs: Any,
) -> list[ValidationIssue]:
    """Placeholder gate for binding compatibility -- always passes."""
    _ = step_type
    _ = resolved_inputs
    return []


def gate_dimension_compatibility(resolved_inputs: Any) -> list[ValidationIssue]:
    """Check dimension compatibility.

    *resolved_inputs* must have ``.normalized_request`` (with ``.request_dimensions``),
    ``.resolved_dimensions`` (list with ``.ref``, ``.semantic_object``),
    ``.resolved_metric`` (with ``.ref``, ``.semantic_object``),
    ``.resolved_process`` (with ``.semantic_object``),
    ``.resolved_filter_time`` (with ``.ref``),
    ``.warnings``.
    """
    issues: list[ValidationIssue] = []
    requested_dimensions = list(resolved_inputs.normalized_request.request_dimensions)
    resolved_dimensions = {
        dimension.ref: dimension for dimension in resolved_inputs.resolved_dimensions
    }
    metric_dimension_refs = _metric_consumable_dimension_refs(resolved_inputs)
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
                )
            )
            continue
        if dimension_ref.startswith("dimension.") and dimension_ref not in metric_dimension_refs:
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
            process_contract = (
                dict(
                    resolved_inputs.resolved_process.semantic_object.get("interface_contract") or {}
                )
                if resolved_inputs.resolved_process is not None
                else {}
            )
            available_anchor_refs = {
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


def gate_intent_specific(
    step_type: str,
    resolved_inputs: Any,
    derived_state: Any,
) -> list[ValidationIssue]:
    """Check intent-specific compatibility.

    *derived_state* must have ``.metric_capabilities`` (with ``.supports_validate``).
    *resolved_inputs* must have ``.resolved_metric`` (with ``.ref``).
    """
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


def gate_dimension_additivity_condition(
    step_type: str,
    resolved_inputs: Any,
    derived_state: Any,
) -> list[ValidationIssue]:
    """Gate decompose/attribute dimensions against additive_dimensions.

    *derived_state* must have ``.metric_capabilities`` (with ``.additive_dimensions``).
    *resolved_inputs* must have ``.resolved_dimension_refs``, ``.normalized_request``
    (with ``.request_dimensions``), ``.resolved_metric`` (with ``.ref``).
    """
    issues: list[ValidationIssue] = []
    caps = derived_state.metric_capabilities
    if caps is None:
        return issues
    if step_type not in ("decompose", "attribute"):
        return issues
    if len(caps.additive_dimensions) == 0:
        return issues

    resolved_refs = set(resolved_inputs.resolved_dimension_refs)
    for dim in resolved_refs:
        if dim not in caps.additive_dimensions:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_NOT_ADDITIVE",
                    gate="dimension_additivity",
                    category="compatibility",
                    severity="error",
                    message=(f"Dimension '{dim}' is not in additive_dimensions for this metric."),
                    subject_ref=resolved_inputs.resolved_metric.ref
                    if resolved_inputs.resolved_metric is not None
                    else None,
                )
            )

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
                    f"for this metric."
                ),
                subject_ref=resolved_inputs.resolved_metric.ref
                if resolved_inputs.resolved_metric is not None
                else None,
            )
        )

    return issues


# ── Pure helpers ────────────────────────────────────────────────────────


def _metric_consumable_dimension_refs(resolved_inputs: Any) -> set[str]:
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


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _request_has_explicit_time_field(normalized: Any) -> bool:
    """Check whether the request explicitly specifies a time axis field via time_scope.field."""
    options = getattr(normalized, "request_options", None) or {}
    time_axis = options.get("resolved_time_axis") or {}
    override = time_axis.get("override_analysis_time_column")
    return bool(override and str(override).strip())
