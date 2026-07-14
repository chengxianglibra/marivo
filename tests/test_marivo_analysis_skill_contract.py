"""Package-shape and ownership tests for the marivo-analysis boundary skill.

Asserts the skill directory contains exactly ``SKILL.md``, that no active
source/test/package metadata references deleted attachment paths, and that
the boundary kernel content matches the design spec.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"

# Sections required by the boundary kernel design spec.
REQUIRED_SECTIONS = (
    "Trigger",
    "Mission and authority",
    "Live-contract rule",
    "Semantic authority",
    "Live-state authority",
    "Judgment separation",
    "Evidence integrity",
    "Governed transition",
    "Handoffs",
    "Closeout obligations",
)

# Content forbidden by the boundary kernel design spec.
FORBIDDEN_CONTENT = (
    "references/",
    "mv.help('workflow')",
    'mv.help("workflow")',
    "session.observe(",
    "session.compare(",
    "session.attribute(",
    "session.discover.",
    "session.correlate(",
    "session.hypothesis_test(",
    "session.forecast(",
    "session.derive_metric_frame(",
    "session.assess_quality(",
    "make test",
    "make typecheck",
    "make lint",
    "make examples-check",
    "Internal Marivo Feedback",
    "internal_feedback",
)


def test_skill_directory_contains_exactly_skill_md() -> None:
    """The packaged analysis skill shape is exactly one file."""
    entries = sorted(p.name for p in SKILL_DIR.iterdir())
    assert entries == ["SKILL.md"], f"Expected exactly SKILL.md in {SKILL_DIR}; found {entries}"


def test_no_references_directory_remains() -> None:
    """The references/ tree must be fully deleted."""
    refs_dir = SKILL_DIR / "references"
    assert not refs_dir.exists(), f"references/ still exists at {refs_dir}"


def test_skill_md_contains_all_required_sections() -> None:
    """SKILL.md must contain every section defined by the boundary kernel spec."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    assert not missing, f"SKILL.md missing required sections: {missing}"


def test_skill_md_does_not_contain_forbidden_content() -> None:
    """SKILL.md must not contain API signatures, operator inventory, call
    examples, ordered process, methodology checklist, report template,
    repository commands, or internal feedback procedure."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    found = [snippet for snippet in FORBIDDEN_CONTENT if snippet in text]
    assert not found, f"SKILL.md contains forbidden content: {found}"


def test_skill_md_names_marivo_semantic_for_business_object_handoff() -> None:
    """The missing/disputed business-object handoff must name marivo-semantic."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert "marivo-semantic" in text, (
        "SKILL.md must name marivo-semantic for missing/disputed business objects"
    )


def test_skill_md_allows_unlimited_focused_help_calls() -> None:
    """Complex investigations may consult as many focused topics as needed."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert "as many" in text.lower(), (
        "SKILL.md must state that complex investigations may consult "
        "as many focused topics as needed"
    )


def test_skill_md_help_limits_are_evaluation_thresholds() -> None:
    """Help-call limits are interface evaluation thresholds, not runtime
    permissions."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert "evaluation" in text.lower(), (
        "SKILL.md must state that help-call limits are interface evaluation "
        "thresholds, not runtime permissions"
    )


def test_skill_md_uses_boundary_violation_table() -> None:
    """The skill must define boundary-violation behavior."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert "violation" in text.lower(), "SKILL.md must contain boundary-violation behavior section"


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
    # Tests that legitimately reference the forbidden string in their own
    # assertions are excluded.
    exempt_basenames = {
        "test_marivo_analysis_skill_contract.py",
        "test_skill_examples_runner.py",
        "test_analysis_docs_drift.py",
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
