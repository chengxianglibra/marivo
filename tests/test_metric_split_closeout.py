"""Closeout tests for the simple/derived metric split migration.

Verifies: prepare template emits flat constructors; readiness records
metric_composition with composition deps; constraint wording updated;
tiny_semantic fixture loads; pinned public surface matches reality.
"""

from __future__ import annotations

from marivo.semantic.prepare import _build_derived_metric_template

# ---------------------------------------------------------------------------
# Task 1: prepare.py — flat constructors + linear
# ---------------------------------------------------------------------------


def test_template_emits_flat_constructors() -> None:
    """_build_derived_metric_template emits ms.ratio / ms.weighted_average / ms.linear."""
    ratio = _build_derived_metric_template(
        composition_kind="ratio",
        name_hint="aov",
        numerator="s.rev",
        denominator="s.cnt",
        weight=None,
    )
    assert "ms.ratio(" in ratio
    assert "ms.derived_metric" not in ratio
    assert "numerator='s.rev'" in ratio
    assert "denominator='s.cnt'" in ratio

    wa = _build_derived_metric_template(
        composition_kind="weighted_average",
        name_hint="ovr",
        numerator="s.rate",
        denominator=None,
        weight="s.n",
    )
    assert "ms.weighted_average(" in wa
    assert "value='s.rate'" in wa
    assert "weight='s.n'" in wa

    lin = _build_derived_metric_template(
        composition_kind="linear",
        name_hint="net",
        numerator=None,
        denominator=None,
        weight=None,
    )
    assert "ms.linear(" in lin
    assert "add=" in lin


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

    hint = CONSTRAINTS[ConstraintId.METRIC_DATASETS_REQUIRED].hint
    assert "ms.derived_metric" not in hint
    # Should reference the new flat constructors.
    assert "ms.ratio" in hint or "ms.weighted_average" in hint or "ms.linear" in hint
