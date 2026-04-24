"""Predicate-to-binding-surface lowering responsibility boundary.

This module defines and enforces the three-part responsibility model for
how predicate filter semantics reach physical execution:

1. **predicate_semantic** — contributes ``target_ref`` (semantic ref),
   ``op``, and ``value``.  Must NOT contain physical column names, SQL,
   or engine-specific syntax.

2. **binding_grounding** — contributes ``surface_ref -> physical_name``
   mapping via ``CarrierBinding.field_surfaces``.  Must NOT contain
   filter semantics or operator logic.

3. **lowering_bridge** — composes the other two by resolving
   ``target_ref -> BindingTarget -> surface_ref -> physical_name``.
   Neither filter semantics nor physical grounding live here; it is the
   composition step.

This boundary ensures predicates remain engine-agnostic and reusable
across different binding surfaces, while bindings remain filter-agnostic
and reusable across different predicates.
"""

from __future__ import annotations

from typing import Any, Literal

from app.evidence_engine.ref_boundary import RefBoundaryError, RefBoundaryViolation

PredicateLoweringRole = Literal[
    "predicate_semantic",
    "binding_grounding",
    "lowering_bridge",
]

_FORBIDDEN_PHYSICAL_KEYS = frozenset(
    {
        "physical_name",
        "physical_column",
        "column_name",
        "sql_expression",
        "lowering_template",
        "sql",
    }
)


def assert_predicate_uses_no_physical_names(
    data: Any,
    *,
    surface: str,
) -> None:
    """Assert that a predicate data structure contains no physical column names.

    Recursively walks *data* and raises :class:`RefBoundaryError` if any
    key from :data:`_FORBIDDEN_PHYSICAL_KEYS` is found.  This enforces the
    boundary that predicate contributions use semantic ``target_ref`` values
    only — physical resolution is the binding surface's responsibility.

    This is distinct from :func:`assert_predicate_lineage_refs_only`
    (which guards the artifact/read surface against expression trees).
    The normalized predicate input legitimately carries expression atoms
    (``op``, ``value``), but must never carry physical column references.
    """
    violations = list(_find_physical_keys(data, path=surface))
    if violations:
        raise RefBoundaryError(surface, violations)


def _find_physical_keys(
    value: Any,
    *,
    path: str,
) -> list[RefBoundaryViolation]:
    violations: list[RefBoundaryViolation] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in _FORBIDDEN_PHYSICAL_KEYS:
                violations.append(
                    RefBoundaryViolation(
                        path=child_path,
                        reason="contains forbidden physical-layer key",
                        value=str(key),
                    )
                )
            violations.extend(_find_physical_keys(child, path=child_path))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            violations.extend(_find_physical_keys(item, path=f"{path}[{i}]"))
    return violations
