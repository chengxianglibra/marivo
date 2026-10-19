"""Check the public calls in the current disclosure examples against live Help."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo._help.render import render_help_text


@pytest.mark.parametrize(
    ("filename", "receivers"),
    (
        (
            "docs/specs/analysis/lazy-analysis-dsl-examples.md",
            {
                "parents": mv.Population,
                "attributes": mv.AnalysisFrame,
                "row_summary": mv.AnalysisFrame,
                "variance": mv.AnalysisFrame,
                "change.artifact": mv.Artifact,
            },
        ),
        (
            "docs/specs/analysis/lazy-analysis-dsl-scenario-validation.md",
            {
                "focus": mv.Population,
                "detail": mv.AnalysisFrame,
                "rolling": mv.AnalysisFrame,
                "saved.artifact": mv.Artifact,
            },
        ),
        ("docs/specs/semantic/datasource-layer.md", {"snapshot": md.DiscoverySnapshot}),
        ("docs/api/datasource.rst", {"snapshot": md.DiscoverySnapshot}),
    ),
)
def test_documented_disclosure_calls_resolve_to_live_members(
    filename: str, receivers: dict[str, type]
) -> None:
    source = (Path(__file__).resolve().parents[2] / filename).read_text()
    for receiver, owner in receivers.items():
        # These named receivers belong to the examples above. Check their display
        # calls, including a possible chained helper, without executing a source.
        calls = re.findall(rf"\b{re.escape(receiver)}((?:\.\w+\(\))*\.show\([^)]*\))", source)
        assert calls, (filename, receiver)
        for suffix in calls:
            expression = ast.parse(receiver + suffix, mode="eval").body
            assert isinstance(expression, ast.Call)
            while isinstance(expression.func, ast.Attribute) and isinstance(
                expression.func.value, ast.Call
            ):
                expression = expression.func.value
            assert isinstance(expression.func, ast.Attribute)
            member = getattr(owner, expression.func.attr)
            text, _, _ = render_help_text(member)
            assert "Kind: callable" in text
