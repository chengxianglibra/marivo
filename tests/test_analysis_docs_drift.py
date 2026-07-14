"""Drift tests enforcing the active analysis ownership model across
repository guidance, active specs, and latest site docs.

These tests assert that current text contains:
- environment-bound ``python -m marivo help analysis`` capability route
- capability / static-state / error layering (live surfaces own
  capabilities and runtime guidance; the skill owns hard boundaries,
  handoffs, evidence continuity, and closeout obligations; the agent
  owns planning and judgment)
- explicit terminal / governed boundaries
- agent-owned planning

They reject:
- active workflow-topic / advanced / skill-manual claims
- attachment paths (``references/``)

Historical release notes and versioned docs are explicitly excluded.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Active file paths (not historical versioned docs)
# ---------------------------------------------------------------------------

AGENT_GUIDE = REPO_ROOT / "agent-guide.md"
ACTIVE_SURFACE_SPEC = REPO_ROOT / "docs" / "specs" / "agent-friendly-public-surface.md"
ANALYSIS_DESIGN_SPEC = REPO_ROOT / "docs" / "specs" / "analysis" / "python-analysis-design.md"
OPERATORS_SPEC = REPO_ROOT / "docs" / "specs" / "analysis" / "operators-and-frames.md"
SEMANTIC_HELP_PY = REPO_ROOT / "marivo" / "semantic" / "help.py"

EN_ANALYSIS_WORKFLOW = (
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "en"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx"
)
ZH_ANALYSIS_WORKFLOW = (
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx"
)
EN_FIRST_ANALYSIS = (
    REPO_ROOT / "site" / "src" / "content" / "docs" / "en" / "latest" / "first-analysis.mdx"
)
ZH_FIRST_ANALYSIS = (
    REPO_ROOT / "site" / "src" / "content" / "docs" / "zh-cn" / "latest" / "first-analysis.mdx"
)
EN_EVIDENCE = (
    REPO_ROOT / "site" / "src" / "content" / "docs" / "en" / "latest" / "concepts" / "evidence.mdx"
)
ZH_EVIDENCE = (
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "latest"
    / "concepts"
    / "evidence.mdx"
)

ACTIVE_LATEST_DOCS = (
    EN_ANALYSIS_WORKFLOW,
    ZH_ANALYSIS_WORKFLOW,
    EN_FIRST_ANALYSIS,
    ZH_FIRST_ANALYSIS,
    EN_EVIDENCE,
    ZH_EVIDENCE,
)

# Directories excluded from stale-reference scans.
EXCLUDED_PARTS = (
    "docs/superpowers/specs/",
    "docs/superpowers/plans/",
    "site/src/content/docs/en/v0.",
    "site/src/content/docs/zh-cn/v0.",
    "site/src/content/docs/en/latest/release-notes/0.2",
    "site/src/content/docs/en/latest/release-notes/0.3.0",
    "site/src/content/docs/en/latest/release-notes/0.3.1",
    "site/src/content/docs/en/latest/release-notes/0.3.2",
    "site/src/content/docs/zh-cn/latest/release-notes/0.2",
    "site/src/content/docs/zh-cn/latest/release-notes/0.3.0",
    "site/src/content/docs/zh-cn/latest/release-notes/0.3.1",
    "site/src/content/docs/zh-cn/latest/release-notes/0.3.2",
    ".venv/",
    "__pycache__/",
    "build/",
)

# File extensions to scan for stale references.
SCAN_EXTENSIONS = {".py", ".rst", ".md", ".mdx", ".toml", ".cfg", ".txt", ".yaml", ".yml"}

# This test file is excluded by basename to avoid self-referential failure.
SELF_BASENAME = "test_analysis_docs_drift.py"

# Stale patterns that must not appear in active latest docs.
STALE_WORKFLOW_PATTERNS = (
    "mv.help('workflow')",
    'mv.help("workflow")',
    "mv.help('advanced')",
    'mv.help("advanced")',
)
STALE_ATTACHMENT_PATTERNS = (
    "marivo-analysis/references",
    "references/",
)


# ---------------------------------------------------------------------------
# agent-guide.md ownership model
# ---------------------------------------------------------------------------


def test_agent_guide_owns_analysis_layering_with_new_model() -> None:
    """The Analysis Guidance Layering section must reflect the new ownership
    model: live surfaces own capabilities/runtime guidance; the skill owns
    hard boundaries, handoffs, evidence continuity, closeout obligations;
    the agent owns planning/judgment."""
    text = AGENT_GUIDE.read_text()
    normalized = " ".join(text.split())
    assert "hard boundaries" in normalized
    assert "handoffs" in normalized
    assert "evidence continuity" in normalized
    assert "closeout" in normalized
    assert "agent owns" in normalized.lower()
    # The old "skill owns workflow only" must be gone.
    assert "owns workflow only" not in normalized


def test_agent_guide_routes_analysis_help_through_environment() -> None:
    """The guide must point agents to the environment-verified CLI route."""
    text = AGENT_GUIDE.read_text()
    assert "python -m marivo help analysis" in text


# ---------------------------------------------------------------------------
# Active specs: capability_id, boundary ports, family gate, removed describe/plot
# ---------------------------------------------------------------------------


def test_operators_spec_records_capability_id() -> None:
    """The operators spec must mention capability_id on affordances."""
    text = OPERATORS_SPEC.read_text()
    assert "capability_id" in text


def test_operators_spec_records_boundary_ports() -> None:
    """The operators spec must mention boundary_ports on ArtifactContract."""
    text = OPERATORS_SPEC.read_text()
    assert "boundary_ports" in text or "boundary port" in text.lower()


def test_operators_spec_records_family_gate() -> None:
    """The operators spec must mention the runtime family gate from the
    registry's accepted_inputs."""
    text = OPERATORS_SPEC.read_text()
    assert "accepted_inputs" in text or "family gate" in text.lower()


