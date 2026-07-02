"""Closeout tests for the simple/derived metric split migration.

Verifies: constraint wording updated; tiny_semantic fixture loads; pinned
public surface matches reality.
"""

from __future__ import annotations

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
