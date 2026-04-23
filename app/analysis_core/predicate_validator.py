"""Predicate contract-level validator for the compiler pipeline.

Validates predicate contracts before SQL generation, catching invalid
predicates with structured diagnostics early in the compile flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.analysis_core.validator import ValidationIssue
from app.api.models.base import PredicateUsage
from app.semantic_runtime.errors import SemanticRuntimeError
from app.semantic_runtime.repository import SemanticRuntimeRepository


@dataclass(slots=True, frozen=True)
class PredicateRefWithUsage:
    """A predicate ref paired with the usage context where it was consumed."""

    ref: str
    required_usage: PredicateUsage


_GATE = "predicate_contract"

_ALLOWED_ATOM_TARGET_PREFIXES = frozenset(
    {
        "dimension.",
        "entity.",
        "key.",
        "enum.",
        "subject.",
        "population.",
        "event.",
        "field.",
    }
)

_FORBIDDEN_ATOM_TARGET_PREFIXES = frozenset(
    {
        "time.",
        "metric.",
        "process.",
        "binding.",
        "predicate.",
        "grain.",
        "measure.",
        "compiler_profile.",
    }
)


def validate_predicate_contracts(
    *,
    predicate_refs: list[PredicateRefWithUsage],
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    """Validate all predicate contracts referenced in a compile flow.

    Resolves each predicate, then checks: ref prefix, subject_ref resolvable,
    target_ref resolvable (where runtime supports), allowed_usage non-empty,
    usage context matches, time_policy=non_time_only, expression deterministic.

    Each (ref, usage_context) pair is validated independently so one failure
    does not suppress diagnostics for the rest. Contract-level checks run once
    per unique ref; usage-level checks run per (ref, required_usage) pair.
    """
    resolved_cache: dict[str, Any | None] = {}
    seen_contract_checks: set[str] = set()
    issues: list[ValidationIssue] = []
    for entry in predicate_refs:
        ref = entry.ref
        required_usage = entry.required_usage

        ref_issues: list[ValidationIssue] = []
        ref_issues.extend(_check_predicate_ref_prefix(ref))
        if ref_issues:
            issues.extend(ref_issues)
            continue

        if ref not in resolved_cache:
            resolved_cache[ref] = _resolve_predicate(ref, resolver)
        resolved = resolved_cache[ref]

        if resolved is None:
            issues.extend(_check_predicate_resolved(ref, resolved=None, resolver=resolver))
            continue
        ref_issues.extend(_check_predicate_resolved(ref, resolved, resolver))
        if any(i.severity == "error" for i in ref_issues):
            issues.extend(ref_issues)
            continue

        if ref not in seen_contract_checks:
            seen_contract_checks.add(ref)
            header = dict(resolved.semantic_object.get("header") or {})
            interface_contract = dict(resolved.semantic_object.get("interface_contract") or {})
            ref_issues.extend(_check_subject_ref_resolvable(header, ref, resolver))
            ref_issues.extend(_check_target_refs_resolvable(interface_contract, ref, resolver))
            ref_issues.extend(_check_allowed_usage_nonempty(interface_contract, ref))
            ref_issues.extend(_check_time_policy(interface_contract, ref))
            ref_issues.extend(_check_expression_deterministic(interface_contract, ref))
        issues.extend(ref_issues)

        interface_contract = dict(resolved.semantic_object.get("interface_contract") or {})
        issues.extend(_check_usage_context_allowed(interface_contract, ref, required_usage))
    return issues


def _resolve_predicate(ref: str, resolver: SemanticRuntimeRepository) -> Any | None:
    """Attempt to resolve a predicate ref via the runtime repository.

    Returns the resolved object or None on any resolution failure.
    """
    try:
        return resolver.resolve_ref(ref)
    except SemanticRuntimeError:
        return None


def _check_predicate_ref_prefix(ref: str) -> list[ValidationIssue]:
    if not ref.startswith("predicate."):
        return [
            ValidationIssue(
                code="COMPILER_PREDICATE_REF_INVALID",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=f"Predicate ref must start with 'predicate.' prefix, got: {ref}",
                subject_ref=ref,
            )
        ]
    return []


def _check_predicate_resolved(
    ref: str,
    resolved: Any | None,
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    if resolved is not None:
        availability = resolver.inspect_ref(ref)
        if availability.is_active and availability.is_ready:
            return []
        return [
            ValidationIssue(
                code="COMPILER_PREDICATE_REF_UNRESOLVED",
                gate=_GATE,
                category="readiness",
                severity="error",
                message=(
                    f"Predicate '{ref}' is not active and ready "
                    f"(lifecycle={availability.lifecycle_status}, "
                    f"readiness={availability.readiness_status})"
                ),
                subject_ref=ref,
            )
        ]
    return [
        ValidationIssue(
            code="COMPILER_PREDICATE_REF_UNRESOLVED",
            gate=_GATE,
            category="readiness",
            severity="error",
            message=f"Predicate '{ref}' could not be resolved",
            subject_ref=ref,
        )
    ]


def _check_subject_ref_resolvable(
    header: dict[str, Any],
    predicate_ref: str,
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    subject_ref = header.get("subject_ref")
    if not subject_ref:
        return []
    entity_ref = _resolve_entity_ref_from_alias(subject_ref)
    try:
        resolver.resolve_ref(entity_ref)
        return []
    except SemanticRuntimeError:
        return [
            ValidationIssue(
                code="COMPILER_PREDICATE_SUBJECT_UNRESOLVED",
                gate=_GATE,
                category="readiness",
                severity="error",
                message=f"Predicate subject_ref '{subject_ref}' could not be resolved",
                subject_ref=predicate_ref,
                details={"subject_ref": subject_ref},
            )
        ]


def _check_target_refs_resolvable(
    interface_contract: dict[str, Any],
    predicate_ref: str,
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    expression = interface_contract.get("expression")
    if not expression:
        return []
    target_refs = _extract_target_refs(expression)
    issues: list[ValidationIssue] = []
    for target_ref in target_refs:
        if any(target_ref.startswith(p) for p in _FORBIDDEN_ATOM_TARGET_PREFIXES):
            issues.append(
                ValidationIssue(
                    code="COMPILER_PREDICATE_TARGET_UNRESOLVED",
                    gate=_GATE,
                    category="compiler",
                    severity="error",
                    message=f"Predicate target_ref '{target_ref}' uses a forbidden prefix",
                    subject_ref=predicate_ref,
                    details={"target_ref": target_ref},
                )
            )
            continue
        if any(target_ref.startswith(p) for p in ("key.", "enum.", "field.")):
            # Prefix-only validation — these were validated at CRUD time
            continue
        entity_ref = _resolve_entity_ref_from_alias(target_ref)
        try:
            resolver.resolve_ref(entity_ref)
        except SemanticRuntimeError:
            issues.append(
                ValidationIssue(
                    code="COMPILER_PREDICATE_TARGET_UNRESOLVED",
                    gate=_GATE,
                    category="readiness",
                    severity="error",
                    message=f"Predicate target_ref '{target_ref}' could not be resolved",
                    subject_ref=predicate_ref,
                    details={"target_ref": target_ref},
                )
            )
    return issues


def _check_allowed_usage_nonempty(
    interface_contract: dict[str, Any],
    predicate_ref: str,
) -> list[ValidationIssue]:
    allowed_usage = interface_contract.get("allowed_usage")
    if allowed_usage is not None and not allowed_usage:
        return [
            ValidationIssue(
                code="COMPILER_PREDICATE_USAGE_EMPTY",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=f"Predicate '{predicate_ref}' has empty allowed_usage",
                subject_ref=predicate_ref,
            )
        ]
    return []


def _check_usage_context_allowed(
    interface_contract: dict[str, Any],
    predicate_ref: str,
    required_usage: str,
) -> list[ValidationIssue]:
    """Check that the predicate's allowed_usage includes the required usage context."""
    allowed_usage = interface_contract.get("allowed_usage")
    if allowed_usage is None:
        return []
    if required_usage in allowed_usage:
        return []
    return [
        ValidationIssue(
            code="COMPILER_PREDICATE_USAGE_MISMATCH",
            gate=_GATE,
            category="compiler",
            severity="error",
            message=(
                f"Predicate '{predicate_ref}' used as {required_usage} "
                f"does not declare '{required_usage}' in allowed_usage "
                f"(has: {allowed_usage})"
            ),
            subject_ref=predicate_ref,
            details={
                "required_usage": required_usage,
                "allowed_usage": list(allowed_usage),
            },
        )
    ]