def test_operators_spec_records_removed_describe_and_plot() -> None:
    """The operators spec must note that BaseFrame.describe() and
    BaseFrame.plot() are removed (intentional AttributeError)."""
    text = OPERATORS_SPEC.read_text()
    normalized = " ".join(text.split())
    # The spec must explicitly state describe/plot are removed.
    assert "describe" in normalized.lower()
    assert "plot" in normalized.lower()
    assert "removed" in normalized.lower() or "AttributeError" in normalized
    # The spec must not list describe() as a public frame accessor.
    assert ".describe()" not in text.replace("BaseFrame.describe()", "", 1)


def test_operators_spec_records_boundary_derive_metric_frame() -> None:
    """The operators spec must record the boundary capability id
    boundary.derive_metric_frame."""
    text = OPERATORS_SPEC.read_text()
    assert "boundary.derive_metric_frame" in text


def test_analysis_design_spec_owns_new_layering_model() -> None:
    """The analysis design spec must reflect the new ownership model."""
    text = ANALYSIS_DESIGN_SPEC.read_text()
    assert "hard boundaries" in text or "boundary" in text.lower()
    assert "agent owns" in text.lower() or "judgment stays with the agent" in text.lower()
    # The old "skill owns workflow only" must be gone.
    assert "owns workflow only" not in text


def test_active_surface_spec_no_stale_workflow_help() -> None:
    """The agent-friendly surface spec must not reference mv.help('workflow')."""
    text = ACTIVE_SURFACE_SPEC.read_text()
    for pattern in STALE_WORKFLOW_PATTERNS:
        assert pattern not in text, f"Active surface spec contains stale {pattern!r}"


def test_active_surface_spec_owns_new_analysis_layering() -> None:
    """The agent-friendly surface spec must reflect the new ownership model."""
    text = ACTIVE_SURFACE_SPEC.read_text()
    assert "boundary" in text.lower()
    assert "agent owns" in text.lower() or "judgment" in text.lower()
    assert "owns workflow only" not in text


# ---------------------------------------------------------------------------
# Semantic help cross-track pointer
# ---------------------------------------------------------------------------


def test_semantic_help_cross_track_pointer_updated() -> None:
    """The semantic help module must not point to mv.help('workflow').
    It should point to python -m marivo help analysis or mv.help()."""
    text = SEMANTIC_HELP_PY.read_text()
    for pattern in STALE_WORKFLOW_PATTERNS:
        assert pattern not in text, f"semantic help.py contains stale {pattern!r}"


# ---------------------------------------------------------------------------
# Latest site docs: no stale workflow/advanced help references
# ---------------------------------------------------------------------------


def test_latest_docs_contain_no_stale_workflow_help() -> None:
    """No active latest site doc should reference mv.help('workflow') or
    mv.help('advanced')."""
    offenders: list[str] = []
    for doc_path in ACTIVE_LATEST_DOCS:
        text = doc_path.read_text()
        for pattern in STALE_WORKFLOW_PATTERNS:
            if pattern in text:
                offenders.append(f"{doc_path.name}: contains {pattern!r}")
    assert not offenders, f"Latest docs contain stale workflow/advanced help: {offenders}"


def test_latest_docs_contain_no_stale_attachment_paths() -> None:
    """No active latest site doc should reference marivo-analysis/references
    or references/ attachment paths."""
    offenders: list[str] = []
    for doc_path in ACTIVE_LATEST_DOCS:
        text = doc_path.read_text()
        for pattern in STALE_ATTACHMENT_PATTERNS:
            if pattern in text:
                offenders.append(f"{doc_path.name}: contains {pattern!r}")
    assert not offenders, f"Latest docs contain stale attachment paths: {offenders}"


def test_latest_analysis_workflow_docs_route_through_environment() -> None:
    """Both EN and ZH analysis-workflow docs must route entry through the
    project interpreter and fingerprint (python -m marivo help analysis)."""
    for doc_path in (EN_ANALYSIS_WORKFLOW, ZH_ANALYSIS_WORKFLOW):
        text = doc_path.read_text()
        assert "python -m marivo help analysis" in text or "marivo help analysis" in text, (
            f"{doc_path.name} must route through the project interpreter"
        )


# ---------------------------------------------------------------------------
# Active skill manual claims must be absent from latest docs
# ---------------------------------------------------------------------------


def test_latest_docs_do_not_claim_skill_owns_workflow_only() -> None:
    """No active latest site doc should claim the analysis skill owns
    workflow only."""
    for doc_path in ACTIVE_LATEST_DOCS:
        text = doc_path.read_text()
        assert "owns workflow only" not in text, (
            f"{doc_path.name} contains stale 'owns workflow only' claim"
        )


# ---------------------------------------------------------------------------
# No stale references anywhere in active (non-historical) source
# ---------------------------------------------------------------------------


def test_no_active_source_contains_stale_workflow_help() -> None:
    """No active source/test/doc/package file (outside historical archives)
    should contain mv.help('workflow') or mv.help('advanced')."""
    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if path.name == SELF_BASENAME:
            continue
        if any(excluded in rel for excluded in EXCLUDED_PARTS):
            continue
        # Exclude the skill contract test which legitimately references the
        # forbidden strings as test data.
        if path.name == "test_marivo_analysis_skill_contract.py":
            continue
        text = path.read_text(errors="ignore")
        for pattern in STALE_WORKFLOW_PATTERNS:
            if pattern in text:
                offenders.append(f"{rel}: contains {pattern!r}")
                break
    assert not offenders, (
        f"Active source/test/doc/package files contain stale "
        f"workflow/advanced help references: {offenders}"
    )
