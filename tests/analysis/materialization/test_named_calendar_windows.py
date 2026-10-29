"""Named window recipes consume the Session's frozen certified calendar scope."""

import shutil
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._temporal import TemporalSnapshotStore, certify_period_calendar_rows, semantic_grain
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import AnalysisError
from marivo.semantic.catalog import PeriodCalendarEntry, SemanticCatalog
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import (
    catalog_entry,
    catalog_value,
    fiscal_analysis_project_files,
    publish_fiscal_calendar_artifact,
    terminal_runs,
)
from tests.support.refs import as_ref


def _metric(catalog: SemanticCatalog, path: str) -> Deferred:
    metric = catalog_value(catalog, path, kind="metric")
    assert isinstance(metric, Deferred)
    return metric


def _calendar(catalog: SemanticCatalog) -> PeriodCalendarEntry:
    calendar = catalog_entry(catalog, "sales.fiscal", kind="period_calendar")
    assert isinstance(calendar, PeriodCalendarEntry)
    return calendar


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
week = ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="fiscal_week")
@ms.metric()
def weekly_avg():
    return gmv.per(week).mean()
@ms.metric()
def weekly_weighted():
    return gmv.per(week).weighted_mean(weight=gmv * 0 + 1)
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
    calendar_ref, total_ref, alias_ref = as_ref(calendar), as_ref(total), as_ref(alias)
    assert calendar_ref is not None and total_ref is not None and alias_ref is not None
    assert calendar_ref in catalog._state.dependencies[total_ref]
    assert total_ref in catalog._state.dependencies[alias_ref]
    before = catalog.readiness(refs=[calendar])
    assert before.status == "blocked"
    assert "period_calendar_artifact_missing" in before.render(max_output_bytes=None)
    publish_fiscal_calendar_artifact(catalog)
    assert catalog.readiness(refs=[calendar]).status != "blocked"


@pytest.mark.parametrize("name", ["weekly_avg", "weekly_weighted"])
def test_calendar_sample_dependency_requires_certification(
    calendar_window_project: SemanticCatalog, name: str
) -> None:
    catalog = calendar_window_project
    calendar = ms.ref.period_calendar("sales.fiscal")
    metric = ms.ref.metric(f"sales.{name}")
    calendar_ref, metric_ref = as_ref(calendar), as_ref(metric)
    assert calendar_ref is not None and metric_ref is not None
    assert calendar_ref in catalog._state.dependencies[metric_ref]
    before = catalog.readiness(refs=[metric])
    assert before.status == "blocked"
    assert "period_calendar_artifact_missing" in before.render(max_output_bytes=None)


def test_declared_and_adhoc_semantic_calendar_samples_bind_session_snapshot(
    calendar_window_project: SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_fiscal_calendar_artifact(calendar_window_project)
    session = mv.session.get_or_create("calendar-sample-lookup", timezone="UTC")
    calendar = _calendar(session.catalog)
    gmv = _metric(session.catalog, "sales.gmv")
    week = ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="fiscal_week")
    scope = session.time_scope(start="2026-01-01", end="2026-03-01")
    query = session.observe(
        {
            "declared": _metric(session.catalog, "sales.weekly_avg"),
            "weighted": _metric(session.catalog, "sales.weekly_weighted"),
            "adhoc": gmv.per(week).mean(),
            "bound": gmv.per(calendar.grain("fiscal_week")).mean(),
        },
        by=(scope.by(calendar.grain("fiscal_month")),),
    )
    with pytest.raises(CoreRuleError) as captured:
        session.observe(
            {"v": gmv.per(calendar.grain("fiscal_month")).mean()},
            by=(scope.by(calendar.grain("fiscal_week")),),
        )
    assert "nest" in (captured.value.expected or "")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("execution refreshed the frozen temporal scope")

    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", forbidden)
    rows = query.compute().to_pandas()
    # January has five fiscal weeks with totals [100, 0, 0, 20, 0];
    # February has four with [7, 0, 0, 0]. Empty weeks are samples.
    for name in ("declared", "weighted", "adhoc", "bound"):
        assert rows[name].tolist() == [24.0, 1.75]


