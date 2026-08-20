"""Package-shape tests for packaged Marivo skills.

Skill prose is policy content and is intentionally not asserted by this test
module.
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


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_no_active_source_references_deleted_semantic_paths() -> None:
    forbidden = "marivo/skills/marivo-semantic/references"
    assert not _active_references_to_deleted_semantic_paths(forbidden)
