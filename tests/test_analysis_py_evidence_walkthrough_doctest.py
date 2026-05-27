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


class _ResultView:
    """Expose target Surface 1 fields over today's meta-backed frames."""

    def __init__(self, frame: Any) -> None:
        self._frame = frame

    def __getattr__(self, name: str) -> Any:
        meta = getattr(self._frame, "meta", None)
        if meta is not None and hasattr(meta, name):
            return getattr(meta, name)
        return getattr(self._frame, name)


class _WalkthroughSession:
    """Target session-method facade backed by the current public API."""

    def __init__(self, project_root: Path) -> None:
        bootstrap_sales_project(project_root)
        con = ibis.duckdb.connect(":memory:")
        _seed(con)
        self._session = ap.session.attach.create(
            name="walkthrough",
            backends={"warehouse": lambda: con},
            use_datasources=False,
        )

    def observe(
        self,
        *,
        metric: ap.MetricRef,
        time: str,
        grain: str | None = None,
        segment_by: str | None = None,
    ) -> _ResultView:
        windows = {
            "this_week": {"start": "2026-05-01", "end": "2026-05-07", "grain": grain},
            "previous_week": {
                "start": "2026-04-24",
                "end": "2026-04-30",
                "grain": grain,
            },
        }
        window = {k: v for k, v in windows[time].items() if v is not None}
        fixture_dimension = "region" if segment_by == "country" else segment_by
        dimensions = [ap.DimensionRef(fixture_dimension)] if fixture_dimension is not None else None
        frame = ap.observe(metric, window=window, dimensions=dimensions, session=self._session)
        return _ResultView(frame)

    def compare(self, current: _ResultView, baseline: _ResultView) -> _ResultView:
        frame = ap.compare(current._frame, baseline._frame, session=self._session)
        return _ResultView(frame)

    def knowledge(self) -> Any:
        return self._session.knowledge()

    def run_followup(self, action: Any) -> Any:
        return self._session.run_followup(action)


def test_skill_walkthrough_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    code = _extract_walkthrough_block()
    namespace: dict[str, Any] = {
        "ap": ap,
        "_WalkthroughSession": _WalkthroughSession,
        "_project_root": tmp_path,
    }
    code = code.replace("import marivo.analysis_py as ap\n\n", "")
    code = code.replace("session = ap.session()", "session = _WalkthroughSession(_project_root)")
    exec(compile(code, str(SKILL_PATH), "exec"), namespace)
