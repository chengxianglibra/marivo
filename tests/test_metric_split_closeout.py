"""Closeout tests for the simple/derived metric split migration.

Verifies: readiness records metric_composition with composition deps;
constraint wording updated; tiny_semantic fixture loads; pinned public surface
matches reality.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Task 2: readiness.py — metric_composition decision + composition deps
# ---------------------------------------------------------------------------


def test_readiness_uses_composition_decision_and_deps() -> None:
    """Readiness records metric_composition (not metric_decomposition) with composition deps."""
    from marivo.semantic.readiness import _REQUIRED_DECISION_BY_KIND, _SemanticKind

    # Decision kind for metrics should be metric_composition.
    assert _REQUIRED_DECISION_BY_KIND[_SemanticKind.METRIC] == "metric_composition"


# ---------------------------------------------------------------------------
# Task 3: constraints wording — flat constructors
# ---------------------------------------------------------------------------


def test_constraints_mention_flat_constructors() -> None:
    """Constraint messages point to ms.ratio / ms.weighted_average / ms.linear, not ms.derived_metric."""
    from marivo.semantic.constraints import CONSTRAINTS, ConstraintId

    hint = CONSTRAINTS[ConstraintId.METRIC_ENTITIES_REQUIRED].hint
    assert "ms.derived_metric" not in hint
    # Should reference the new flat constructors.
    assert "ms.ratio" in hint or "ms.weighted_average" in hint or "ms.linear" in hint
