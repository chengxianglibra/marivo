"""Package-shape and ownership tests for the packaged Marivo skills.

Asserts the skill directory contains exactly ``SKILL.md``, that no active
source/test/package metadata references deleted attachment paths, and that the
single-file boundary kernels stay bounded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
MAX_SKILL_LINES = 600


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
    exempt_basenames = {"test_marivo_analysis_skill_contract.py"}
    offenders: list[str] = []
    for py_file in (REPO_ROOT / "tests").rglob("*.py"):
        if py_file.name in exempt_basenames:
            continue
        text = py_file.read_text()
        if forbidden in text:
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert not offenders, f"Active test files reference deleted analysis references: {offenders}"


def test_analysis_skill_keeps_session_scripts_reference_only() -> None:
    """Session-local scripts are rerunnable workspaces, not reusable evidence."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    live_contract_position = text.index("## Live-contract rule")
    workspace_position = text.index("## Script workspace")
    hard_boundaries_position = text.index("## Hard boundaries")

    assert live_contract_position < workspace_position < hard_boundaries_position

    workspace = text[workspace_position:hard_boundaries_position]
    for required in (
        "<project_root>/.marivo/analysis/sessions/<session.id>/scripts/",
        "verified `<analysis-python>`",
        "`succeeded` job",
        "artifact remains recoverable",
        "Reference-only",
        "never executing it directly",
        "copying it wholesale",
        "re-resolve semantic refs, time scopes, and parameters",
        "script is not evidence",
        "artifacts/jobs",
    ):
        assert required in workspace, f"Missing script-workspace boundary: {required}"


def test_marivo_semantic_skill_is_one_file_routing_kernel() -> None:
    """The packaged semantic skill shape is exactly one file, with no embedded
    code/repair symbols and all required routing sections present."""
    entries = sorted(p.name for p in SEMANTIC_SKILL_DIR.iterdir())
    assert entries == ["SKILL.md"], (
        f"Expected exactly SKILL.md in {SEMANTIC_SKILL_DIR}; found {entries}"
    )
    text = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for forbidden in ("def ", "class ", "canonical_id=", "RepairKind", "AuthoringRepair"):
        assert forbidden not in text, f"Forbidden token {forbidden!r} present in semantic SKILL.md"
    for required in ("Ownership", "Hard boundaries", "Routing", "Closeout"):
        assert required in text, f"Required section {required!r} missing from semantic SKILL.md"


def test_marivo_semantic_skill_defines_aliases_before_first_use() -> None:
    """Cold-start agents must see each public alias before its first help call."""
    skill_path = SEMANTIC_SKILL_DIR / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    for import_statement, first_use in (
        ("import marivo.datasource as md", "md.help("),
        ("import marivo.semantic as ms", "ms.help("),
    ):
        import_position = text.find(import_statement)
        use_position = text.find(first_use)
        assert import_position >= 0, f"Missing public alias declaration: {import_statement}"
        assert use_position >= 0, f"Missing live help route: {first_use}"
        assert import_position < use_position, (
            f"Public alias must be defined before first use: {import_statement}"
        )


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


def test_packaged_skill_files_stay_bounded() -> None:
    """Single-file boundary kernels must stay small enough to load directly."""
    for skill_dir in (SKILL_DIR, SEMANTIC_SKILL_DIR):
        skill_path = skill_dir / "SKILL.md"
        line_count = len(skill_path.read_text(encoding="utf-8").splitlines())
        assert line_count <= MAX_SKILL_LINES, (
            f"{skill_path} has {line_count} lines; reduce it to at most {MAX_SKILL_LINES} "
            "by moving mechanical contracts to live help or structured results"
        )
