"""Package-shape tests for packaged Marivo skills.

These checks cover distributable file layout only. Skill prose is policy
content and is intentionally not asserted by the test suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"


def _active_references_to_deleted_semantic_paths(forbidden: str) -> list[str]:
    result = subprocess.run(
        ["git", "grep", "--name-only", forbidden],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    exempt_prefix = "docs/superpowers/specs/"
    exempt_basename = Path(__file__).name
    return [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(exempt_prefix) and Path(line).name != exempt_basename
    ]


def test_analysis_skill_package_layout() -> None:
    assert sorted(path.name for path in ANALYSIS_SKILL_DIR.iterdir()) == [
        "SKILL.md",
        "references",
    ]
    references = ANALYSIS_SKILL_DIR / "references"
    assert sorted(path.name for path in references.iterdir()) == ["runtime-metric-closeout.md"]


def test_analysis_skill_routes_runtime_metric_discovery_and_closeout() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()

    assert 'marivo.help("analysis.runtime_metric")' in text
    assert "question-scoped expression" in text
    assert "semantic-authoring handoff" in text
    assert "references/runtime-metric-closeout.md" in text


def test_analysis_skill_keeps_semantic_api_contract_in_live_help() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()

    assert "focused live help" in text
    assert "does not reproduce" in text
    assert 'marivo.help("analysis.runtime_metric")' in text
    assert "catalog.metrics.get(" not in text
    assert "catalog.<family>.show()" not in text
    assert "entry.details().show()" not in text
    assert "entry.ref" not in text


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_no_active_source_references_deleted_semantic_paths() -> None:
    forbidden = "marivo/skills/marivo-semantic/references"
    assert not _active_references_to_deleted_semantic_paths(forbidden)