@pytest.mark.parametrize("case", ["missing", "level", "timezone", "snapshot"])
def test_calendar_samples_validate_session_authority_before_business_reads(
    calendar_window_project: SemanticCatalog, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case != "missing":
        publish_fiscal_calendar_artifact(calendar_window_project)
    session = mv.session.get_or_create("calendar-sample-authority", timezone="UTC")
    grain = ms.calendar_grain(
        calendar=ms.ref.period_calendar("sales.fiscal"),
        level="unknown" if case == "level" else "fiscal_week",
    )
    if case == "snapshot":
        snapshot = session._sources().period_calendar_snapshots[0]
        foreign = certify_period_calendar_rows(
            calendar_ref=snapshot.calendar_ref,
            boundary_timezone="UTC",
            coverage=snapshot.coverage,
            columns=("day", "week"),
            retained_values=tuple(
                {"day": (snapshot.coverage[0] + timedelta(days=index)).isoformat(), "week": "other"}
                for index in range((snapshot.coverage[1] - snapshot.coverage[0]).days)
            ),
            date_column="day",
            levels={"fiscal_week": "week"},
        )
        grain = semantic_grain(
            calendar=snapshot.calendar_ref, level="fiscal_week", snapshot=foreign
        )
    gmv = _metric(session.catalog, "sales.gmv")
    scope = session.time_scope(
        start="2026-01-01",
        end="2026-02-01",
        timezone="Asia/Shanghai" if case == "timezone" else "UTC",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("calendar sample binding read a business source")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    with pytest.raises(CoreRuleError) as captured:
        session.observe({"v": gmv.per(grain).mean()}, by=(scope,))
    error = captured.value
    assert error.expected and error.received and error.repair
    assert not terminal_runs(session)


def test_calendar_sample_formula_discloses_public_semantic_grain(
    calendar_window_project: SemanticCatalog, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = catalog_entry(calendar_window_project, "sales.weekly_avg", kind="metric")
    entry.show(max_output_bytes=None)
    text = capsys.readouterr().out
    assert "Grain(calendar='period_calendar:sales.fiscal', level='fiscal_week'" in text
    assert "_SemanticGrain" not in text
    assert "_captured_snapshot" not in text


@pytest.mark.parametrize(
    ("end", "expected"),
    [("2026-02-01", 120), ("2026-02-02", 0), ("2026-02-04", 7), ("2026-03-01", 7)],
)
def test_named_and_anonymous_scalar_windows_preserve_reset_and_null_semantics(
    calendar_window_project: SemanticCatalog, end: str, expected: int | None
) -> None:
    publish_fiscal_calendar_artifact(calendar_window_project)
    session = mv.session.get_or_create("scalar-calendar", timezone="UTC")
    calendar = _calendar(session.catalog)
    anonymous = _metric(session.catalog, "sales.gmv").window(
        mv.to_date(grain=calendar.grain("fiscal_month"))
    )
    data = (
        session.observe(
            {
                "named": _metric(session.catalog, "sales.fiscal_total"),
                "anonymous": anonymous,
            },
            by=(session.time_scope(start="2026-01-20", end=end),),
        )
        .compute()
        .to_pandas()
    )
    for name in ("named", "anonymous"):
        actual = data[name].iloc[0]
        assert pd.isna(actual) if expected is None else actual == expected


def test_named_calendar_window_freezes_snapshot_before_execution(
    calendar_window_project: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = calendar_window_project
    publish_fiscal_calendar_artifact(catalog)
    session = mv.session.get_or_create("frozen-calendar", timezone="UTC")
    metric = _metric(session.catalog, "sales.fiscal_total")
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
    assert data["total"].tolist() == [120, 0, 0, 7]


@pytest.mark.release
@pytest.mark.parametrize("bucketed", [False, True])
def test_explicit_grain_rejects_a_different_certified_snapshot(
    calendar_window_project: SemanticCatalog, monkeypatch: pytest.MonkeyPatch, bucketed: bool
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
    session_a = mv.session.get_or_create("calendar-a", timezone="UTC")
    calendar_a = _calendar(session_a.catalog)
    grain_a = calendar_a.grain("fiscal_month")
    scope_a = session_a.time_scope(start="2026-01-20", end="2026-02-01")
    query_a = session_a.observe(
        {
            "named": _metric(session_a.catalog, "sales.fiscal_total"),
            "explicit": _metric(session_a.catalog, "sales.gmv").window(mv.to_date(grain=grain_a)),
        },
        by=(scope_a.by(mv.grain("day")) if bucketed else scope_a,),
    )
    original_fingerprint = _source_graph(query_a).fingerprint
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE calendar SET fiscal_month = CASE WHEN calendar_date < DATE '2026-01-15' THEN 'M1' ELSE 'M2' END"
        )
    catalog.preview(calendar_ref, scope=acquisition)
    session_b = mv.session.get_or_create("calendar-b", timezone="UTC")
    grain_b = _calendar(session_b.catalog).grain("fiscal_month")
    assert grain_a._snapshot is not None and grain_b._snapshot is not None
    assert grain_a._snapshot.snapshot_digest != grain_b._snapshot.snapshot_digest
    scope_b = session_b.time_scope(start="2026-01-20", end="2026-02-01")
    query_b = session_b.observe(
        {
            "named": _metric(session_b.catalog, "sales.fiscal_total"),
            "explicit": _metric(session_b.catalog, "sales.gmv").window(mv.to_date(grain=grain_b)),
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
            metric = _metric(session.catalog, "sales.gmv").window(mv.to_date(grain=foreign))
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
            assert data[name].iloc[-1] == expected


@pytest.mark.parametrize("status", ["missing", "stale", "invalid"])
def test_unusable_calendar_rejects_before_business_reads(
    calendar_window_project: SemanticCatalog, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", lambda *a, **kw: (status, None))
    session = mv.session.get_or_create("uncertified-calendar", timezone="UTC")
    metric = _metric(session.catalog, "sales.fiscal_total")

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
    session = mv.session.get_or_create("invalid-calendar", timezone="UTC")
    calendar = _calendar(session.catalog)
    grain = ms.calendar_grain(
        calendar=ms.ref.period_calendar(calendar.path),
        level="unknown" if case == "level" else "fiscal_month",
    )
    metric = _metric(session.catalog, "sales.gmv").window(mv.to_date(grain=grain))
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
