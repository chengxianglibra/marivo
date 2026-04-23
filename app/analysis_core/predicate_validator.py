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


def _extract_atoms(expression: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively extract all leaf atom dicts (those with target_ref) from an expression tree."""
    if expression.get("target_ref") is not None:
        return [expression]
    atoms: list[dict[str, Any]] = []
    for item in expression.get("items") or []:
        atoms.extend(_extract_atoms(item))
    return atoms


# ---------------------------------------------------------------------------
# Task 4.3: Scope expression shape validation
# ---------------------------------------------------------------------------

_SCOPE_GATE = "scope_validation"
_FORBIDDEN_SCOPE_ATOM_OPS = frozenset({"neq", "not_in"})


def _check_scope_expression_shape(
    expression: dict[str, Any],
    scope_ref: str,
) -> list[ValidationIssue]:
    """Check request scope expression is non-time, conjunctive-only, no dynamic values."""
    issues: list[ValidationIssue] = []
    _walk_scope_expression(expression, scope_ref, issues)
    return issues


def _walk_scope_expression(
    node: dict[str, Any],
    scope_ref: str,
    issues: list[ValidationIssue],
) -> None:
    op = node.get("op")
    if op == "or":
        issues.append(
            ValidationIssue(
                code="COMPILER_SCOPE_DISJUNCTIVE",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=f"Request scope predicate '{scope_ref}' uses disjunctive op 'or'",
                subject_ref=scope_ref,
                details={"op": "or"},
            )
        )
    if op == "not":
        issues.append(
            ValidationIssue(
                code="COMPILER_SCOPE_NEGATION",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=f"Request scope predicate '{scope_ref}' uses negation op 'not'",
                subject_ref=scope_ref,
                details={"op": "not"},
            )
        )
    target_ref = node.get("target_ref")
    if target_ref and target_ref.startswith("time."):
        issues.append(
            ValidationIssue(
                code="COMPILER_SCOPE_TIME_CONDITION",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=(
                    f"Request scope predicate '{scope_ref}' references "
                    f"time-dependent target '{target_ref}'"
                ),
                subject_ref=scope_ref,
                details={"target_ref": target_ref},
            )
        )
    if target_ref and op in _FORBIDDEN_SCOPE_ATOM_OPS:
        issues.append(
            ValidationIssue(
                code="COMPILER_SCOPE_FORBIDDEN_OPERATOR",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=(
                    f"Request scope predicate '{scope_ref}' uses "
                    f"forbidden operator '{op}' — neq and not_in are not allowed as scope operators"
                ),
                subject_ref=scope_ref,
                details={"op": op},
            )
        )
    value = node.get("value")
    if _contains_dynamic_value(value):
        issues.append(
            ValidationIssue(
                code="COMPILER_SCOPE_DYNAMIC_VALUE",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=f"Request scope predicate '{scope_ref}' contains dynamic value",
                subject_ref=scope_ref,
            )
        )
    for item in node.get("items") or []:
        _walk_scope_expression(item, scope_ref, issues)


# ---------------------------------------------------------------------------
# Task 4.4: Scope narrowing proof
# ---------------------------------------------------------------------------

_SCOPE_EXCLUDED_UPSTREAM_USAGES = frozenset({"governance_policy", "carrier_row_filter"})


def _check_scope_target_exclusions(
    scope_atoms: list[dict[str, Any]],
    excluded_targets: dict[str, str],
    scope_ref: str,
) -> list[ValidationIssue]:
    """Reject scope atoms that constrain targets governed by excluded upstream usages.

    excluded_targets maps target_ref → upstream usage that governs it.
    """
    issues: list[ValidationIssue] = []
    for atom in scope_atoms:
        target_ref = atom.get("target_ref", "")
        governing_usage = excluded_targets.get(target_ref)
        if governing_usage:
            issues.append(
                ValidationIssue(
                    code="COMPILER_SCOPE_TARGET_EXCLUDED",
                    gate=_SCOPE_GATE,
                    category="compiler",
                    severity="error",
                    message=(
                        f"Request scope predicate '{scope_ref}' constrains target "
                        f"'{target_ref}' which is governed by upstream "
                        f"{governing_usage} — request scope cannot override or narrow this target"
                    ),
                    subject_ref=scope_ref,
                    details={
                        "target_ref": target_ref,
                        "governing_usage": governing_usage,
                    },
                )
            )
    return issues


def _check_scope_narrowing(
    scope_atoms: list[dict[str, Any]],
    upstream_atoms_by_target: dict[str, list[dict[str, Any]]],
    scope_ref: str,
) -> list[ValidationIssue]:
    """Check that scope atoms narrow (do not contradict) upstream atoms."""
    issues: list[ValidationIssue] = []
    for atom in scope_atoms:
        target_ref = atom.get("target_ref", "")
        upstream_atoms = upstream_atoms_by_target.get(target_ref)
        if not upstream_atoms:
            continue
        for upstream_atom in upstream_atoms:
            narrowing_issues = _check_atom_narrowing(atom, upstream_atom, scope_ref, target_ref)
            issues.extend(narrowing_issues)
    return issues


def _check_atom_narrowing(
    scope_atom: dict[str, Any],
    upstream_atom: dict[str, Any],
    scope_ref: str,
    target_ref: str,
) -> list[ValidationIssue]:
    """Check single scope atom against single upstream atom on the same target_ref."""
    scope_op = scope_atom.get("op", "")
    upstream_op = upstream_atom.get("op", "")
    scope_value = scope_atom.get("value")
    upstream_value = upstream_atom.get("value")

    result = _values_overlap(scope_op, scope_value, upstream_op, upstream_value)
    if result is True:
        return []
    if result is False:
        return [
            ValidationIssue(
                code="COMPILER_SCOPE_CONTRADICTS_UPSTREAM",
                gate=_SCOPE_GATE,
                category="compiler",
                severity="error",
                message=(
                    f"Request scope predicate '{scope_ref}' contradicts upstream filter "
                    f"on '{target_ref}': scope {scope_op} vs upstream {upstream_op}"
                ),
                subject_ref=scope_ref,
                details={
                    "target_ref": target_ref,
                    "scope_op": scope_op,
                    "upstream_op": upstream_op,
                },
            )
        ]
    reason = _narrowing_unprovable_reason(scope_op, upstream_op, scope_value, upstream_value)
    return [
        ValidationIssue(
            code="COMPILER_SCOPE_NARROWING_UNPROVABLE",
            gate=_SCOPE_GATE,
            category="compiler",
            severity="error",
            message=(
                f"Request scope predicate '{scope_ref}' hits upstream target "
                f"'{target_ref}' but cannot prove narrowing: "
                f"scope {scope_op} vs upstream {upstream_op} is not a narrowing-safe pair"
            ),
            subject_ref=scope_ref,
            details={
                "target_ref": target_ref,
                "scope_op": scope_op,
                "upstream_op": upstream_op,
                "reason": reason,
            },
        )
    ]


def _narrowing_unprovable_reason(
    scope_op: str,
    upstream_op: str,
    scope_value: Any,
    upstream_value: Any,
) -> str:
    """Classify why narrowing is unprovable for agent-facing diagnostics."""
    if scope_op != upstream_op:
        return "cross_operator"
    if (
        scope_op == "in"
        and isinstance(scope_value, list)
        and isinstance(upstream_value, list)
        and set(scope_value) & set(upstream_value)
    ):
        return "not_subset"
    if (
        scope_op == "between"
        and isinstance(scope_value, list)
        and isinstance(upstream_value, list)
        and len(scope_value) == 2
        and len(upstream_value) == 2
    ):
        try:
            if not (scope_value[0] > upstream_value[1] or scope_value[1] < upstream_value[0]):
                return "not_subset"
        except TypeError:
            pass
    return "unsupported_operator"


def _values_overlap(
    scope_op: str,
    scope_value: Any,
    upstream_op: str,
    upstream_value: Any,
) -> bool | None:
    """Check narrowing between a scope atom and an upstream atom on the same target_ref.

    Implements a narrow decidable subset: only same-operator pairs and one
    cross-operator pair (eq scope vs in upstream) are accepted as narrowing-safe.
    Cross-operator or complex pairs return None (fail-closed).

    Returns:
        True  — narrowing is provable
        False — contradiction detected
        None  — cannot prove narrowing (fail-closed)
    """
    # --- same-operator: null-check ops ---
    if scope_op == "is_null" and upstream_op == "is_null":
        return True
    if scope_op == "is_null" and upstream_op == "is_not_null":
        return False
    if scope_op == "is_not_null" and upstream_op == "is_null":
        return False
    if scope_op == "is_not_null" and upstream_op == "is_not_null":
        return True

    # null-check ops against value ops: cross-operator → reject
    if scope_op in ("is_null", "is_not_null"):
        return None

    # --- same-operator: eq ---
    if scope_op == "eq" and upstream_op == "eq":
        return bool(scope_value == upstream_value)

    # --- only allowed cross-operator pair: eq scope vs in upstream ---
    if scope_op == "eq" and upstream_op == "in":
        return _value_in_set(scope_value, upstream_value)

    # --- same-operator: in (subset semantics) ---
    if scope_op == "in" and upstream_op == "in":
        return _sets_subset(scope_value, upstream_value)

    # --- same-operator: between (range subset semantics) ---
    if scope_op == "between" and upstream_op == "between":
        return _ranges_subset(scope_value, upstream_value)

    # --- same-operator: gte ---
    if scope_op == "gte" and upstream_op == "gte":
        return _compare_values(scope_value, upstream_value, ">=")

    # --- same-operator: gt ---
    if scope_op == "gt" and upstream_op == "gt":
        return _compare_values(scope_value, upstream_value, ">=")

    # --- same-operator: lte ---
    if scope_op == "lte" and upstream_op == "lte":
        return _compare_values(scope_value, upstream_value, "<=")

    # --- same-operator: lt ---
    if scope_op == "lt" and upstream_op == "lt":
        return _compare_values(scope_value, upstream_value, "<=")

    # everything else: cross-operator or unsupported → reject
    return None


def _value_in_set(value: Any, set_value: Any) -> bool | None:
    """Check if value is in the set represented by set_value."""
    if isinstance(set_value, list):
        return value in set_value
    return None


def _sets_subset(scope_set: Any, upstream_set: Any) -> bool | None:
    """Check if scope_set ⊆ upstream_set (narrowing), disjoint (contradiction), or ambiguous."""
    if not isinstance(scope_set, list) or not isinstance(upstream_set, list):
        return None
    scope_s = set(scope_set)
    upstream_s = set(upstream_set)
    if scope_s <= upstream_s:
        return True
    if not scope_s & upstream_s:
        return False
    return None


def _ranges_subset(scope_range: Any, upstream_range: Any) -> bool | None:
    """Check if scope_range ⊆ upstream_range (narrowing), disjoint (contradiction), or ambiguous."""
    if not isinstance(scope_range, list) or len(scope_range) != 2:
        return None
    if not isinstance(upstream_range, list) or len(upstream_range) != 2:
        return None
    try:
        s_lo, s_hi = scope_range[0], scope_range[1]
        u_lo, u_hi = upstream_range[0], upstream_range[1]
        if s_lo >= u_lo and s_hi <= u_hi:
            return True
        if s_lo > u_hi or s_hi < u_lo:
            return False
        return None
    except TypeError:
        return None


def _compare_values(a: Any, b: Any, op: str) -> bool | None:
    """Compare two scalar values with the given operator. Returns None if incomparable."""
    if a is None or b is None:
        return None
    try:
        if op == ">=":
            return bool(a >= b)
        if op == ">":
            return bool(a > b)
        if op == "<=":
            return bool(a <= b)
        if op == "<":
            return bool(a < b)
    except TypeError:
        return None
    return None


# ---------------------------------------------------------------------------
# Scope validation entry point (4.3 + 4.4)
# ---------------------------------------------------------------------------


def validate_request_scope(
    *,
    request_scope_ref: str | None,
    upstream_predicates: list[PredicateRefWithUsage],
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    """Validate request scope predicate: shape + target exclusions + narrowing.

    Returns empty list if no scope predicate is present. Skips validation
    if the scope predicate cannot be resolved (the predicate_contract gate
    already covers that failure mode).
    """
    if request_scope_ref is None:
        return []

    # Resolve scope predicate
    resolved = _resolve_predicate(request_scope_ref, resolver)
    if resolved is None:
        return []

    interface_contract = dict(resolved.semantic_object.get("interface_contract") or {})
    expression = interface_contract.get("expression")
    if not expression:
        return []

    issues: list[ValidationIssue] = []
    scope_atoms = _extract_atoms(expression)

    # Task 4.3: scope expression shape
    issues.extend(_check_scope_expression_shape(expression, request_scope_ref))

    # Task 4.4: collect upstream atoms and check exclusions + narrowing
    upstream_atoms_by_target: dict[str, list[dict[str, Any]]] = {}
    excluded_targets: dict[str, str] = {}
    for entry in upstream_predicates:
        if entry.ref == request_scope_ref:
            continue
        upstream_resolved = _resolve_predicate(entry.ref, resolver)
        if upstream_resolved is None:
            continue
        upstream_ic = dict(upstream_resolved.semantic_object.get("interface_contract") or {})
        upstream_expr = upstream_ic.get("expression")
        if not upstream_expr:
            continue
        usage = entry.required_usage
        for atom in _extract_atoms(upstream_expr):
            target = atom.get("target_ref", "")
            upstream_atoms_by_target.setdefault(target, []).append(atom)
            if usage in _SCOPE_EXCLUDED_UPSTREAM_USAGES and target not in excluded_targets:
                excluded_targets[target] = usage

    # Check that scope doesn't constrain governance/carrier targets
    if excluded_targets:
        issues.extend(
            _check_scope_target_exclusions(scope_atoms, excluded_targets, request_scope_ref)
        )

    # Check narrowing on non-excluded targets
    if upstream_atoms_by_target:
        issues.extend(
            _check_scope_narrowing(scope_atoms, upstream_atoms_by_target, request_scope_ref)
        )

    return issues


def _resolve_entity_ref_from_alias(ref: str) -> str:
    """Map subject/population/event alias to entity ref."""
    for prefix in ("subject.", "population.", "event."):
        if ref.startswith(prefix):
            return "entity." + ref[len(prefix) :]
    return ref
