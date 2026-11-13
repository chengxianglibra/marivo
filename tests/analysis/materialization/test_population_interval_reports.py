"""Interval report cardinality is decided by checked complete output keys."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic._expression_values import NumericMetric
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.fixture
def report_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> mv.Session:
    database = tmp_path / "intervals.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute("CREATE TABLE members(id BIGINT)")
        conn.execute("INSERT INTO members VALUES (1),(2)")
        conn.execute("CREATE TABLE events(id BIGINT, day DATE, amount BIGINT)")
        conn.execute(
            "INSERT INTO events VALUES (1,'2026-08-01',10),(2,'2026-08-02',20),(3,'2026-08-03',40)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource('warehouse')
members = ms.entity(name='members', datasource=warehouse, source=md.table('members'), primary_key=['id'])
events = ms.entity(name='events', datasource=warehouse, source=md.table('events'), primary_key=['id'])
day = ms.time_dimension_column(name='day', entity=events, column='day')
amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("interval reports", timezone="UTC")


def _intervals(session: mv.Session) -> tuple[mv.Population, mv.Population, mv.Population]:
    unit = mv.coordinates(by=(), keys=((),))
    first = mv.product(unit, session.time_scope(start="2026-08-01", end="2026-08-02"))
    second = mv.product(unit, session.time_scope(start="2026-08-02", end="2026-08-03"))
    union = first.union(second)
    assert isinstance(union, mv.Population)
    return first, second, union


def _metrics(session: mv.Session) -> NumericMetric:
    day = catalog_value(session.catalog, "sales.events.day", kind="time_dimension")
    amount = catalog_value(session.catalog, "sales.events.amount", kind="measure")
    return amount.sum(time=day)


@pytest.mark.parametrize("fixed", (False, True))
def test_report_resolves_nonzero_ordinal_once_before_metric(
    report_session: mv.Session, monkeypatch: pytest.MonkeyPatch, fixed: bool
) -> None:
    session = report_session
    _, second, both = _intervals(session)
    members = catalog_value(session.catalog, "sales.members", kind="entity")
    selected = session.members(members)
    if fixed:
        frame = session.frame(selected).compute()
        selected = session.population_from(frame, on=((frame.axes[0], members),))
    joint = mv.product(selected, both).where(mv.in_population(second))
    assert isinstance(joint, mv.Population)
    domain = joint.project(by=(joint.axes[1],))
    assert isinstance(domain, mv.Population)
    trace = capture_source(monkeypatch)
    metric = _metrics(session)
    report = session.observe({"n": domain.count(), "total": metric}, by=domain)
    assert trace.native_sql == []
    result = report.persist()
    assert result.to_pandas()["n"].tolist() == [1]
    assert result.to_pandas()["total"].tolist() == [20]
    assert any('"events"' in sql.lower() for sql in trace.native_sql)
    if fixed:
        assert all('"members"' not in sql.lower() for sql in trace.native_sql)
    else:
        assert sum('"members"' in sql.lower() for sql in trace.native_sql) == 1
        assert '"members"' in trace.native_sql[0].lower()
        assert '"events"' not in trace.native_sql[0].lower()
    assert str(result.to_pandas().iloc[0, 0]).startswith("2026-08-02")
    actual_periods = tuple(
        scope.period
        for read in result.source_reads
        for scope in read.scopes
        if scope.source == "sales.events"
    )
    assert len(actual_periods) == 1 and actual_periods[0] is not None
    assert actual_periods[0].start == session.instant("2026-08-02")
    assert actual_periods[0].end == session.instant("2026-08-03")


@pytest.mark.parametrize("selection", ("none", "many"))
def test_report_rejects_only_actual_multiple_intervals_before_metric(
    report_session: mv.Session, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    session = report_session
    first, second, both = _intervals(session)
    members = catalog_value(session.catalog, "sales.members", kind="entity")
    domain = mv.product(session.members(members), both)
    if selection == "none":
        empty = first.intersection(second)
        assert isinstance(empty, mv.Population)
        restricted = domain.where(mv.in_population(empty))
        assert isinstance(restricted, mv.Population)
        domain = restricted
    projected = domain.project(by=(domain.axes[1],))
    assert isinstance(projected, mv.Population)

    execute = SourceSession.execute
    reads = []

    def guard(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append(read.sql)
        assert '"events"' not in read.sql.lower()
        return execute(source, read)

    monkeypatch.setattr(SourceSession, "execute", guard)
    report = session.observe({"total": _metrics(session)}, by=projected)
    assert reads == []
    if selection == "many":
        with pytest.raises(AnalysisError, match="at most one distinct report interval"):
            report.compute()
    else:
        assert report.compute().to_pandas().empty
    assert len(reads) == 1


def test_literal_multiple_intervals_are_rejected_during_capture(
    report_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, both = _intervals(report_session)
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="at most one distinct report interval"):
        report_session.observe({"total": _metrics(report_session)}, by=both)
    assert trace.native_sql == []


def test_domain_identity_check_precedes_multiple_interval_and_fact_reads(
    report_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = report_session
    _, _, intervals = _intervals(session)
    members = catalog_value(session.catalog, "sales.members", kind="entity")
    with duckdb.connect(str(session.project_root / "intervals.duckdb")) as conn:
        conn.execute("INSERT INTO members VALUES (NULL)")
    joint = mv.product(session.members(members), intervals)
    domain = joint.project(by=(joint.axes[1],))
    assert isinstance(domain, mv.Population)
    trace = capture_source(monkeypatch)
    pending = session.observe({"total": _metrics(session)}, by=domain)
    with pytest.raises(AnalysisError, match="non-null complete identity keys"):
        pending.compute()
    assert len(trace.native_sql) == 1
    assert '"events"' not in trace.native_sql[0].lower()


@pytest.mark.parametrize("dynamic", (False, True))
def test_window_uses_the_selected_nonzero_report_ordinal(
    report_session: mv.Session,
    dynamic: bool,
) -> None:
    session = report_session
    _, second, both = _intervals(session)
    domain = both.where(mv.in_population(second))
    assert isinstance(domain, mv.Population)
    if dynamic:
        members = catalog_value(session.catalog, "sales.members", kind="entity")
        joined = mv.product(session.members(members), domain)
        domain = joined.project(by=(joined.axes[1],))
        assert isinstance(domain, mv.Population)
    metric = _metrics(session).window(mv.all_history())
    result = session.observe({"total": metric}, by=domain).compute().to_pandas()
    assert result["total"].tolist() == [30]
    assert str(result.iloc[0, 0]).startswith("2026-08-02")


def test_sample_windows_reuse_checked_interval_keys(
    report_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = report_session
    _, second, both = _intervals(session)
    members = catalog_value(session.catalog, "sales.members", kind="entity")
    selected = mv.product(session.members(members), both).where(mv.in_population(second))
    assert isinstance(selected, mv.Population)
    domain = selected.project(by=(selected.axes[1],))
    assert isinstance(domain, mv.Population)
    metric = _metrics(session).window(mv.all_history()).per(mv.grain("day")).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"total": metric}, by=domain).compute().to_pandas()
    assert result["total"].tolist() == [30]
    assert sum('"members"' in sql.lower() for sql in trace.native_sql) == 1
    assert '"members"' in trace.native_sql[0].lower()
    assert '"events"' not in trace.native_sql[0].lower()
