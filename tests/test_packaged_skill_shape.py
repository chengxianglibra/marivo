"""Package shape and stable authoring-policy boundary tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"


def test_temporal_semantics_is_registered_as_a_current_runtime_contract() -> None:
    temporal_spec = REPO_ROOT / "docs" / "specs" / "temporal-semantics.md"
    text = temporal_spec.read_text(encoding="utf-8")

    assert "Status: implemented current contract." in text
    assert "does not describe the current runtime" not in text
    for relative_path in (
        "docs/README.md",
        "docs/specs/semantic/overview.md",
        "docs/specs/analysis/python-analysis-design.md",
    ):
        current_text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "proposed cross-layer" not in current_text


def test_analysis_skill_package_layout() -> None:
    assert sorted(path.name for path in ANALYSIS_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]
