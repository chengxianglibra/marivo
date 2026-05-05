"""Predicate filter lineage reuse for compare-like intents.

Validates and summarizes predicate lineage compatibility between two
upstream observation artifacts, following the same pattern as
``calendar_alignment_metadata.resolve_calendar_alignment_reuse_for_intent``.

The reuse summary is a refs-only provenance record: it carries predicate
refs and scope fingerprints, never expression trees or lowering details.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NotRequired, TypedDict

from app.evidence_engine.ref_boundary import RefBoundaryError, RefBoundaryViolation

# ---------------------------------------------------------------------------
# Issue policy table
# ---------------------------------------------------------------------------

_VALID_LINEAGE_KEYS: frozenset[str] = frozenset(
    {
        "shared_effective_scope",
        "metric_default_lineage",
        "component_qualifier_lineages",
        "component_effective_scopes",
    }
)

_SHARED_SCOPE_KEYS: frozenset[str] = frozenset(
    {
        "carrier_row_filter_refs",
        "request_scope_ref",
    }
)

_FORBIDDEN_EXECUTION_KEYS: frozenset[str] = frozenset(
    {
        "expression",
        "sql",
        "lowering_template",
        "physical_column",
    }
)


class _PredicateLineageIssuePolicy(TypedDict):
    gate_family: str
    severity: str
    blocking: bool
    message_template: str
    next_action_template: NotRequired[str]


_PREDICATE_LINEAGE_ISSUE_POLICIES: dict[str, _PredicateLineageIssuePolicy] = {
    "predicate_lineage_metadata_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": (
            "predicate filter lineage is missing on one observation while the other "
            "side freezes a lineage"
        ),
        "next_action_template": (
            "Re-run the missing side with a predicate-aware observe flow so both "
            "observations freeze compatible lineage metadata."
        ),
    },
    "metric_default_predicate_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": (
            "left and right observations freeze different metric default predicates, "
            "so the observations are not directly comparable"
        ),
        "next_action_template": (
            "Ensure both observations use the same metric (which defines the default predicates)."
        ),
    },
    "component_structure_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": (
            "left and right observations have different component structures, "
            "so their predicate lineages cannot be aligned"
        ),
        "next_action_template": (
            "Ensure both observations are for the same metric with the same component layout."
        ),
    },
    "scope_divergence": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": (
            "left and right observations have different shared effective scopes; "
            "this is expected for comparisons but should be noted when interpreting results"
        ),
        "next_action_template": (
            "Verify that the scope divergence is intentional "
            "(e.g., different request_scope or carrier)."
        ),
    },
    "component_scope_fingerprint_divergence": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": (
            "one or more components have different scope fingerprints between left and "
            "right observations, indicating different effective filtering per component"
        ),
        "next_action_template": (
            "Review whether the component-level filtering differences are expected "
            "for this comparison."
        ),
    },
}

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_predicate_filter_lineage(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    """Normalize and validate a ``predicate_filter_lineage`` dict from an artifact."""
    if not isinstance(value, dict):
        raise error_factory()
    unknown = set(value) - _VALID_LINEAGE_KEYS
    if unknown:
        raise error_factory()

    # Validate nested structures so downstream .get() calls never raise
    # AttributeError on malformed artifacts.
    metric_default = value.get("metric_default_lineage")
    if metric_default is not None:
        if not isinstance(metric_default, dict):
            raise error_factory()
        refs = metric_default.get("default_predicate_refs")
        if refs is not None and not isinstance(refs, list):
            raise error_factory()

    shared_scope = value.get("shared_effective_scope")
    if shared_scope is not None and not isinstance(shared_scope, dict):
        raise error_factory()

    comp_qual = value.get("component_qualifier_lineages")
    if comp_qual is not None and not (
        isinstance(comp_qual, list) and all(isinstance(e, dict) for e in comp_qual)
    ):
        raise error_factory()

    comp_scopes = value.get("component_effective_scopes")
    if comp_scopes is not None and not (
        isinstance(comp_scopes, list) and all(isinstance(e, dict) for e in comp_scopes)
    ):
        raise error_factory()

    return dict(value)


def _normalize_optional_lineage(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any] | None:
    if value is None:
        return None
    return normalize_predicate_filter_lineage(value, error_factory=error_factory)


# ---------------------------------------------------------------------------
# Core reuse resolution
# ---------------------------------------------------------------------------


def resolve_predicate_lineage_reuse(
    *,
    left_predicate_filter_lineage: Any,
    right_predicate_filter_lineage: Any,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    """Validate and summarize predicate lineage compatibility between two observations.

    Returns ``{issues, fatal_message, reuse_summary}``.
    """
    left = _normalize_optional_lineage(left_predicate_filter_lineage, error_factory=error_factory)
    right = _normalize_optional_lineage(right_predicate_filter_lineage, error_factory=error_factory)

    # Both absent: pre-predicate observations — no issues, no summary.
    if left is None and right is None:
        return {"issues": [], "fatal_message": None, "reuse_summary": None}

    # One absent, one present: mismatch.
    if left is None or right is None:
        issue = _build_issue("predicate_lineage_metadata_mismatch")
        return {"issues": [issue], "fatal_message": issue["message"], "reuse_summary": None}

    # Both present: validate compatibility.
    issues: list[dict[str, Any]] = []
    fatal_message: str | None = None

    # metric_default_lineage.default_predicate_refs must match.
    left_defaults = sorted(
        (left.get("metric_default_lineage") or {}).get("default_predicate_refs") or []
    )
    right_defaults = sorted(
        (right.get("metric_default_lineage") or {}).get("default_predicate_refs") or []
    )
    if left_defaults != right_defaults:
        issue = _build_issue(
            "metric_default_predicate_mismatch",
            details={
                "left_default_predicate_refs": left_defaults,
                "right_default_predicate_refs": right_defaults,
            },
        )
        issues.append(issue)
        if fatal_message is None:
            fatal_message = issue["message"]

    # component_qualifier_lineages component_fields must match.
    left_fields = sorted(
        e.get("component_field", "") for e in (left.get("component_qualifier_lineages") or [])
    )
    right_fields = sorted(
        e.get("component_field", "") for e in (right.get("component_qualifier_lineages") or [])
    )
    if left_fields != right_fields:
        issue = _build_issue(
            "component_structure_mismatch",
            details={
                "left_component_fields": left_fields,
                "right_component_fields": right_fields,
            },
        )
        issues.append(issue)
        if fatal_message is None:
            fatal_message = issue["message"]

    # shared_effective_scope divergence: warning only (expected for compare).
    left_shared = _summarize_shared_scope(left.get("shared_effective_scope"))
    right_shared = _summarize_shared_scope(right.get("shared_effective_scope"))
    if left_shared != right_shared:
        issues.append(
            _build_issue(
                "scope_divergence",
                details={
                    "left_shared_effective_scope": left_shared,
                    "right_shared_effective_scope": right_shared,
                },
            )
        )

    # component_effective_scopes fingerprint divergence: warning only.
    left_fps = _fingerprint_map(left.get("component_effective_scopes") or [])
    right_fps = _fingerprint_map(right.get("component_effective_scopes") or [])
    divergent_components = sorted(
        k for k in left_fps if k in right_fps and left_fps[k] != right_fps[k]
    )
    if divergent_components:
        issues.append(
            _build_issue(
                "component_scope_fingerprint_divergence",
                details={
                    "divergent_components": divergent_components,
                    "left_scope_fingerprints": {k: left_fps[k] for k in divergent_components},
                    "right_scope_fingerprints": {k: right_fps[k] for k in divergent_components},
                },
            )
        )

    reuse_summary: dict[str, Any] | None = {
        "reuse_source": "observation_predicate_filter_lineage",
        "metric_default_predicate_refs": left_defaults,
        "component_fields": left_fields,
        "left_shared_effective_scope": left_shared,
        "right_shared_effective_scope": right_shared,
        "left_scope_fingerprints": left_fps,
        "right_scope_fingerprints": right_fps,
    }
    if fatal_message is not None:
        reuse_summary = None

    return {"issues": issues, "fatal_message": fatal_message, "reuse_summary": reuse_summary}


def resolve_predicate_lineage_reuse_for_intent(
    *,
    intent_name: str,
    left_predicate_filter_lineage: Any,
    right_predicate_filter_lineage: Any,
) -> dict[str, Any]:
    """Convenience wrapper providing a default ``error_factory``."""
    return resolve_predicate_lineage_reuse(
        left_predicate_filter_lineage=left_predicate_filter_lineage,
        right_predicate_filter_lineage=right_predicate_filter_lineage,
        error_factory=lambda: ValueError(
            f"{intent_name}: INVALID_ARGUMENT - malformed predicate filter lineage"
        ),
    )


# ---------------------------------------------------------------------------
# Boundary assertion (task 5.6)
# ---------------------------------------------------------------------------


def assert_predicate_lineage_refs_only(
    lineage: dict[str, Any],
    *,
    surface: str,
) -> None:
    """Assert that a predicate lineage dict does not contain expression trees.

    Recursively walks the lineage structure and rejects forbidden
    execution-layer keys (``expression``, ``sql``, ``lowering_template``,
    ``physical_column``).
    """
    violations = list(_find_forbidden_keys(lineage, path=surface))
    if violations:
        raise RefBoundaryError(surface, violations)


def _find_forbidden_keys(
    value: Any,
    *,
    path: str,
) -> list[RefBoundaryViolation]:
    violations: list[RefBoundaryViolation] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in _FORBIDDEN_EXECUTION_KEYS:
                violations.append(
                    RefBoundaryViolation(
                        path=child_path,
                        reason="contains forbidden execution-layer key",
                        value=str(key),
                    )
                )
            violations.extend(_find_forbidden_keys(child, path=child_path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            violations.extend(_find_forbidden_keys(item, path=f"{path}[{i}]"))
    return violations


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_issue(
    code: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = _PREDICATE_LINEAGE_ISSUE_POLICIES.get(code)
    if policy is None:
        policy = {
            "gate_family": "comparability_gate",
            "severity": "warning",
            "blocking": False,
            "message_template": (f"upstream observation froze predicate lineage warning '{code}'"),
        }
    issue: dict[str, Any] = {
        "code": code,
        "severity": policy["severity"],
        "message": message or _render_issue_message(policy),
        "gate_family": policy["gate_family"],
        "blocking": policy["blocking"],
    }
    if details is not None:
        issue["details"] = details
    return issue


def _render_issue_message(policy: _PredicateLineageIssuePolicy) -> str:
    next_action = policy.get("next_action_template")
    if not next_action:
        return policy["message_template"]
    return f"{policy['message_template']}. {next_action}"


def _summarize_shared_scope(shared: dict[str, Any] | None) -> dict[str, Any]:
    """Extract refs-only summary from a shared_effective_scope dict."""
    if shared is None:
        return {}
    return {k: shared.get(k) for k in _SHARED_SCOPE_KEYS if k in shared}


def _fingerprint_map(
    component_scopes: list[dict[str, Any]],
) -> dict[str, str]:
    """Map component_field -> scope_fingerprint."""
    result: dict[str, str] = {}
    for entry in component_scopes:
        field = entry.get("component_field")
        fp = entry.get("scope_fingerprint")
        if isinstance(field, str) and isinstance(fp, str):
            result[field] = fp
    return result