def _check_time_policy(
    interface_contract: dict[str, Any],
    predicate_ref: str,
) -> list[ValidationIssue]:
    time_policy = interface_contract.get("time_policy")
    if time_policy is not None and time_policy != "non_time_only":
        return [
            ValidationIssue(
                code="COMPILER_PREDICATE_TIME_POLICY_INVALID",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=(
                    f"Predicate '{predicate_ref}' has time_policy '{time_policy}', "
                    f"expected 'non_time_only'"
                ),
                subject_ref=predicate_ref,
                details={"time_policy": time_policy},
            )
        ]
    return []


def _check_expression_deterministic(
    interface_contract: dict[str, Any],
    predicate_ref: str,
) -> list[ValidationIssue]:
    expression = interface_contract.get("expression")
    if not expression:
        return []
    issues: list[ValidationIssue] = []
    _walk_expression(expression, predicate_ref, issues)
    return issues


def _walk_expression(
    node: dict[str, Any],
    predicate_ref: str,
    issues: list[ValidationIssue],
) -> None:
    op = node.get("op")
    if op in ("or", "not"):
        issues.append(
            ValidationIssue(
                code="COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=f"Predicate '{predicate_ref}' uses non-deterministic op '{op}'",
                subject_ref=predicate_ref,
                details={"op": op},
            )
        )
    target_ref = node.get("target_ref")
    if target_ref and target_ref.startswith("time."):
        issues.append(
            ValidationIssue(
                code="COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=f"Predicate '{predicate_ref}' references time-dependent target '{target_ref}'",
                subject_ref=predicate_ref,
                details={"target_ref": target_ref},
            )
        )
    value = node.get("value")
    if _contains_dynamic_value(value):
        issues.append(
            ValidationIssue(
                code="COMPILER_PREDICATE_EXPRESSION_NONDETERMINISTIC",
                gate=_GATE,
                category="compiler",
                severity="error",
                message=f"Predicate '{predicate_ref}' contains dynamic value in expression",
                subject_ref=predicate_ref,
            )
        )
    for item in node.get("items") or []:
        _walk_expression(item, predicate_ref, issues)


_DYNAMIC_VALUE_PATTERN = re.compile(r"(?:now\(\)|current_timestamp\(\)|\$\{)")


def _contains_dynamic_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_DYNAMIC_VALUE_PATTERN.search(value))
    if isinstance(value, list):
        return any(_contains_dynamic_value(item) for item in value)
    return False


def _extract_target_refs(expression: dict[str, Any]) -> list[str]:
    """Recursively extract all target_ref values from a predicate expression tree."""
    refs: list[str] = []
    if expression.get("target_ref") is not None:
        refs.append(expression["target_ref"])
    for item in expression.get("items") or []:
        refs.extend(_extract_target_refs(item))
    return refs


def _resolve_entity_ref_from_alias(ref: str) -> str:
    """Map subject/population/event alias to entity ref."""
    for prefix in ("subject.", "population.", "event."):
        if ref.startswith(prefix):
            return "entity." + ref[len(prefix) :]
    return ref
