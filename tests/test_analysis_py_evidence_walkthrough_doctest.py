"""Verify the SKILL.md walkthrough block executes against the live API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ibis
import pytest

import marivo.analysis_py as ap
import marivo.analysis_py.session.attach as session_attach
from tests.conftest import bootstrap_sales_project

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "marivo-skill/marivo-py-analysis/SKILL.md"
ACTIVE_SKILL_DOCS = [
    SKILL_PATH,
    REPO_ROOT / "marivo-skill/marivo-py-analysis/references/cheatsheet.md",
    REPO_ROOT / "marivo-skill/marivo-py-analysis/references/pitfalls.md",
]


def _extract_walkthrough_block() -> str:
    """Pull the python code block under the '## Walkthrough' heading."""
    text = SKILL_PATH.read_text()
    match = re.search(
        r"## Walkthrough.*?```python\n(.*?)```",
        text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("Walkthrough python block not found in SKILL.md")
    return match.group(1)


def _seed(con: Any) -> None:
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-05-01', 100.0, 'us', 1),"
        "(2, DATE '2026-05-02', 120.0, 'jp', 2),"
        "(3, DATE '2026-04-24', 90.0, 'us', 1),"
        "(4, DATE '2026-04-25', 80.0, 'jp', 2)"
    )


def _create_walkthrough_session(project_root: Path) -> ap.session.attach.Session:
    bootstrap_sales_project(project_root)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return ap.session.attach.create(
        name="walkthrough",
        backends={"warehouse": lambda: con},
        use_datasources=False,
    )


def test_skill_walkthrough_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    code = _extract_walkthrough_block()
    namespace: dict[str, Any] = {
        "ap": ap,
        "_create_walkthrough_session": _create_walkthrough_session,
        "_project_root": tmp_path,
    }
    code = code.replace("import marivo.analysis_py as ap\n\n", "")
    code = code.replace(
        'session = ap.session.get_or_create(name="sales_weekly_revenue")',
        "session = _create_walkthrough_session(_project_root)",
    )
    exec(compile(code, str(SKILL_PATH), "exec"), namespace)


def test_analysis_skill_docs_do_not_use_stale_api_patterns() -> None:
    stale_patterns = {
        "segment_by": re.compile(r"\bsegment_by\b"),
        "module_level_list_metrics": re.compile(r"\bms\.list_metrics\("),
        "top_level_evidence_field": re.compile(
            r"\b(?:result|delta|frame)\."
            r"(?:artifact_id|evidence_status|blocking_issues|recommended_followups|"
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
