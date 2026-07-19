"""Package-shape and ownership tests for the packaged Marivo skills."""

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


def test_analysis_skill_packages_conditional_runtime_closeout_reference() -> None:
    entries = sorted(p.name for p in SKILL_DIR.iterdir())
    assert entries == ["SKILL.md", "references"]
    refs_dir = SKILL_DIR / "references"
    assert sorted(path.name for path in refs_dir.iterdir()) == ["runtime-metric-closeout.md"]
    kernel = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "references/runtime-metric-closeout.md" in kernel


def test_runtime_closeout_reference_carries_required_disclosures() -> None:
    text = (SKILL_DIR / "references" / "runtime-metric-closeout.md").read_text(encoding="utf-8")
    for required in (
        "aggregate/fold",
        "branch-local slice",
        "zero-division policy",
        "presentation labels are non-authoritative",
        "owning analysis session/artifact scope",
        "current is",
        "baseline is the comparator",
    ):
        assert required in text


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


def test_analysis_skill_bounds_historical_session_reference() -> None:
    """Historical sessions are selectively inspected reference memory."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    section = text[text.index("## Historical session reference") : text.index("## Hard boundaries")]
    for required in (
        "external reference memory",
        "loaded by default",
        "same failure recurs",
        "three candidate sessions",
        "do not support current material claims",
        "current semantic catalog",
        "runtime fingerprint",
        "analysis scope",
    ):
        assert required in section, f"Missing historical-session boundary: {required}"


def test_analysis_skill_defers_semantic_authoring_and_allows_raw_sql_escape() -> None:
    """Semantic gaps stop typed work but may continue through a terminal escape."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for required in (
        "During analysis the agent must not",
        "semantic definitions",
        "user approves the closeout proposal",
        "`md.raw_sql(...)`",
        "without prior approval",
        "temporary",
        "inferred semantics",
        "cannot re-enter typed analysis",
        "it is not permission to mutate the semantic layer",
        "requires explicit user approval",
        "no-lineage/no-evidence-continuity",
        "canonical artifact claims and raw-SQL-supported claims remain",
    ):
        assert required in text, f"Missing semantic-gap/raw-SQL boundary: {required}"


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
