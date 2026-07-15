"""Package-shape and ownership tests for the marivo-analysis boundary skill.

Asserts the skill directory contains exactly ``SKILL.md``, that no active
source/test/package metadata references deleted attachment paths, and that
the example runner handles the analysis skill correctly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"


def _active_references_to_deleted_semantic_paths(forbidden: str) -> list[str]:
    """Return repo-relative paths of active, non-spec files that reference the
    deleted marivo-semantic/references path.

    Historical specs under ``docs/superpowers/specs/`` are intentionally
    unchanged and excluded. This contract test file references the string in
    its assertions and is also excluded.
    """
    result = subprocess.run(
        ["git", "grep", "--name-only", forbidden],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    exempt_prefix = "docs/superpowers/specs/"
    exempt_basename = "test_marivo_analysis_skill_contract.py"
    offenders: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        if line.startswith(exempt_prefix):
            continue
        if Path(line).name == exempt_basename:
            continue
        offenders.append(line)
    return offenders


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


def test_semantic_examples_may_be_absent() -> None:
    """The example runner must treat absence of semantic examples as valid.

    The marivo-semantic skill is now a single-file boundary kernel with no
    packaged examples, mirroring marivo-analysis. The runner must still know
    about the skill directory (for SKILL.md checks) but must not require
    examples or enforce a semantic example contract.
    """
    runner = (REPO_ROOT / "scripts" / "run_skill_examples.py").read_text()
    assert "marivo-semantic" in runner, (
        "run_skill_examples.py must still reference marivo-semantic skill dir"
    )
    # The semantic example contract enforcement must be removed.
    assert "_SEMANTIC_EXAMPLE_NAMES" not in runner, (
        "run_skill_examples.py must not enforce semantic example contract"
    )
    assert "_check_semantic_example_contract" not in runner, (
        "run_skill_examples.py must not enforce semantic example contract"
    )


def test_marivo_semantic_skill_is_one_file_boundary_kernel() -> None:
    """The packaged semantic skill shape is exactly one file, with no embedded
    code/repair symbols and all required boundary sections present."""
    semantic_dir = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
    entries = sorted(p.name for p in semantic_dir.iterdir())
    assert entries == ["SKILL.md"], f"Expected exactly SKILL.md in {semantic_dir}; found {entries}"
    text = (semantic_dir / "SKILL.md").read_text(encoding="utf-8")
    for forbidden in ("def ", "class ", "canonical_id=", "RepairKind", "AuthoringRepair"):
        assert forbidden not in text, f"Forbidden token {forbidden!r} present in semantic SKILL.md"
    for required in ("Ownership", "Hard boundaries", "Handoff", "Closeout"):
        assert required in text, f"Required section {required!r} missing from semantic SKILL.md"


def test_no_active_source_references_deleted_semantic_paths() -> None:
    """No active non-spec source file should reference
    marivo-semantic/references (deleted path). Historical specs under
    docs/superpowers/specs/ are intentionally unchanged and excluded."""
    forbidden = "marivo/skills/marivo-semantic/references"
    offenders = _active_references_to_deleted_semantic_paths(forbidden)
    assert not offenders, (
        f"Active non-spec files reference deleted semantic references: {offenders}"
    )


def test_no_active_test_references_deleted_semantic_paths() -> None:
    """No active test file (other than this contract test) should reference
    marivo-semantic/references as a real path. This test file references the
    string in its assertion and is exempt."""
    forbidden = "marivo/skills/marivo-semantic/references"
    offenders = _active_references_to_deleted_semantic_paths(forbidden)
    assert not offenders, (
        f"Active non-spec files reference deleted semantic references: {offenders}"
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
