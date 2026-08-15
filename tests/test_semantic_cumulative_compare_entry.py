"""Issue #50 review: semantic cumulative compare entry typed-rejection surface.

Locks three findings from the MR !76 review:

1. P1 — bucketed (time-series/panel) cumulative compare with a semantic
   calendar query grain is typed-rejected at the compare entry (no bare
   ``invalid grain token`` ``ValueError``);
2. P2a — a scalar semantic-calendar cumulative delta projects ``blocked``
   attribute admission and its materialization error carries the
   semantic-decomposition location/repair (not the misleading missing-axes
   retry guidance);
3. P2b — the default ``window_bucket`` alignment rejects scalar semantic GTD
   compare with an explicit ``period_progress`` pointer.
"""

from __future__ import annotations

from datetime import date, timedelta

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import (
    AnalysisError,
    AttributionMaterializationError,
    SemanticCumulativeBucketCompareUnsupportedError,
)
from marivo.analysis.intents.attribute import _cumulative_anchor_evidence
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from tests.shared_fixtures import fiscal_analysis_project_files, fiscal_calendar_evidence


def _fiscal_session(semantic_project_factory, monkeypatch, name: str):
    """Build a certified fiscal-calendar session for cumulative compare probes."""
    project = semantic_project_factory(fiscal_analysis_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE calendar (calendar_date DATE, fiscal_week VARCHAR, fiscal_month VARCHAR)"
    )
    calendar_rows = []
    cursor = date(2026, 1, 1)
    while cursor < date(2026, 3, 1):
        month = "M1" if cursor.month == 1 else "M2"
        week = f"{month}-W{((cursor.day - 1) // 7) + 1}"
        calendar_rows.append((cursor.isoformat(), week, month))
        cursor += timedelta(days=1)
    backend.raw_sql(
        "INSERT INTO calendar VALUES "
        + ",".join(f"(DATE '{day}', '{week}', '{month}')" for day, week, month in calendar_rows)
    )
    backend.raw_sql("CREATE TABLE events (event_date DATE, amount DOUBLE, user_id INTEGER)")
    backend.raw_sql(
        "INSERT INTO events VALUES "
        "(DATE '2026-01-01', 10, 1), (DATE '2026-01-08', 20, 1), "
        "(DATE '2026-02-01', 30, 1), (DATE '2026-02-08', 40, 2)"
    )
    catalog = ms.SemanticCatalog(project)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    catalog.verify(calendar_ref)
    catalog.preview(calendar_ref, using=fiscal_calendar_evidence(project.workspace_dir))
    session = mv.session.get_or_create(
        name=name,
        backends={"warehouse": lambda: backend},
        report_timezone="UTC",
    )
    return session, session.catalog.period_calendars.get("sales.fiscal")


def test_compare_bucketed_semantic_grain_is_typed_rejected(semantic_project_factory, monkeypatch):
    session, calendar = _fiscal_session(semantic_project_factory, monkeypatch, "p1-bucketed")
    metric = session.catalog.require(ms.ref.metric("sales.fiscal_mtd")).ref
    semantic_week = calendar.grain("fiscal_week")

    current = observe(
        metric,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-01-29"),
        grain=semantic_week,
        session=session,
    )
    baseline = observe(
        metric,
        time_scope=mv.time_scope(start="2026-02-01", end="2026-03-01"),
        grain=semantic_week,
        session=session,
    )

    with pytest.raises(SemanticCumulativeBucketCompareUnsupportedError) as exc_info:
        compare(current, baseline, alignment=mv.period_progress(), session=session)

    error = exc_info.value
    assert error._context["kind"] == "SemanticCumulativeBucketCompareUnsupported"
    assert error._context["calendar_ref"] == "sales.fiscal"
    assert error._context["level"] == "fiscal_week"
    assert "period_progress" in error.hint


def test_scalar_semantic_delta_admission_is_blocked(semantic_project_factory, monkeypatch):
    session, _calendar = _fiscal_session(semantic_project_factory, monkeypatch, "p2a-scalar")
    metric = session.catalog.require(ms.ref.metric("sales.fiscal_mtd")).ref

    # Two scalar fiscal GTD frames with equal elapsed spans so period_progress
    # can pair them (full fiscal months M1=31d / M2=28d would not).
    current = session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-01-08"),
    )
    baseline = session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-02-01", end="2026-02-08"),
    )

    delta = compare(current, baseline, alignment=mv.period_progress(), session=session)

    admission = delta.contract().attribute_admission
    assert admission.status == "blocked"
    assert admission.blocker == "semantic_grain_decomposition_unsupported"

    with pytest.raises(AttributionMaterializationError) as exc_info:
        _cumulative_anchor_evidence(delta)

    error = exc_info.value
    assert error._context["recoverability_status"] == "semantic_grain_decomposition_unsupported"
    assert error.location is not None and "semantic" in error.location
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert "missing-axis" not in error.location


def test_scalar_semantic_default_alignment_points_to_period_progress(
    semantic_project_factory,
    monkeypatch,
):
    session, calendar = _fiscal_session(semantic_project_factory, monkeypatch, "p2b-default")
    metric = session.catalog.require(ms.ref.metric("sales.fiscal_mtd")).ref

    current = session.observe(metric, time_scope=calendar.period("fiscal_month", "M1"))
    baseline = session.observe(metric, time_scope=calendar.period("fiscal_month", "M2"))

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    error = exc_info.value
    assert "period_progress" in str(error).lower()
