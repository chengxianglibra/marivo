"""Phase 3 analysis help contract tests."""

from __future__ import annotations

import marivo.analysis as mv

CORE_OPERATORS = (
    "observe",
    "compare",
    "attribute",
    "discover.<objective>",
    "correlate",
    "hypothesis_test",
    "forecast",
    "derive_metric_frame",
    "assess_quality",
)


def test_workflow_help_topic_teaches_phase3_default_surface() -> None:
    text = mv.help_text("workflow")

    assert "contract().affordances" in text
    assert "mechanical compatibility" in text
    assert "assess_quality" in text
    assert "derive_metric_frame" in text
    assert "session.frame_summaries()" in text
    assert "session.get_frame(" in text
    for operator in CORE_OPERATORS:
        assert operator in text
    assert "recommend" not in text.lower()
    assert "decompose" not in text
