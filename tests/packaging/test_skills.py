"""Package shape and stable authoring-policy boundary tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.paths import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"


def test_packaged_workflows_have_only_semantic_and_analysis() -> None:
    from marivo.config import PACKAGED_SKILLS

    expected = {"marivo-semantic", "marivo-analysis"}
    assert set(PACKAGED_SKILLS) == expected
    assert {
        path.name
        for path in (REPO_ROOT / "marivo/skills").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    } == expected


def test_analysis_skill_package_layout() -> None:
    assert sorted(path.name for path in ANALYSIS_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


@pytest.mark.parametrize(
    ("directory", "entry_target"),
    (
        (ANALYSIS_SKILL_DIR, "analysis"),
        (SEMANTIC_SKILL_DIR, "authoring"),
    ),
)
def test_packaged_skill_help_routes_resolve_from_the_owning_root(
    directory: Path, entry_target: str
) -> None:
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    targets = re.findall(r"`marivo help ([a-z][a-z0-9_.]*)`", text)
    assert targets and targets[0] == entry_target
