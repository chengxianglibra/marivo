from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "marivo-skills/marivo-analysis/SKILL.md"
FINAL_REPORT_PATH = REPO_ROOT / "marivo-skills/marivo-analysis/references/final-report.md"


def test_analysis_skill_links_final_report_guidance() -> None:
    text = SKILL_PATH.read_text()

    assert "references/final-report.md" in text
    assert "frame.summary()" in text
    assert "frame.head(n)" in text


def test_final_report_guidance_contains_required_contract() -> None:
    text = FINAL_REPORT_PATH.read_text()
    required_terms = [
        "Executive Summary",
        "结论摘要",
        "Key Findings",
        "核心发现",
        "Caveats and Assumptions",
        "Recommended Next Steps",
        "Source details",
        "result.meta.evidence_status",
        "result.meta.blocking_issues",
        "result.meta.confidence_scope",
        "result.meta.recommended_followups",
        "session.assess_quality",
        "MarivoReportArtifact",
        "grounding.json",
        "value_refs",
        "narrative layer",
        "artifact-backed",
        "adjacent to each important chart or table",
        "to_mcp_artifact_payload",
        "materialize_mcp_adapter",
        "validate_artifact",
        "render_artifact",
        "MCP adapter",
        "Codex/Data Analytics environments",
        "first visible `render_artifact` call",
        "must not connect to live datasources",
        "recompute main claims",
        "replace `grounding.json` / `flow.json`",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []


def test_final_report_guidance_covers_cdn_review_patterns() -> None:
    text = FINAL_REPORT_PATH.read_text()
    required_terms = [
        "broad rule hits",
        "actionable candidates",
        "high confidence",
        "expected cycle",
        "missing-data gap",
        "low-volume noise",
        "previous value",
        "current value",
        "absolute change",
        "relative change",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []


def test_analysis_cheatsheet_points_to_runtime_help_contract() -> None:
    cheatsheet = (REPO_ROOT / "marivo-skills/marivo-analysis/references/cheatsheet.md").read_text()

    assert "mv.help('discover', format='json')" in cheatsheet
    assert "mv.help('alignment', format='json')" in cheatsheet
    assert "mv.help('MetricFrame', format='json')" in cheatsheet
    assert "mv.help('MetricFrame.components', format='json')" in cheatsheet
