"""Phase 3 analysis help contract tests.

The old ``workflow`` help topic has been replaced by the capability-registry
renderer.  The root help now teaches the default surface directly; see
``tests/test_analysis_help.py`` for the current invariants.
"""

from __future__ import annotations

import marivo.analysis as mv

CORE_OPERATORS = (
    "observe",
    "compare",
    "attribute",
    "discover",
    "correlate",
    "hypothesis_test",
    "forecast",
    "assess_quality",
)


def test_root_help_teaches_core_operators() -> None:
    text = mv.help_text()

    for operator in CORE_OPERATORS:
        assert operator in text, operator

    assert "recommend" not in text.lower()
    assert "decompose" not in text
