"""Phase 3 agent-facing docs, skill, and discovery contract tests."""

from __future__ import annotations

import re
from pathlib import Path

import marivo.analysis as mv

REPO_ROOT = Path(__file__).resolve().parents[1]

CORE_OPERATORS = (
    "observe",
    "compare",
    "attribute",
    "discover.<objective>",
    "correlate",
    "hypothesis_test",
    "forecast",
    "derive_metric_frame",
    "assess_quality",
)

REMOVED_AGENT_SURFACE_PATTERNS = (
    re.compile(r"\bsession\.decompose\("),
    re.compile(r"\bdecompose\b"),
    re.compile(r"\bdetect\b"),
    re.compile(r"\bsession\.test\("),
    re.compile(r"\bcompare_frames\b"),
    re.compile(r"\bcorrelate_frames\b"),
    re.compile(r"session\.forecast_frame\b"),
    re.compile(r"\bsession\.from_pandas\("),
    re.compile(r"\bsession\.explore_ibis\("),
    re.compile(r"\bsession\.promote_metric_frame\("),
    re.compile(r"\bsession\.promote_delta_frame\("),
    re.compile(r"\bsession\.promote_attribution_frame\("),
    re.compile(r"\brecommended_followups\b"),
    re.compile(r"\bnext_actions\b"),
    re.compile(r"\bDecisionAction\b"),
    re.compile(r"\bdecision_descriptor\b"),
)

RELATIVE_TIMESCOPE_PATTERNS = (
    re.compile(r'timescope\s*=\s*["\'](?:last|previous|prior)_\d+[dwmy]["\']'),
    re.compile(r'mv\.window\(["\'](?:last|previous|prior)_\d+[dwmy]["\']\)'),
)

SKILL_MARKDOWN_PATHS = (
    REPO_ROOT / "marivo/skills/marivo-analysis/SKILL.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/cheatsheet.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/pitfalls.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/backend-setup.md",
)

SITE_ANALYSIS_PATHS = (
    REPO_ROOT / "site/src/content/docs/en/latest/concepts/analysis-workflow.mdx",
    REPO_ROOT / "site/src/content/docs/en/v0.2/concepts/analysis-workflow.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/latest/concepts/analysis-workflow.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/v0.2/concepts/analysis-workflow.mdx",
)

SITE_QUICKSTART_PATHS = (
    REPO_ROOT / "site/src/content/docs/en/latest/quick-start.mdx",
    REPO_ROOT / "site/src/content/docs/en/v0.2/quick-start.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/latest/quick-start.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/v0.2/quick-start.mdx",
)

CURRENT_SITE_EXTRA_PATHS = (
    REPO_ROOT / "site/src/content/docs/en/latest/concepts/evidence.mdx",
    REPO_ROOT / "site/src/content/docs/en/v0.2/concepts/evidence.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/latest/concepts/evidence.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/v0.2/concepts/evidence.mdx",
    REPO_ROOT / "site/src/content/docs/en/latest/concepts/semantic-layer.mdx",
    REPO_ROOT / "site/src/content/docs/en/v0.2/concepts/semantic-layer.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/latest/concepts/semantic-layer.mdx",
    REPO_ROOT / "site/src/content/docs/zh-cn/v0.2/concepts/semantic-layer.mdx",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _skill_example_files() -> tuple[Path, ...]:
    examples_dir = REPO_ROOT / "marivo/skills/marivo-analysis/references/examples"
    return tuple(sorted(p for p in examples_dir.iterdir() if p.suffix == ".py"))


def test_agent_surface_help_topic_teaches_phase3_default_surface() -> None:
    text = mv.help_text("agent_surface")

    assert "Default agent-facing operators" in text
    assert "Base artifact protocol" in text
    assert "contract().affordances" in text
    assert "mechanical compatibility" in text
    assert "quality_summary" in text
    assert "assess_quality" in text
    assert "derive_metric_frame" in text
    assert "session.frame_summaries()" in text
    assert "session.get_frame(ref)" in text
    for operator in CORE_OPERATORS:
        assert operator in text
    assert "recommend" not in text.lower()
    assert "decompose" not in text


def test_phase3_skill_markdown_teaches_only_current_default_surface() -> None:
    failures: list[str] = []
    for path in SKILL_MARKDOWN_PATHS:
        text = _read(path)
        for operator in CORE_OPERATORS:
            if operator not in text:
                failures.append(f"{_relative(path)} missing {operator}")
        for pattern in REMOVED_AGENT_SURFACE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains removed pattern {pattern.pattern}")
        for pattern in RELATIVE_TIMESCOPE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains relative timescope pattern")
    assert failures == []


def test_phase3_skill_examples_use_current_operator_names_and_absolute_timescope() -> None:
    failures: list[str] = []
    for path in _skill_example_files():
        text = _read(path)
        name = path.name
        if "decompose" in name or "detect" in name or "test_hypothesis" in name:
            failures.append(f"{_relative(path)} filename uses old Phase 2 name")
        for pattern in REMOVED_AGENT_SURFACE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains removed pattern {pattern.pattern}")
        for pattern in RELATIVE_TIMESCOPE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains relative timescope pattern")
        if "print(frame.summary())" in text or "print(frame.preview(" in text:
            failures.append(f"{_relative(path)} prints raw summary/preview instead of bounded show")
    assert failures == []


def test_phase3_site_docs_teach_artifact_protocol_and_current_surface() -> None:
    required = (
        "session.attribute",
        "session.derive_metric_frame",
        "quality_summary",
        "session.assess_quality",
        "artifact.contract().affordances",
        "session.frame_summaries()",
        "session.get_frame(ref)",
    )
    failures: list[str] = []
    for path in SITE_ANALYSIS_PATHS + SITE_QUICKSTART_PATHS:
        text = _read(path)
        for phrase in required:
            if phrase not in text:
                failures.append(f"{_relative(path)} missing {phrase}")
        for pattern in REMOVED_AGENT_SURFACE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains removed pattern {pattern.pattern}")
        for pattern in RELATIVE_TIMESCOPE_PATTERNS:
            if pattern.search(text):
                failures.append(f"{_relative(path)} contains relative timescope pattern")
    assert failures == []


def test_phase3_current_site_extra_pages_do_not_teach_old_analysis_operator() -> None:
    failures: list[str] = []
    for path in CURRENT_SITE_EXTRA_PATHS:
        text = _read(path)
        if re.search(r"\bdecompose\b", text):
            failures.append(f"{_relative(path)} still contains decompose")
    assert failures == []


def test_final_report_guidance_marks_next_steps_as_agent_authored() -> None:
    text = _read(REPO_ROOT / "marivo/skills/marivo-analysis/references/final-report.md")

    assert "Agent-authored Next Steps" in text
    assert "Marivo affordances are mechanical compatibility facts" in text
    assert "artifact.contract()" in text
    assert "not recommendations from Marivo" in text
