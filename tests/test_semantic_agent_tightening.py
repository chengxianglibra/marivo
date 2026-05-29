"""Regression tests for semantic agent authoring guidance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def test_semantic_skill_points_to_standard_metadata_api() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/authoring-workflow.md")
    evidence = _read("marivo-skills/marivo-semantic/references/evidence.md")

    assert "mv.datasources.inspect_table(...)" in skill
    assert "mv.datasources.inspect_table(" in workflow
    assert "Table metadata evidence" in evidence
    assert "table.schema()` returns types but not comments" in skill
    assert "target preview APIs until they exist" not in skill


def test_design_spec_marks_remaining_phases_implemented() -> None:
    spec = _read("docs/specs/semantic/agent-semantic-layer-authoring-design.md")

    assert "| Table metadata/comments | `mv.datasources.inspect_table(...)` | same |" in spec
    assert "### Phase 4: Metadata API\n\nImplemented:" in spec
    assert "### Phase 5: Agent Automation Tightening\n\nImplemented:" in spec


def test_semantic_skill_examples_cover_phase5_cases() -> None:
    examples_dir = REPO_ROOT / "marivo-skills" / "marivo-semantic" / "references" / "examples"
    expected = {
        "05_inspect_table_metadata.py",
        "06_readiness_requires_preview.py",
        "07_readiness_unverified_metric.py",
        "08_readiness_parity_drift.py",
        "09_ambiguous_time_axis_prompt.py",
    }

    assert expected.issubset({path.name for path in examples_dir.glob("*.py")})


def test_semantic_skill_examples_execute() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_skill_examples.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
