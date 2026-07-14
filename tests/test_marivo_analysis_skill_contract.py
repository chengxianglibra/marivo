"""Package-shape and ownership tests for the marivo-analysis boundary skill.

Asserts the skill directory contains exactly ``SKILL.md``, that no active
source/test/package metadata references deleted attachment paths, and that
the example runner handles the analysis skill correctly.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"


def test_skill_directory_contains_exactly_skill_md() -> None:
    """The packaged analysis skill shape is exactly one file."""
    entries = sorted(p.name for p in SKILL_DIR.iterdir())
    assert entries == ["SKILL.md"], f"Expected exactly SKILL.md in {SKILL_DIR}; found {entries}"


def test_no_references_directory_remains() -> None:
    """The references/ tree must be fully deleted."""
    refs_dir = SKILL_DIR / "references"
    assert not refs_dir.exists(), f"references/ still exists at {refs_dir}"


def test_no_active_source_references_deleted_analysis_paths() -> None:
    """No active Python source file in marivo/ should reference
    marivo-analysis/references (deleted paths)."""
    forbidden = "marivo-analysis/references"
    offenders: list[str] = []
    for py_file in (REPO_ROOT / "marivo").rglob("*.py"):
        text = py_file.read_text()
        if forbidden in text:
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert not offenders, f"Active source files reference deleted analysis references: {offenders}"


def test_no_active_test_references_deleted_analysis_paths() -> None:
    """No active test file should reference marivo-analysis/references as a
    real path. Test files that assert the absence of these paths are
    themselves exempt (they reference the string in assertions, not as
    path pointers)."""
    forbidden = "marivo-analysis/references"
    exempt_basenames = {
        "test_marivo_analysis_skill_contract.py",
        "test_skill_examples_runner.py",
    }
    offenders: list[str] = []
    for py_file in (REPO_ROOT / "tests").rglob("*.py"):
        if py_file.name in exempt_basenames:
            continue
        text = py_file.read_text()
        if forbidden in text:
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert not offenders, f"Active test files reference deleted analysis references: {offenders}"


def test_semantic_examples_remain_required() -> None:
    """The example runner must still require semantic examples."""
    runner = (REPO_ROOT / "scripts" / "run_skill_examples.py").read_text()
    assert "marivo-semantic" in runner, "run_skill_examples.py must still reference marivo-semantic"
    assert "_SEMANTIC_EXAMPLE_NAMES" in runner, (
        "run_skill_examples.py must still enforce semantic example contract"
    )


def test_analysis_examples_may_be_absent() -> None:
    """The example runner must treat absence of analysis examples as valid."""
    runner = (REPO_ROOT / "scripts" / "run_skill_examples.py").read_text()
    assert "marivo-analysis" in runner, (
        "run_skill_examples.py must still know about marivo-analysis skill dir"
    )
    # The runner must not hard-fail when analysis examples dir is missing.
    assert 'skill_dir.name == "marivo-analysis"' in runner or (
        '"marivo-analysis"' in runner and "examples_dir" in runner
    ), "run_skill_examples.py must allow absent analysis examples"
