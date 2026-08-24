"""Package shape and stable authoring-policy boundary tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-analysis"
SEMANTIC_SKILL_DIR = REPO_ROOT / "marivo" / "skills" / "marivo-semantic"
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


def test_semantic_skill_package_layout() -> None:
    assert sorted(path.name for path in SEMANTIC_SKILL_DIR.iterdir()) == ["SKILL.md"]


def test_semantic_skill_routes_coherent_slice_authoring() -> None:
    text = (SEMANTIC_SKILL_DIR / "SKILL.md").read_text()

    assert "one dependency-coherent semantic slice" in text
    assert "one `ms.load()`" in text
    assert "`catalog.require(ref)`" in text
    assert "one-object checkpoint loop" in text
    assert "separate verification checkpoint" in text


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
