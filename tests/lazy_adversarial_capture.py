"""Opt-in post-test Store observations, outside fault and contention timing.

Load with ``-p tests.lazy_adversarial_capture`` and MARIVO_SLICE9C_EVIDENCE_DIR.
Capture only identities, lifecycle, counts and digests, never payloads or SQL.
Write only after fixture teardown restores test-owned I/O monkeypatches.
Assertions remain owned by each test; this observer never reconciles a Store.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Generator
from contextlib import closing
from pathlib import Path

import pytest

_PROJECTIONS = {
    "runs": "SELECT r.run_ref, r.session_ref, r.admitted_at, t.outcome, t.output_artifact_ref FROM analysis_action_runs r LEFT JOIN analysis_action_run_terminals t USING(run_ref) ORDER BY r.run_ref",
    "artifacts": "SELECT artifact_ref, session_ref, committed_at FROM dataset_artifacts ORDER BY artifact_ref",
    "evidence": "SELECT artifact_ref, evidence_digest, finding_count, finding_set_digest FROM dataset_evidence ORDER BY artifact_ref",
    "counts": "SELECT (SELECT count(*) FROM analysis_action_runs) AS runs, (SELECT count(*) FROM dataset_artifacts) AS artifacts, (SELECT count(*) FROM dataset_evidence) AS evidence, (SELECT count(*) FROM findings) AS findings, (SELECT count(*) FROM action_resource_journal) AS obligations",
}
_REPORTS: dict[str, list[dict[str, object]]] = {}
_PROJECTS: dict[str, Path] = {}


def _store(path: Path) -> dict[str, object]:
    try:
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            return {
                key: [dict(row) for row in connection.execute(sql)]
                for key, sql in _PROJECTIONS.items()
            }
    except sqlite3.Error:
        return {"unavailable": "Store absent, locked, incompatible or deliberately damaged"}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    destination = os.environ.get("MARIVO_SLICE9C_EVIDENCE_DIR")
    if destination is None:
        return report
    _REPORTS.setdefault(item.nodeid, []).append(
        {
            "nodeid": item.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "duration_seconds": report.duration,
            "candidate": os.environ.get("MARIVO_SLICE9C_CANDIDATE"),
        }
    )
    if isinstance(item, pytest.Function):
        project = item.funcargs.get("tmp_path")
        if isinstance(project, Path):
            _PROJECTS[item.nodeid] = project
    if report.when != "teardown":
        return report
    retained_project = _PROJECTS.pop(item.nodeid, None)
    stores = (
        {}
        if retained_project is None
        else {
            str(path.relative_to(retained_project)): _store(path)
            for path in sorted(retained_project.rglob("session_store.db"))
        }
    )
    directory = Path(destination) / "tests"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(item.nodeid.encode()).hexdigest()[:20]
    for result in _REPORTS.pop(item.nodeid):
        result.update(
            observation="After fixture teardown; assertions own transitions and reads",
            stores=stores if result["phase"] == "call" else {},
        )
        (directory / f"{key}-{result['phase']}.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    return report
