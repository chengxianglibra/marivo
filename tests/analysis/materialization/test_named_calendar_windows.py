"""Named window recipes consume the Session's frozen certified calendar scope."""

import shutil
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._temporal import TemporalSnapshotStore
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.errors import AnalysisError
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import (
    fiscal_analysis_project_files,
    publish_fiscal_calendar_artifact,
    terminal_runs,
)


def _source_graph(frame: mv.AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, SourceFrameBinding)
    return frame._backing.root.graph


@pytest.fixture
def calendar_window_project(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SemanticCatalog:
    database = tmp_path / "windows.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE events (user_id BIGINT, event_date DATE, amount BIGINT)")
        connection.execute(
            "INSERT INTO events VALUES (1, '2026-01-05', 100), (2, '2026-01-25', 20), (3, '2026-02-03', 7)"
        )
    files = fiscal_analysis_project_files()
    files["datasources/warehouse.py"] = (
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    files["sales/metrics.py"] = (
        files["sales/metrics.py"]
        .replace("source=md.table('events')", "source=md.table('events'), primary_key=['user_id']")
        .replace("column='event_date')", "column='event_date', is_default=True)")
        + """
import marivo.analysis as mv
month = ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="fiscal_month")
rule = mv.to_date(grain=month)
@ms.metric()
def fiscal_total():
    return gmv.window(rule)
@ms.metric()
def fiscal_alias():
    return fiscal_total + 0
"""
    )
    catalog = SemanticCatalog(semantic_project_factory(files))
    monkeypatch.chdir(tmp_path)
    return catalog


def test_calendar_dependency_closure_keeps_explicit_certification(
    calendar_window_project: SemanticCatalog,
) -> None:
    catalog = calendar_window_project
    calendar = ms.ref.period_calendar("sales.fiscal")
    total = ms.ref.metric("sales.fiscal_total")
    alias = ms.ref.metric("sales.fiscal_alias")
    assert calendar in catalog._state.dependencies[total]
    assert calendar in catalog._state.dependencies[alias]
    before = catalog.readiness(refs=[calendar])
    assert before.status == "blocked"
    assert "period_calendar_artifact_missing" in before.render(max_output_bytes=None)
    publish_fiscal_calendar_artifact(catalog)
    assert catalog.readiness(refs=[calendar]).status != "blocked"


@pytest.mark.parametrize(
    ("end", "expected"),
    [("2026-02-01", 120), ("2026-02-02", None), ("2026-02-04", 7), ("2026-03-01", 7)],
)
def test_named_and_anonymous_scalar_windows_preserve_reset_and_null_semantics(
    calendar_window_project: SemanticCatalog, end: str, expected: int | None
) -> None:
    publish_fiscal_calendar_artifact(calendar_window_project)
    session = mv.session.create("scalar-calendar", timezone="UTC")
    calendar = session.catalog.period_calendars.get("sales.fiscal")
    anonymous = session.catalog.metrics.get("sales.gmv").window(
        mv.to_date(grain=calendar.grain("fiscal_month"))
    )
    data = (
        session.observe(
            {"named": session.catalog.metrics.get("sales.fiscal_total"), "anonymous": anonymous},
            by=(session.time_scope(start="2026-01-20", end=end),),
        )
        .compute()
        .to_pandas()
    )
    for name in ("named", "anonymous"):
        actual = data[("value", name)].iloc[0]
        assert pd.isna(actual) if expected is None else actual == expected


def test_named_calendar_window_freezes_snapshot_before_execution(
    calendar_window_project: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = calendar_window_project
    publish_fiscal_calendar_artifact(catalog)
    session = mv.session.create("frozen-calendar", timezone="UTC")
    metric = session.catalog.metrics.get("sales.fiscal_total")
    query = session.observe(
        {"total": metric},
        by=(session.time_scope(start="2026-01-31", end="2026-02-04").by(mv.grain("day")),),
    )
    binding = _source_graph(query).signature.observation
    assert binding is not None
    snapshot = session._sources().period_calendar_snapshots[0]
    assert {item.window_calendar_digest for item in binding.contributions} == {
        snapshot.snapshot_digest
    }
    original = _source_graph(query).fingerprint
    shutil.rmtree(TemporalSnapshotStore(catalog.workspace_dir)._directory(snapshot.calendar_ref))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("execution refreshed the frozen temporal scope")

    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", forbidden)
    assert _source_graph(query).fingerprint == original
    data = query.compute().to_pandas()
    assert data[("value", "total")].isna().tolist() == [False, True, True, False]
    assert data[("value", "total")].dropna().tolist() == [120, 7]


@pytest.mark.parametrize("bucketed", [False, True])
def test_explicit_grain_rejects_a_different_certified_snapshot(
    calendar_window_project: SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
    bucketed: bool,
) -> None:
    catalog = calendar_window_project
    database = catalog.workspace_dir / "windows.duckdb"
    rows: list[tuple[date, str, str]] = []
    cursor = date(2026, 1, 1)
    while cursor < date(2026, 3, 1):
        month = f"M{cursor.month}"
        rows.append((cursor, f"{month}-W{(cursor.day - 1) // 7 + 1}", month))
        cursor += timedelta(days=1)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE calendar (calendar_date DATE, fiscal_week VARCHAR, fiscal_month VARCHAR)"
        )
        connection.executemany("INSERT INTO calendar VALUES (?, ?, ?)", rows)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    acquisition = md.unpruned(max_rows=100, timeout_seconds=10)
    catalog.preview(calendar_ref, scope=acquisition)
    session_a = mv.session.create("calendar-a", timezone="UTC")
    calendar_a = session_a.catalog.period_calendars.get(calendar_ref.path)
    grain_a = calendar_a.grain("fiscal_month")
    scope_a = session_a.time_scope(start="2026-01-20", end="2026-02-01")
    query_a = session_a.observe(
        {
            "named": session_a.catalog.metrics.get("sales.fiscal_total"),
            "explicit": session_a.catalog.metrics.get("sales.gmv").window(
                mv.to_date(grain=grain_a)
            ),
        },
        by=(scope_a.by(mv.grain("day")) if bucketed else scope_a,),
    )
    original_fingerprint = _source_graph(query_a).fingerprint

    # Certify changed source rows under the same definition, not a fabricated digest.
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE calendar SET fiscal_month = CASE WHEN calendar_date < DATE '2026-01-15' THEN 'M1' ELSE 'M2' END"
        )
    catalog.preview(calendar_ref, scope=acquisition)
    session_b = mv.session.create("calendar-b", timezone="UTC")
    grain_b = session_b.catalog.period_calendars.get(calendar_ref.path).grain("fiscal_month")
    assert grain_a._snapshot is not None and grain_b._snapshot is not None
    assert grain_a._snapshot.snapshot_digest != grain_b._snapshot.snapshot_digest
    scope_b = session_b.time_scope(start="2026-01-20", end="2026-02-01")
    query_b = session_b.observe(
        {
            "named": session_b.catalog.metrics.get("sales.fiscal_total"),
            "explicit": session_b.catalog.metrics.get("sales.gmv").window(
                mv.to_date(grain=grain_b)
            ),
        },
        by=(scope_b.by(mv.grain("day")) if bucketed else scope_b,),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("conflicting calendar binding read a business source")

    with monkeypatch.context() as patch:
        patch.setattr(duckdb, "connect", forbidden)
        for session, foreign, scope in (
            (session_a, grain_b, scope_a),
            (session_b, grain_a, scope_b),
        ):
            metric = session.catalog.metrics.get("sales.gmv").window(mv.to_date(grain=foreign))
            with pytest.raises(AnalysisError, match="same certified snapshot"):
                session.observe(
                    {"total": metric}, by=(scope.by(mv.grain("day")) if bucketed else scope,)
                )
            assert not terminal_runs(session)

    assert _source_graph(query_a).fingerprint == original_fingerprint
    for query, expected, snapshot in (
        (query_a, 120, grain_a._snapshot),
        (query_b, 20, grain_b._snapshot),
    ):
        binding = _source_graph(query).signature.observation
        assert binding is not None
        assert {item.window_calendar_digest for item in binding.contributions} == {
            snapshot.snapshot_digest
        }
        data = query.compute().to_pandas()
        for name in ("named", "explicit"):
            assert data[("value", name)].iloc[-1] == expected


@pytest.mark.parametrize("status", ["missing", "stale", "invalid"])
def test_unusable_calendar_rejects_before_business_reads(
    calendar_window_project: SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", lambda *a, **kw: (status, None))
    session = mv.session.create("uncertified-calendar", timezone="UTC")
    metric = session.catalog.metrics.get("sales.fiscal_total")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("binding read a business source")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    with pytest.raises(AnalysisError, match="current certified calendar"):
        session.observe(
            {"total": metric}, by=(session.time_scope(start="2026-01-20", end="2026-02-01"),)
        )
    assert not terminal_runs(session)


@pytest.mark.parametrize("case", ["level", "timezone", "coverage", "containment"])
def test_calendar_window_validates_consumed_authority(
    calendar_window_project: SemanticCatalog, case: str
) -> None:
    publish_fiscal_calendar_artifact(calendar_window_project)
    session = mv.session.create("invalid-calendar", timezone="UTC")
    calendar = session.catalog.period_calendars.get("sales.fiscal")
    grain = ms.calendar_grain(
        calendar=calendar.ref, level="unknown" if case == "level" else "fiscal_month"
    )
    metric = session.catalog.metrics.get("sales.gmv").window(mv.to_date(grain=grain))
    scope = session.time_scope(
        start="2026-01-20",
        end="2026-04-01" if case == "coverage" else "2026-02-04",
        timezone="Asia/Shanghai" if case == "timezone" else "UTC",
    )
    with pytest.raises(AnalysisError):
        session.observe(
            {"total": metric}, by=(scope.by(mv.grain("week")) if case == "containment" else scope,)
        )
    assert not terminal_runs(session)
