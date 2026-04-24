"""Predicate contract-level validator for the compiler pipeline.

Validates predicate contracts before SQL generation, catching invalid
predicates with structured diagnostics early in the compile flow.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from app.analysis_core.validator import ValidationIssue
from app.api.models.base import PredicateUsage
from app.semantic_runtime.errors import SemanticRuntimeError
from app.semantic_runtime.repository import SemanticRuntimeRepository

if TYPE_CHECKING:
    from app.analysis_core.ir import PredicateFilterLineage
    from app.analysis_core.typed_resolution import ResolvedCompilerInputs
    from app.governance_engine.repository import GovernanceRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PredicateRefWithUsage:
    """A predicate ref paired with the usage context where it was consumed."""

    ref: str
    required_usage: PredicateUsage


@dataclass(slots=True, frozen=True)
class PredicateLayerRef:
    """A predicate ref tagged with its filter layer and optional component field."""

    ref: str
    layer: Literal[
        "governance_policy",
        "carrier_row_filter",
        "metric_default",
        "component_qualifier",
        "request_scope",
    ]
    component_field: str | None = None


@dataclass(slots=True, frozen=True)
class ResolvedAtom:
    """A leaf atom from a resolved predicate, tagged with provenance."""

    target_ref: str
    op: str
    value: Any
    source_ref: str
    source_layer: str
    component_field: str | None = None


# ---------------------------------------------------------------------------
# Task 6.2: Normalized predicate input (compiler-internal, not persisted to artifact)
# ---------------------------------------------------------------------------


class NormalizedPredicateAtom(TypedDict):
    target_ref: str
    op: str
    value: Any
    source_ref: str
    source_layer: str
    component_field: NotRequired[str | None]


class NormalizedComponentPredicateInput(TypedDict):
    component_field: str
    shared_scope_atoms: list[NormalizedPredicateAtom]
    default_atoms: list[NormalizedPredicateAtom]
    qualifier_atoms: list[NormalizedPredicateAtom]
    effective_scope_refs: list[str]
    scope_fingerprint: str


class NormalizedPredicateInput(TypedDict):
    shared_scope_atoms: list[NormalizedPredicateAtom]
    shared_scope_refs: list[str]
    default_atoms: list[NormalizedPredicateAtom]
    default_refs: list[str]
    component_inputs: list[NormalizedComponentPredicateInput]


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
# Task 4.5: Layered predicate extraction for conflict detection
# ---------------------------------------------------------------------------

_COMPONENT_FIELDS = (
    "count_target",
    "measure",
    "numerator",
    "denominator",
    "value_component",
    "score_source",
)


def collect_component_fields(resolved_inputs: ResolvedCompilerInputs) -> list[str]:
    """Return sorted list of component fields present in the resolved metric payload."""
    metric = resolved_inputs.resolved_metric
    if metric is None:
        return []
    payload = dict(metric.semantic_object.get("payload") or {})
    return sorted(field for field in _COMPONENT_FIELDS if payload.get(field) is not None)


def collect_layered_predicate_refs(
    resolved_inputs: ResolvedCompilerInputs,
    governance_repository: GovernanceRepository | None,
) -> list[PredicateLayerRef]:
    """Extract all predicate refs tagged with filter layer and component field.

    Unlike _collect_predicate_refs (which flattens to usage), this preserves
    the distinction between metric_default and component_qualifier layers,
    and records which component_field each qualifier_ref belongs to.
    """
    refs: list[PredicateLayerRef] = []
    seen: set[tuple[str, str, str | None]] = set()

    _layer_type = Literal[
        "governance_policy",
        "carrier_row_filter",
        "metric_default",
        "component_qualifier",
        "request_scope",
    ]

    def _add(ref: str, layer: _layer_type, component_field: str | None = None) -> None:
        if not ref.startswith("predicate."):
            return
        key = (ref, layer, component_field)
        if key not in seen:
            seen.add(key)
            refs.append(PredicateLayerRef(ref=ref, layer=layer, component_field=component_field))

    # Governance policies
    if governance_repository is not None:
        from app.governance_engine.runtime import policy_matches_scope

        step_type = resolved_inputs.normalized_request.intent_kind
        tables: set[str] = set()
        if resolved_inputs.normalized_request.table_name:
            tables.add(resolved_inputs.normalized_request.table_name)
        gov_seen: set[str] = set()
        for policy in governance_repository.list_policies(enabled_only=True):
            if policy.get("policy_type") != "row_filter":
                continue
            if not policy_matches_scope(policy, step_type=step_type, tables=tables or None):
                continue
            definition = policy.get("definition") or {}
            predicate_ref = definition.get("predicate_ref")
            if (
                predicate_ref
                and predicate_ref.startswith("predicate.")
                and predicate_ref not in gov_seen
            ):
                gov_seen.add(predicate_ref)
                _add(predicate_ref, "governance_policy")

    # Binding carrier row_filter_refs
    for binding in resolved_inputs.resolved_bindings:
        interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
        for carrier in interface_contract.get("carrier_bindings") or []:
            for ref in carrier.get("row_filter_refs") or []:
                _add(ref, "carrier_row_filter")

    # Metric default_predicate_refs and component qualifier_refs
    metric = resolved_inputs.resolved_metric
    if metric is not None:
        header = dict(metric.semantic_object.get("header") or {})
        payload = dict(metric.semantic_object.get("payload") or {})
        for ref in (
            header.get("default_predicate_refs") or payload.get("default_predicate_refs") or []
        ):
            _add(ref, "metric_default")
        for field in _COMPONENT_FIELDS:
            component = payload.get(field)
            if component is not None:
                for ref in component.get("qualifier_refs") or []:
                    _add(ref, "component_qualifier", component_field=field)

    # Request scope
    request_predicate = resolved_inputs.normalized_request.request_scope_predicate_ref
    if request_predicate:
        _add(request_predicate, "request_scope")

    return refs


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

    # --- cross-operator: eq vs comparison ops ---
    _cmp_ops = {"gte", "gt", "lte", "lt"}
    _cmp_map: dict[str, str] = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}

    if scope_op == "eq" and upstream_op in _cmp_ops:
        return _compare_values(scope_value, upstream_value, _cmp_map[upstream_op])

    if scope_op in _cmp_ops and upstream_op == "eq":
        # scope gte 18 narrows upstream eq 25 iff 25 >= 18
        return _compare_values(upstream_value, scope_value, _cmp_map[scope_op])

    # --- cross-operator: eq vs between ---
    if scope_op == "eq" and upstream_op == "between":
        if isinstance(upstream_value, list) and len(upstream_value) == 2:
            try:
                return bool(upstream_value[0] <= scope_value <= upstream_value[1])
            except TypeError:
                return None
        return None

    if scope_op == "between" and upstream_op == "eq":
        if isinstance(scope_value, list) and len(scope_value) == 2:
            try:
                return bool(scope_value[0] <= upstream_value <= scope_value[1])
            except TypeError:
                return None
        return None

    # --- cross-operator: in vs comparison ops ---
    if scope_op == "in" and upstream_op in _cmp_ops:
        if isinstance(scope_value, list):
            results = [
                _compare_values(v, upstream_value, _cmp_map[upstream_op]) for v in scope_value
            ]
            if all(r is True for r in results):
                return True
            if all(r is False for r in results):
                return False
        return None

    if scope_op in _cmp_ops and upstream_op == "in":
        if isinstance(upstream_value, list):
            results = [_compare_values(v, scope_value, _cmp_map[scope_op]) for v in upstream_value]
            if all(r is True for r in results):
                return True
            if all(r is False for r in results):
                return False
        return None

    # --- cross-operator: in vs between ---
    if scope_op == "in" and upstream_op == "between":
        if (
            isinstance(scope_value, list)
            and isinstance(upstream_value, list)
            and len(upstream_value) == 2
        ):
            try:
                in_range = [upstream_value[0] <= v <= upstream_value[1] for v in scope_value]
                if all(in_range):
                    return True
                if not any(in_range):
                    return False
            except TypeError:
                pass
        return None

    if scope_op == "between" and upstream_op == "in":
        if (
            isinstance(upstream_value, list)
            and isinstance(scope_value, list)
            and len(scope_value) == 2
        ):
            try:
                in_range = [scope_value[0] <= v <= scope_value[1] for v in upstream_value]
                if all(in_range):
                    return True
                if not any(in_range):
                    return False
            except TypeError:
                pass
        return None

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


# ---------------------------------------------------------------------------
# Task 4.5: Predicate conflict detection
# ---------------------------------------------------------------------------

_CONFLICT_GATE = "predicate_conflict"


def validate_predicate_conflicts(
    *,
    layered_refs: list[PredicateLayerRef],
    resolver: SemanticRuntimeRepository,
) -> list[ValidationIssue]:
    """Detect cross-layer predicate conflicts.

    Runs after gates 6 (predicate_contract) and 7 (scope_validation) so all
    individual predicates are already validated. This gate checks conflicts
    BETWEEN layers, not within individual predicates.
    """
    # Resolve each layer ref and extract atoms
    atoms_by_target: dict[str, list[ResolvedAtom]] = {}
    for entry in layered_refs:
        resolved = _resolve_predicate(entry.ref, resolver)
        if resolved is None:
            continue
        ic = dict(resolved.semantic_object.get("interface_contract") or {})
        expression = ic.get("expression")
        if not expression:
            continue
        for raw_atom in _extract_atoms(expression):
            atom = ResolvedAtom(
                target_ref=raw_atom.get("target_ref", ""),
                op=raw_atom.get("op", ""),
                value=raw_atom.get("value"),
                source_ref=entry.ref,
                source_layer=entry.layer,
                component_field=entry.component_field,
            )
            atoms_by_target.setdefault(atom.target_ref, []).append(atom)

    if not atoms_by_target:
        return []

    issues: list[ValidationIssue] = []
    for _target, atoms in atoms_by_target.items():
        issues.extend(_check_governance_vs_metric_conflict(atoms))
        issues.extend(_check_carrier_vs_qualifier_conflict(atoms))
        issues.extend(_check_within_metric_conflict(atoms))
        issues.extend(_check_cross_component_conflict(atoms))

    return issues


def _atoms_compatible(atom_a: ResolvedAtom, atom_b: ResolvedAtom) -> bool | None:
    """Check if two atoms on the same target_ref are compatible (non-empty intersection).

    Unlike _values_overlap which is directional (scope narrows upstream), this
    checks both directions: if either direction proves narrowing, the atoms
    are compatible.

    Returns:
        True  — compatible (narrowing provable in at least one direction)
        False — contradiction (both directions contradict)
        None  — cannot prove compatibility (fail-closed)
    """
    fwd = _values_overlap(atom_a.op, atom_a.value, atom_b.op, atom_b.value)
    if fwd is True:
        return True
    rev = _values_overlap(atom_b.op, atom_b.value, atom_a.op, atom_a.value)
    if rev is True:
        return True
    if fwd is False and rev is False:
        return False
    return None


def _check_governance_vs_metric_conflict(atoms: list[ResolvedAtom]) -> list[ValidationIssue]:
    """Detect contradictions between governance_policy and metric predicates."""
    gov_atoms = [a for a in atoms if a.source_layer == "governance_policy"]
    metric_atoms = [a for a in atoms if a.source_layer in ("metric_default", "component_qualifier")]
    if not gov_atoms or not metric_atoms:
        return []
    issues: list[ValidationIssue] = []
    for gov in gov_atoms:
        for metric in metric_atoms:
            result = _atoms_compatible(gov, metric)
            if result is True:
                continue
            issues.append(_make_conflict_issue(gov, metric, result, "readiness"))
    return issues


def _check_carrier_vs_qualifier_conflict(atoms: list[ResolvedAtom]) -> list[ValidationIssue]:
    """Detect contradictions between carrier_row_filter and component_qualifier."""
    carrier_atoms = [a for a in atoms if a.source_layer == "carrier_row_filter"]
    qualifier_atoms = [a for a in atoms if a.source_layer == "component_qualifier"]
    if not carrier_atoms or not qualifier_atoms:
        return []
    issues: list[ValidationIssue] = []
    for carrier in carrier_atoms:
        for qualifier in qualifier_atoms:
            result = _atoms_compatible(carrier, qualifier)
            if result is True:
                continue
            issues.append(_make_conflict_issue(carrier, qualifier, result, "compiler"))
    return issues


def _check_within_metric_conflict(atoms: list[ResolvedAtom]) -> list[ValidationIssue]:
    """Detect contradictions between metric_default and component_qualifier on same target."""
    default_atoms = [a for a in atoms if a.source_layer == "metric_default"]
    qualifier_atoms = [a for a in atoms if a.source_layer == "component_qualifier"]
    if not default_atoms or not qualifier_atoms:
        return []
    issues: list[ValidationIssue] = []
    for default in default_atoms:
        for qualifier in qualifier_atoms:
            result = _atoms_compatible(default, qualifier)
            if result is True:
                continue
            issues.append(_make_conflict_issue(default, qualifier, result, "compiler"))
    return issues


def _check_cross_component_conflict(atoms: list[ResolvedAtom]) -> list[ValidationIssue]:
    """Detect contradictions between different components on the same target."""
    qualifier_atoms = [
        a for a in atoms if a.source_layer == "component_qualifier" and a.component_field
    ]
    if len(qualifier_atoms) < 2:
        return []
    # Group by component_field
    by_field: dict[str, list[ResolvedAtom]] = {}
    for atom in qualifier_atoms:
        field = atom.component_field
        assert field is not None  # guaranteed by filter above
        by_field.setdefault(field, []).append(atom)
    if len(by_field) < 2:
        return []
    issues: list[ValidationIssue] = []
    fields = sorted(by_field)
    for i in range(len(fields)):
        for j in range(i + 1, len(fields)):
            for atom_a in by_field[fields[i]]:
                for atom_b in by_field[fields[j]]:
                    result = _atoms_compatible(atom_a, atom_b)
                    if result is True:
                        continue
                    issues.append(_make_conflict_issue(atom_a, atom_b, result, "readiness"))
    return issues


def _make_conflict_issue(
    atom_a: ResolvedAtom,
    atom_b: ResolvedAtom,
    overlap_result: bool | None,
    category: str,
) -> ValidationIssue:
    """Produce a structured ValidationIssue for a detected conflict or unprovable pair."""
    code = _conflict_code(atom_a.source_layer, atom_b.source_layer, overlap_result)
    is_contradiction = overlap_result is False
    message = (
        f"Predicate conflict on '{atom_a.target_ref}': "
        f"{atom_a.source_layer} {atom_a.op} vs {atom_b.source_layer} {atom_b.op}"
        + (" — contradiction" if is_contradiction else " — narrowing unprovable")
    )
    return ValidationIssue(
        code=code,
        gate=_CONFLICT_GATE,
        category=category,
        severity="warning" if overlap_result is None else "error",
        message=message,
        subject_ref=atom_a.target_ref,
        details={
            "target_ref": atom_a.target_ref,
            "atom_a": {
                "ref": atom_a.source_ref,
                "layer": atom_a.source_layer,
                "op": atom_a.op,
                "component_field": atom_a.component_field,
            },
            "atom_b": {
                "ref": atom_b.source_ref,
                "layer": atom_b.source_layer,
                "op": atom_b.op,
                "component_field": atom_b.component_field,
            },
        },
    )


def _conflict_code(layer_a: str, layer_b: str, overlap_result: bool | None) -> str:
    """Determine the error code for a conflict between two layers."""
    layers = frozenset({layer_a, layer_b})
    if layers & {"governance_policy"} and layers & {"metric_default", "component_qualifier"}:
        return "COMPILER_PREDICATE_GOVERNANCE_METRIC_CONFLICT"
    if layers == frozenset({"carrier_row_filter", "component_qualifier"}):
        return "COMPILER_PREDICATE_CARRIER_QUALIFIER_CONFLICT"
    if layers == frozenset({"metric_default", "component_qualifier"}):
        return "COMPILER_PREDICATE_WITHIN_METRIC_CONFLICT"
    if layer_a == "component_qualifier" and layer_b == "component_qualifier":
        return "COMPILER_PREDICATE_CROSS_COMPONENT_CONFLICT"
    return "COMPILER_PREDICATE_CONFLICT_UNPROVABLE"


# ---------------------------------------------------------------------------
# Task 4.6: Resolved predicate lineage builder
# ---------------------------------------------------------------------------


def build_predicate_filter_lineage(
    layered_refs: list[PredicateLayerRef],
    *,
    component_fields: list[str] | None = None,
) -> PredicateFilterLineage:
    """Build the frozen predicate lineage from validated+resolved layer refs.

    When *component_fields* is provided, every listed field gets an entry in
    component_qualifier_lineages / component_effective_scopes — even those
    without qualifier_refs.  This satisfies the contract that an N-component
    metric produces N component_effective_scope values.
    When None (default), only fields with qualifier_refs appear (backward-compat).
    """
    from app.analysis_core.ir import (
        ComponentEffectiveScope,
        ComponentQualifierLineage,
        MetricDefaultLineage,
        SharedEffectiveScope,
    )

    gov_refs: list[str] = []
    carrier_refs: list[str] = []
    request_scope_ref: str | None = None
    default_refs: list[str] = []
    qualifier_by_field: dict[str, list[str]] = {}

    for entry in layered_refs:
        if entry.layer == "governance_policy":
            gov_refs.append(entry.ref)
        elif entry.layer == "carrier_row_filter":
            carrier_refs.append(entry.ref)
        elif entry.layer == "request_scope":
            request_scope_ref = entry.ref
        elif entry.layer == "metric_default":
            default_refs.append(entry.ref)
        elif entry.layer == "component_qualifier" and entry.component_field:
            qualifier_by_field.setdefault(entry.component_field, []).append(entry.ref)

    shared_scope: SharedEffectiveScope = {
        "governance_policy_refs": gov_refs,
        "carrier_row_filter_refs": carrier_refs,
    }
    if request_scope_ref is not None:
        shared_scope["request_scope_ref"] = request_scope_ref

    metric_default: MetricDefaultLineage = {
        "default_predicate_refs": default_refs,
    }

    fields_to_emit = (
        component_fields if component_fields is not None else sorted(qualifier_by_field)
    )

    component_lineages: list[ComponentQualifierLineage] = []
    component_scopes: list[ComponentEffectiveScope] = []
    for field in fields_to_emit:
        q_refs = qualifier_by_field.get(field, [])
        component_lineages.append(
            {
                "component_field": field,
                "qualifier_refs": q_refs,
            }
        )
        effective_refs = gov_refs + carrier_refs + default_refs + q_refs
        if request_scope_ref:
            effective_refs.append(request_scope_ref)
        component_scopes.append(
            {
                "component_field": field,
                "effective_scope_refs": effective_refs,
                "scope_fingerprint": _scope_fingerprint(effective_refs),
            }
        )

    lineage: PredicateFilterLineage = {
        "shared_effective_scope": shared_scope,
        "metric_default_lineage": metric_default,
        "component_qualifier_lineages": component_lineages,
        "component_effective_scopes": component_scopes,
    }
    return lineage


def _scope_fingerprint(refs: list[str]) -> str:
    """Deterministic SHA-256 fingerprint for a list of predicate refs."""
    canonical = ":".join(sorted(refs))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Task 6.2: Build normalized predicate input for lowering consumption
# ---------------------------------------------------------------------------


def build_normalized_predicate_input(
    *,
    layered_refs: list[PredicateLayerRef],
    resolver: SemanticRuntimeRepository,
    component_fields: list[str] | None = None,
) -> NormalizedPredicateInput:
    """Build the normalized predicate input from validated layer refs.

    Resolves each predicate ref into expression atoms, groups by layer
    and component, and composes effective scopes per component.

    Precondition: validation gates (predicate_contract, scope_validation,
    predicate_conflict) must have already passed before calling this.

    The result uses semantic ``target_ref`` values only — physical column
    resolution is the binding surface's responsibility during lowering.
    """
    from app.analysis_core.predicate_lowering_boundary import (
        assert_predicate_uses_no_physical_names,
    )

    shared_atoms: list[NormalizedPredicateAtom] = []
    default_atoms: list[NormalizedPredicateAtom] = []
    qualifier_by_field: dict[str, list[NormalizedPredicateAtom]] = {}
    shared_refs: list[str] = []
    default_refs: list[str] = []

    for entry in layered_refs:
        resolved_obj = _resolve_predicate(entry.ref, resolver)
        if resolved_obj is None:
            logger.warning(
                "Predicate ref %s passed validation but failed resolution in "
                "build_normalized_predicate_input; skipping",
                entry.ref,
            )
            continue
        interface_contract = dict(resolved_obj.semantic_object.get("interface_contract") or {})
        expression = dict(interface_contract.get("expression") or {})
        if not expression:
            continue

        raw_atoms = _extract_atoms(expression)
        for raw in raw_atoms:
            atom: NormalizedPredicateAtom = {
                "target_ref": raw["target_ref"],
                "op": raw["op"],
                "value": raw["value"],
                "source_ref": entry.ref,
                "source_layer": entry.layer,
            }
            if entry.component_field is not None:
                atom["component_field"] = entry.component_field

            if entry.layer in {"governance_policy", "carrier_row_filter", "request_scope"}:
                shared_atoms.append(atom)
            elif entry.layer == "metric_default":
                default_atoms.append(atom)
            elif entry.layer == "component_qualifier" and entry.component_field:
                qualifier_by_field.setdefault(entry.component_field, []).append(atom)

        if entry.layer in {"governance_policy", "carrier_row_filter", "request_scope"}:
            shared_refs.append(entry.ref)
        elif entry.layer == "metric_default":
            default_refs.append(entry.ref)

    # Deduplicate ref lists while preserving order
    shared_refs = _dedupe_preserve_order(shared_refs)
    default_refs = _dedupe_preserve_order(default_refs)

    fields_to_emit = (
        component_fields if component_fields is not None else sorted(qualifier_by_field)
    )

    component_inputs: list[NormalizedComponentPredicateInput] = []
    for field in fields_to_emit:
        q_atoms = qualifier_by_field.get(field, [])
        effective_refs = (
            shared_refs
            + default_refs
            + [
                entry.ref
                for entry in layered_refs
                if entry.layer == "component_qualifier" and entry.component_field == field
            ]
        )
        effective_refs = _dedupe_preserve_order(effective_refs)

        # Each component input carries copies of shared/default atoms so the
        # lowering consumer gets self-contained per-component payloads without
        # needing to cross-reference the top-level lists.
        component_inputs.append(
            {
                "component_field": field,
                "shared_scope_atoms": list(shared_atoms),
                "default_atoms": list(default_atoms),
                "qualifier_atoms": q_atoms,
                "effective_scope_refs": effective_refs,
                "scope_fingerprint": _scope_fingerprint(effective_refs),
            }
        )

    result: NormalizedPredicateInput = {
        "shared_scope_atoms": shared_atoms,
        "shared_scope_refs": shared_refs,
        "default_atoms": default_atoms,
        "default_refs": default_refs,
        "component_inputs": component_inputs,
    }

    assert_predicate_uses_no_physical_names(result, surface="normalized_predicate_input")
    return result


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
