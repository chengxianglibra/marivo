"""Progressive analysis Help contract tests."""

from __future__ import annotations

from tests.shared_fixtures import rendered_help

CORE_OPERATORS = (
    "observe",
    "compare",
    "attribute",
    "discover",
    "correlate",
    "hypothesis_test",
    "forecast",
)


def test_methods_hub_reaches_core_operators_without_root_inventory() -> None:
    root = rendered_help(owner="analysis")
    text = "\n".join(
        (
            rendered_help("methods", owner="analysis"),
            rendered_help("methods.change", owner="analysis"),
            rendered_help("methods.relationship_testing", owner="analysis"),
        )
    )

    for operator in CORE_OPERATORS:
        assert operator in text, operator
        assert operator not in root

    assert 'marivo.help("analysis.methods")' in root
    assert "recommend" not in root.lower()
    assert "decompose" not in text
