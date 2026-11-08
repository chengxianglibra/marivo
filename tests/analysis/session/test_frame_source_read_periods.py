"""Read periods retain original endpoints rather than the current result domain."""

from datetime import datetime, timezone

import pytest

import marivo.analysis as mv
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, catalog_get, catalog_value
from tests.support.source_trace import capture_source


def test_raw_read_has_no_captured_period(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="measure")
    orders = session.members(catalog_get(session.catalog, "entity:sales.order", kind="entity"))
    trace = capture_source(monkeypatch)
    saved = session.read(orders, {"value": amount}).persist()
    assert len(saved.to_pandas()) == len(analysis_dsl_rows("j2").orders)
    assert len(saved.source_reads) == len(trace.returned_rows) == 1
    assert saved.source_reads[0].returned_rows == trace.returned_rows[0]
    assert saved.source_reads[0].scopes[0].period is None


@pytest.mark.parametrize("all_history", (False, True))
def test_captured_period_keeps_utc_instants_and_original_timezone(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    all_history: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = mv.session.get_or_create("report-calendar", timezone="Asia/Shanghai")
    revenue = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    metric = revenue.window(mv.all_history()) if all_history else revenue
    scope_scope = session.time_scope(start="2026-08-01", end="2026-08-03")
    scope = scope_scope.by(mv.grain("day"))
    trace = capture_source(monkeypatch)
    saved = session.observe({"value": metric}, by=(scope_scope.by(mv.grain("day")),)).persist()
    assert len(saved.to_pandas()) == (2 if all_history else 1)
    expected_count = 2 if all_history else 1
    assert len(saved.source_reads) == expected_count
    assert len(trace.native_sql) == expected_count + 1
    periods = [scope.period for read in saved.source_reads for scope in read.scopes]
    assert all(period is not None for period in periods)
    assert {
        (period.start, period.end, period.timezone) for period in periods if period is not None
    } == (
        {
            (None, datetime(2026, 8, 1, 16, tzinfo=timezone.utc), "Asia/Shanghai"),
            (None, datetime(2026, 8, 2, 16, tzinfo=timezone.utc), "Asia/Shanghai"),
        }
        if all_history
        else {
            (
                datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
                datetime(2026, 8, 2, 16, tzinfo=timezone.utc),
                "Asia/Shanghai",
            )
        }
    )
    filtered = saved.where(saved["value"] > 0).persist()
    assert filtered.source_reads == saved.source_reads
    assert len(trace.native_sql) == expected_count + 1


def test_combined_endpoints_do_not_expand_distinct_source_periods(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    current = session.observe(
        {"value": metric},
        where=session.time_scope(start="2026-09-01", end="2026-10-01"),
    )
    prior = session.observe(
        {"value": metric},
        where=session.time_scope(start="2026-08-01T00:00+08:00", end="2026-09-01T00:00+08:00"),
    )
    trace = capture_source(monkeypatch)
    saved = current.align(prior).persist()
    periods = {
        (scope.period.start, scope.period.end, scope.period.timezone)
        for read in saved.source_reads
        for scope in read.scopes
        if scope.period is not None
    }
    assert periods == {
        (
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            "UTC",
        ),
        (
            datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 16, tzinfo=timezone.utc),
            "UTC",
        ),
    }
    assert [read.returned_rows for read in saved.source_reads] == trace.returned_rows
