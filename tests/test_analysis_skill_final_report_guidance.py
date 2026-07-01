from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "marivo/skills/marivo-analysis/SKILL.md"
FINAL_REPORT_PATH = REPO_ROOT / "marivo/skills/marivo-analysis/references/final-report.md"


def test_analysis_skill_links_final_report_guidance() -> None:
    text = SKILL_PATH.read_text()

    assert "references/final-report.md" in text
    # show() is the default inspection pattern for analysis artifacts
    assert "frame.show()" in text or ".show()" in text


def test_analysis_skill_owns_workflow_not_static_contracts() -> None:
    text = SKILL_PATH.read_text()

    required_terms = [
        "owns workflow only",
        "mv.help()",
        'mv.help("agent_surface")',
        "artifact.show()",
        "artifact.contract()",
        "mechanically valid next actions",
        "agent judgment",
        "marivo-semantic",
    ]
    forbidden_terms = [
        "## Where filter",
        "## Derived ratio and weighted-average components",
        "## Minimal templates",
        "## Cross-dataset observe",
        "Adapt the nearest `references/examples/NN_*.py`",
        "`references/examples/*.py` — runnable templates (primary reference)",
        "runnable shape of any intent see the matching `references/examples/NN_*.py`",
    ]

    missing = [term for term in required_terms if term not in text]
    present = [term for term in forbidden_terms if term in text]

    assert missing == []
    assert present == []


def test_analysis_skill_recaps_missing_semantic_layer_objects() -> None:
    text = SKILL_PATH.read_text()
    normalized_text = " ".join(text.split())

    required_phrases = [
        "When the analysis exposes missing semantic-layer objects",
        "metadata",
        "name them in the recap",
        "tell the user what to add",
        "`marivo-semantic`",
        "missing metric",
        "dimension",
        "time dimension",
        "entity relationship",
        "unit",
        "business context",
        "analysis step was blocked or weakened",
        "missing semantic object",
    ]

    missing = [phrase for phrase in required_phrases if phrase not in normalized_text]
    assert missing == []


def test_analysis_skill_does_not_make_examples_the_methodology() -> None:
    text = SKILL_PATH.read_text()

    assert "Examples are smoke tests and copyable starting points" in text
    assert "not the analysis methodology" in text
    assert "For exact callable contracts, use `mv.help" in text


def test_final_report_guidance_contains_required_contract() -> None:
    text = FINAL_REPORT_PATH.read_text()
    normalized_text = " ".join(text.split())
    required_terms = [
        "Executive Summary",
        "结论摘要",
        "Key Findings",
        "核心发现",
        "Caveats and Assumptions",
        "Agent-authored Next Steps",
        "Source details",
        "result.meta.evidence_status",
        "result.meta.blocking_issues",
        "result.meta.confidence_scope",
        "artifact.contract()",
        "session.assess_quality",
        "narrative layer",
        "evidence-backed",
        "adjacent to each important chart or table",
        "session.jobs()",
        "session.job(id)",
        "session.frame_summaries()",
        "session.get_frame(ref)",
        "session.knowledge()",
        "session.evidence",
        "Marivo does not generate or publish reports",
        "Do not look for Marivo report package APIs",
        "marivo publish <path>",
    ]
    forbidden_terms = [
        "MarivoReportArtifact",
        "ReportRegistration",
        "session.save_report",
        "session.publish_report",
        "validate_report_artifact",
        "to_mcp_artifact_payload",
        "MCP adapter",
        "Publishing handoff",
        "grounding.json",
        "value_refs",
    ]

    missing = [term for term in required_terms if term not in normalized_text]
    present = [term for term in forbidden_terms if term in normalized_text]
    assert missing == []
    assert present == []


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
    cheatsheet = (REPO_ROOT / "marivo/skills/marivo-analysis/references/cheatsheet.md").read_text()

    assert "mv.help('discover')" in cheatsheet
    assert "mv.help('alignment')" in cheatsheet
    assert "mv.help('MetricFrame')" in cheatsheet
    assert "mv.help('MetricFrame.components')" in cheatsheet
