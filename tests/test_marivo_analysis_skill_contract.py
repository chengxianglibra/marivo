"""Package-shape and ownership tests for the packaged Marivo skills."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
MAX_SKILL_LINES = 200
MAX_SKILL_CODEPOINTS = 10_000
MAX_SEMANTIC_SKILL_LINES = 600
MAX_SEMANTIC_SKILL_CODEPOINTS = 30_000


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


def test_analysis_skill_uses_demand_driven_help_after_environment_entry() -> None:
    """Environment entry is one-time; later guidance comes from live objects."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    section = text[
        text.index("## Mission and authority") : text.index("## Bounded investigation loop")
    ]
    normalized = " ".join(section.split())

    assert "<analysis-python> -m marivo help" in normalized
    assert "`.show()`" in normalized
    assert "`.contract()`" in normalized
    assert 'marivo.help("analysis.<target>")' in normalized


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
    workspace = text[text.index("## Script and session discipline") : text.index("## Closeout")]
    workspace = " ".join(workspace.split())
    for required in (
        "<project_root>/.marivo/analysis/sessions/<session.id>/scripts/",
        "succeeded job",
        "artifact remains recoverable",
        "Never execute it directly",
        "copy it wholesale",
        "Re-resolve refs, windows, policies, and scope",
    ):
        assert required in workspace, f"Missing script-workspace boundary: {required}"


def test_analysis_skill_bounds_historical_session_reference() -> None:
    """Historical sessions are selectively inspected reference memory."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    section = text[text.index("## Deterministic stop rule") : text.index("## Hard boundaries")]
    for required in (
        "reference memory only",
        "same failure recurs",
        "no more than three candidates",
        "never support current claims",
        "current artifacts",
    ):
        assert required in section, f"Missing historical-session boundary: {required}"


def test_analysis_skill_defers_semantic_authoring_and_allows_raw_sql_escape() -> None:
    """Semantic gaps stop typed work but may continue through a terminal escape."""
    text = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
    for required in (
        "must not add or edit semantic definitions",
        "missing or disputed business object stops",
        "`md.raw_sql(...)`",
        "cannot re-enter typed analysis",
        "temporary assumptions",
        "loss of typed lineage/evidence continuity",
        "request approval",
    ):
        assert required in text, f"Missing semantic-gap/raw-SQL boundary: {required}"


def test_analysis_skill_routes_event_journeys_to_live_help_and_coverage_evidence() -> None:
    """Event routing stays in the skill while reducers stay in live help."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for required in (
        'marivo.help("analysis.events.match")',
        ".contract()",
        "censoring",
    ):
        assert required in text, f"Missing Event Journey routing boundary: {required}"
    for forbidden in ("events.funnel(", "events.time_to_event(", "select_subjects("):
        assert forbidden not in text


def test_packaged_skills_route_lifecycle_without_copying_mechanical_contracts() -> None:
    """Lifecycle routing stays in skills while signatures and shapes stay in help."""
    analysis = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    semantic = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    for required in (
        'marivo.help("analysis.lifecycle.replay")',
        "StateModels",
        "censoring",
    ):
        assert required in analysis, f"Missing Lifecycle analysis boundary: {required}"
    for required in (
        "normative states",
        "replay windows",
        "completeness assumptions",
        "not silently treated as replay-ready",
    ):
        assert required in semantic, f"Missing StateModel authoring boundary: {required}"
    for forbidden in (
        "LifecycleFrame[history]",
        "invalid_lifecycle_seed",
        "model_state_mismatch",
    ):
        assert forbidden not in analysis
    assert "ms.state_model(" not in semantic


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


def test_marivo_semantic_skill_imports_marivo_before_first_help_call() -> None:
    """Cold-start agents must import the sole public help owner before use."""
    skill_path = SEMANTIC_SKILL_DIR / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    import_position = text.find("import marivo")
    canonical_route = text.index("## Canonical route")
    assert import_position >= 0
    assert import_position < canonical_route
    assert "md.help(" not in text
    assert "ms.help(" not in text
    assert "mv.help(" not in text


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
        codepoint_count = len(skill_path.read_text(encoding="utf-8"))
        max_lines = MAX_SKILL_LINES if skill_dir == SKILL_DIR else MAX_SEMANTIC_SKILL_LINES
        max_codepoints = (
            MAX_SKILL_CODEPOINTS if skill_dir == SKILL_DIR else MAX_SEMANTIC_SKILL_CODEPOINTS
        )
        assert line_count <= max_lines, (
            f"{skill_path} has {line_count} lines; reduce it to at most {max_lines} "
            "by moving mechanical contracts to live help or structured results"
        )
        assert codepoint_count <= max_codepoints


def test_analysis_skill_has_deterministic_stop_and_no_bypass_rules() -> None:
    text = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())

    for required in (
        "same root cause occurs twice",
        "one focused-help recovery",
        "Do not read business rows directly through Ibis, DuckDB, pandas",
        "minimum sufficient evidence",
    ):
        assert required in text
