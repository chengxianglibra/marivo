"""Package shape and stable authoring-policy boundary tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
LATEST_SEMANTIC_DOCS = (
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "latest"
    / "concepts"
    / "semantic-layer.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "docs"
    / "latest"
    / "concepts"
    / "semantic-layer.mdx",
)
MILESTONE1_ACTIVE_DOCS = (
    REPO_ROOT / "docs" / "specs" / "analysis" / "operators-and-frames.md",
    REPO_ROOT / "docs" / "specs" / "semantic" / "datasource-layer.md",
    REPO_ROOT / "docs" / "specs" / "temporal-semantics.md",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "latest"
    / "concepts"
    / "readiness.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "latest"
    / "concepts"
    / "semantic-layer.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "docs"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "docs"
    / "latest"
    / "concepts"
    / "readiness.mdx",
    REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "docs"
    / "latest"
    / "concepts"
    / "semantic-layer.mdx",
)


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


def _active_references_to_deleted_semantic_paths(forbidden: str) -> list[str]:
    result = subprocess.run(
        ["git", "grep", "--name-only", forbidden],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    exempt_prefix = "docs/superpowers/specs/"
    exempt_basename = Path(__file__).name
    return [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(exempt_prefix) and Path(line).name != exempt_basename
    ]


def test_analysis_skill_package_layout() -> None:
    assert sorted(path.name for path in ANALYSIS_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_analysis_skill_routes_through_progressive_help_topology() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()

    assert text.index('marivo.help("analysis")') < text.index('marivo.help("analysis.entry")')
    for target in (
        "analysis.entry",
        "analysis.methods",
        "analysis.inputs",
        "analysis.artifacts",
        "analysis.evidence",
        "analysis.runtime",
        "analysis.boundary.to_pandas",
    ):
        assert target in text
    assert "## Capability routing map" not in text
    assert "| Question or task | Capability |" not in text
    assert "session.events.match/funnel/time_to_event" not in text
    assert "session.lifecycle.replay/distribution" not in text


def test_analysis_skill_preserves_finding_and_runtime_boundaries() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()
    normalized = " ".join(text.split())

    assert "Artifact-owned Finding reads" in normalized
    assert "Finding reads preserve exact derivation" in normalized
    assert "do not combine Findings or prove business validity" in normalized
    assert "neither checks current semantic authority nor datasource freshness" in normalized


def test_analysis_skill_revalidates_recovered_artifacts_before_reuse() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()
    normalized = " ".join(text.split())

    assert 'marivo.help("analysis.session.revalidate")' in text
    assert "After restoring an old Artifact" in normalized
    assert "session.artifact(ref)" in text
    assert "session.revalidate(ref)" in text
    assert "does not prove datasource freshness" in normalized
    assert "re-run a stale branch" in normalized.lower()
    assert "stop and disclose an indeterminate branch" in normalized.lower()


def test_analysis_skill_contains_no_removed_runtime_names() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()

    for stale in (
        "session.get_frame",
        "session.frame_summaries",
        "session.jobs",
        "session.recent_jobs",
        "session.evidence",
        "runtime.artifacts",
        "runtime.jobs",
        "evidence.browse",
        "evidence.exact",
    ):
        assert stale not in text


def test_analysis_skill_follows_runtime_operator_authority_admission() -> None:
    text = (ANALYSIS_SKILL_DIR / "SKILL.md").read_text()
    normalized = " ".join(text.split())

    assert "Artifact-consuming capabilities enforce their registered authority" in normalized
    assert "ArtifactStaleError" in text
    assert "ArtifactAuthorityUnknownError" in text
    assert "materialized continuation may remain valid" in normalized
    assert "Never treat `artifact.contract()` as current revalidation" in normalized


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_semantic_skill_routes_coherent_slice_authoring() -> None:
    text = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text()

    assert text.index('marivo.help("authoring")') < text.index('marivo.help("semantic.authoring")')
    for target in (
        "semantic.authoring",
        "semantic.objects",
        "semantic.builders",
        "semantic.checks",
    ):
        assert f'marivo.help("{target}")' in text
    assert "one dependency-coherent semantic slice" in text
    assert "one `ms.load()`" in text
    assert "`catalog.require(ref)`" in text
    assert "one-object checkpoint loop" in text
    assert "separate verification checkpoint" in text
    assert "| Object kind |" not in text
    assert "| Parameter |" not in text


def test_latest_semantic_docs_share_progressive_routes_without_leaf_parameter_tables() -> None:
    english, chinese = (path.read_text() for path in LATEST_SEMANTIC_DOCS)

    for text in (english, chinese):
        for target in (
            "semantic.authoring",
            "semantic.objects",
            "semantic.builders",
            "semantic.checks",
        ):
            assert f'marivo.help("{target}")' in text
        assert "marivo.help(entry_or_error)" in text
        for target in (
            "analysis.calendar.grain",
            "analysis.calendar.period",
            "analysis.calendar.period_on",
            "analysis.calendar.periods",
            "analysis.temporal_set.occurrence",
            "analysis.temporal_set.occurrences",
        ):
            assert target in text
        assert "marivo.help(error)" in text

    assert "| Parameter | Type | Required | Default | Meaning |" not in english
    assert "| 参数 | 类型 | 必填 | 默认 | 含义 |" not in chinese


def test_semantic_skill_teaches_governed_terminal_raw_sql() -> None:
    text = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text()

    assert "normal governed exploration option" in text
    assert "read-only, bounded" in text
    assert "terminal" in text
    assert "cannot be passed to typed analysis" in text


def test_semantic_skill_enforces_first_use_authority_without_reapproval() -> None:
    text = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text()

    assert "Before the first typed analysis use" in text
    assert "approved existing project definition" in text
    assert "proceed without asking for redundant confirmation" in text
    assert "stop before typed analysis handoff" in text


def test_active_milestone1_docs_do_not_teach_removed_authoring_apis() -> None:
    forbidden = (
        "catalog.verify",
        "VerifyResult",
        "AuthoringContract",
        "snapshot.entity(",
        "snapshot.dimensions(",
        "snapshot.time_dimensions(",
        "snapshot.measures(",
        "snapshot.relationships(",
        "snapshot.values(",
        "report.contract()",
        "entry.contract()",
    )

    stale = {
        str(path.relative_to(REPO_ROOT)): token
        for path in MILESTONE1_ACTIVE_DOCS
        for token in forbidden
        if token in path.read_text()
    }
    assert stale == {}


def test_latest_quick_start_uses_conditional_first_use_authority() -> None:
    paths = (
        REPO_ROOT / "site" / "src" / "content" / "docs" / "docs" / "latest" / "quick-start.mdx",
        REPO_ROOT
        / "site"
        / "src"
        / "content"
        / "docs"
        / "zh-cn"
        / "docs"
        / "latest"
        / "quick-start.mdx",
    )
    texts = tuple(path.read_text() for path in paths)

    assert "current request, an approved project definition" in texts[0]
    assert "only when a reusable business choice remains unresolved" in texts[0]
    assert "当前请求、已批准的项目定义" in texts[1]
    assert "只有某个可复用业务选择仍未解决时" in texts[1]


def test_no_active_source_references_deleted_semantic_paths() -> None:
    forbidden = "marivo/skills/marivo-semantic/references"
    assert not _active_references_to_deleted_semantic_paths(forbidden)
