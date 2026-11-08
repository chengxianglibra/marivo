"""Ordinary report domains follow the Session; certified domains keep their authority."""

from datetime import date, datetime, timedelta, timezone
from inspect import signature

import pytest

import marivo.analysis as mv
from marivo._definition.population_parameters import interval
from marivo._definition.types import IntervalCoordinate
from marivo._temporal import (
    CertifiedTimeAuthority,
    TimeScope,
    certify_period_calendar,
    certify_temporal_set,
    semantic_grain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarAxisSchema,
    FrameIntervalAxisSchema,
)
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.refs import ref
from tests.shared_fixtures import DslCaseFactory, catalog_get, time_population


@pytest.fixture(params=("calendar", "occurrence"))
def certified_scope(request: pytest.FixtureRequest) -> TimeScope:
    start, end = date(2026, 8, 1), date(2026, 9, 1)
    if request.param == "calendar":
        snapshot = certify_period_calendar(
            calendar_ref=ref.period_calendar("sales.report_month"),
            boundary_timezone="Asia/Shanghai",
            coverage=(start, end),
            levels={"month": "month"},
            rows=tuple(
                {"date": start + timedelta(days=index), "month": "august"} for index in range(31)
            ),
        )
        return snapshot.period_scope("month", "august")
    return certify_temporal_set(
        temporal_set_ref=ref.temporal_set("sales.campaign"),
        boundary_timezone="Asia/Shanghai",
        coverage=(start, end),
        rows=({"id": "august", "start": start, "end": end},),
        occurrence_id="id",
        start="start",
        end="end",
    ).occurrence_scope("august")


def test_scope_uses_saved_session_timezone_and_preserves_aware_bounds(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    analysis_dsl_case_factory("j2")
    session = mv.session.get_or_create("report-shanghai", timezone="Asia/Shanghai")
    assert tuple(signature(session.time_scope).parameters) == ("start", "end")
    scope = session.time_scope(start="2026-08-01", end="2026-08-02T00:00Z")
    assert scope.boundary_timezone == "Asia/Shanghai"
    assert scope.start == datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    assert scope.end == datetime(2026, 8, 2, tzinfo=timezone.utc)
    restored = mv.session.resume(session.id)
    assert restored.time_scope(start="2026-08-01", end="2026-08-02T00:00Z") == scope


@pytest.mark.parametrize(
    "form", ("scope", "axis", "shift", "interval_population", "axis_population")
)
def test_foreign_ordinary_report_domains_reject_before_source_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    foreign = mv.session.get_or_create("foreign-report", timezone="Asia/Shanghai")
    scope = foreign.time_scope(start="2026-08-01", end="2026-09-01")
    axis = scope.by(mv.grain("month"))
    metric = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")

    def forbidden(owner: SourceSession, issued: CompiledRead) -> object:
        pytest.fail(f"report timezone rejection reached source read {issued.purpose}")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(AnalysisError) as caught:
        if form == "scope":
            session.observe({"v": metric}, where=scope)
        elif form == "axis":
            session.observe({"v": metric}, by=(axis,))
        elif form == "shift":
            session.observe({"v": metric}, by=(axis.shift(mv.offset(periods=-1)),))
        else:
            session.observe(
                {"v": metric}, by=time_population(scope if form == "interval_population" else axis)
            )
    assert "UTC" in (caught.value.expected or "")
    assert "Asia/Shanghai" in (caught.value.received or "")
    assert caught.value.repair is not None
    assert "Rebuild" in caught.value.repair.action


def test_same_timezone_foreign_scope_and_normalized_alias_are_accepted(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    foreign = mv.session.get_or_create("same-zone-report", timezone="Etc/UTC")
    metric = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    foreign_scope = foreign.time_scope(start="2026-08-01", end="2026-09-01")
    local_scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    actual = session.observe({"v": metric}, where=foreign_scope).compute().to_pandas()
    expected = session.observe({"v": metric}, where=local_scope).compute().to_pandas()
    assert actual.equals(expected)


@pytest.mark.parametrize(
    "form", ("scope", "axis", "shift", "interval_population", "axis_population")
)
def test_certified_foreign_domain_keeps_authority_through_observation_and_recovery(
    analysis_dsl_case_factory: DslCaseFactory,
    certified_scope: TimeScope,
    form: str,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    axis = certified_scope.by(mv.grain("month"))
    assert isinstance(axis._binding.time_authority, CertifiedTimeAuthority)
    parameter_axis = interval(certified_scope)._definition.axes[0]
    assert isinstance(parameter_axis, IntervalCoordinate)
    assert isinstance(parameter_axis.time_authority, CertifiedTimeAuthority)
    one = mv.coordinates(by=(), keys=((),)).count()
    if form == "scope":
        pending = session.observe({"v": one}, where=certified_scope)
    elif form in ("axis", "shift"):
        selected = axis.shift(mv.offset(periods=-1)) if form == "shift" else axis
        assert selected._binding.time_authority == axis._binding.time_authority
        pending = session.observe({"v": one}, by=(selected,))
    else:
        pending = session.observe(
            {"v": one},
            by=time_population(certified_scope if form == "interval_population" else axis),
        )
    saved = pending.persist()
    rows = saved.to_pandas()
    assert rows.v.tolist() == [1]
    if form != "scope":
        saved_axis = saved.schema.axes[0]
        assert isinstance(saved_axis, (FrameCalendarAxisSchema, FrameIntervalAxisSchema))
        assert saved_axis.timezone == "Asia/Shanghai"
    assert saved.artifact_id is not None
    assert mv.session.resume(session.id).persisted_frame(saved.artifact_id).to_pandas().equals(rows)


def test_certified_grain_owns_axis_timezone_for_an_ordinary_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    foreign = mv.session.get_or_create("calendar-author", timezone="Asia/Shanghai")
    start, end = date(2026, 8, 1), date(2026, 8, 3)
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.short_period"),
        boundary_timezone="Asia/Shanghai",
        coverage=(start, end),
        levels={"period": "period"},
        rows=({"date": start, "period": "p"}, {"date": start + timedelta(days=1), "period": "p"}),
    )
    grain = semantic_grain(calendar=snapshot.calendar_ref, level="period", snapshot=snapshot)
    axis = foreign.time_scope(start=start, end=end).by(grain)
    one = mv.coordinates(by=(), keys=((),)).count()
    result = session.observe({"v": one}, by=(axis,)).compute()
    assert result.to_pandas().v.tolist() == [1]
