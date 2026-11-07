"""Package shape and stable authoring-policy boundary tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from marivo._help.render import render_help_text
from tests.support.paths import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
SERVICE_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-service"


def test_analysis_skill_package_layout() -> None:
    assert sorted(path.name for path in ANALYSIS_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_service_skill_package_layout() -> None:
    assert sorted(path.name for path in SERVICE_SKILL_DIR.iterdir()) == ["SKILL.md"]


@pytest.mark.parametrize(
    ("directory", "entry_target"),
    (
        (ANALYSIS_SKILL_DIR, "analysis"),
        (SEMANTIC_SKILL_DIR, "authoring"),
        (SERVICE_SKILL_DIR, "analysis"),
    ),
)
def test_packaged_skill_help_routes_resolve_from_the_owning_root(
    directory: Path, entry_target: str
) -> None:
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    targets = re.findall(r"`marivo help ([a-z][a-z0-9_.]*)`", text)
    assert targets and targets[0] == entry_target
    for target in targets:
        rendered, _, _ = render_help_text(target)
        assert rendered.startswith(f"Target: {target}\n"), (directory, target)
