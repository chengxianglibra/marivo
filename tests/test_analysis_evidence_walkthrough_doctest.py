"""Verify analysis skill docs stay aligned with the current agent contract."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "marivo/skills/marivo-analysis/SKILL.md"
ACTIVE_SKILL_DOCS = [
    SKILL_PATH,
    REPO_ROOT / "marivo/skills/marivo-analysis/references/final-report.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/cheatsheet.md",
    REPO_ROOT / "marivo/skills/marivo-analysis/references/pitfalls.md",
]


def test_analysis_skill_doc_is_workflow_only_not_walkthrough_source() -> None:
    text = SKILL_PATH.read_text()

    assert "## Walkthrough" not in text
    required_markers = [
        "owns workflow only",
        "mv.help()",
        "artifact.show()",
        "artifact.contract()",
        "`references/examples/` — smoke tests and copyable starting points only",
    ]
    missing = [marker for marker in required_markers if marker not in text]
    assert missing == []


def test_analysis_skill_docs_do_not_use_stale_api_patterns() -> None:
    stale_patterns = {
        "segment_by": re.compile(r"\bsegment_by\b"),
        "module_level_list_metrics": re.compile(r"\bms\.list_metrics\("),
        "top_level_evidence_field": re.compile(
            r"\b(?:result|delta|frame)\."
            r"(?:artifact_id|evidence_status|blocking_issues|affordances|"
            r"confidence_scope|quality)\b"
        ),
    }
    failures: list[str] = []
    for path in ACTIVE_SKILL_DOCS:
        text = path.read_text()
        for name, pattern in stale_patterns.items():
            if pattern.search(text):
                failures.append(f"{path.relative_to(REPO_ROOT)}: {name}")
    assert failures == []
