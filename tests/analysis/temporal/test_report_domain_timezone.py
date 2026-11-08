"""Ordinary report domains follow the Session; certified domains keep their authority."""

from datetime import datetime, timezone
from inspect import signature

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCaseFactory, catalog_get, time_population


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
