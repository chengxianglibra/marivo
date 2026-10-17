"""Current Query calendar correspondence and endpoint recovery."""

from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
def test_public_ordinal_retains_original_positions_after_selection(
    analysis_dsl_case_factory: DslCaseFactory,
    lengths: tuple[int, int],
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, lengths[0])
    _, baseline = period(case.session, 7, lengths[1])
    selected = current.where(current["value"] > 0)
    comparison = selected.compare(baseline, match=mv.ordinal(), how="outer")
    result = comparison.execute()
    frame = result.to_pandas()
    assert frame[("presence", ("current",))].sum() == 1
    assert frame[("presence", ("baseline",))].sum() == lengths[1] * 4
    assert frame[("value", "value.absolute")].dropna().tolist() == [1]
    projection = next(
        p for p in frame.attrs["marivo"]["axes"][0]["projections"] if p["path"] == ("current",)
    )
    assert [row[0] for row in projection["values"] if row[0] is not None] == [0]
    pd.testing.assert_frame_equal(case.session.artifact(result.id).to_pandas(), frame)


@pytest.mark.parametrize("kind", ["ordinal", "period_offset", "previous_year"])
def test_public_period_correspondence_preserves_independent_source_oracle(
    analysis_dsl_case_factory: DslCaseFactory,
    kind: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("a", "A", "web", "paid", "2026-08-01T12:00:00Z", 30),
                ("b", "B", "web", "paid", "2026-08-02T12:00:00Z", 15),
                ("c", "A", "web", "paid", "2025-08-01T12:00:00Z", 10),
            ],
        )
    metric = session.catalog.metrics.get("sales.revenue")
    members = session.members(ms.ref.entity("sales.customer"))
    current_axis = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    if kind == "previous_year":
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.previous_year()
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    elif kind == "period_offset":
        baseline_axis = session.time_scope(start="2026-07-31", end="2026-08-02").by(mv.grain("day"))
        match = mv.period_offset(-1)
        expected = [30, 0, 0, 0, -30, 15, 0, 0]
    else:
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.ordinal()
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    current = session.observe({"value": metric}, during=current_axis, by=members)
    baseline = session.observe({"value": metric}, during=baseline_axis, by=members)
    result = current.compare(baseline, match=match).execute()
    frame = result.to_pandas()
    assert len(frame) == 8 and frame["presence"].all().all()
    assert frame[("value", "value.absolute")].tolist() == expected
    assert frame.attrs["marivo"]["axes"][0]["kind"] == "correspondence"
    assert {p["path"] for p in frame.attrs["marivo"]["axes"][0]["projections"]} == {
        ("current",),
        ("baseline",),
    }


def test_previous_year_leap_day_is_missing_and_current_projection_is_exact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    current_axis = session.time_scope(start="2024-02-28", end="2024-03-01").by(mv.grain("day"))
    baseline_axis = session.time_scope(start="2023-02-28", end="2023-03-01").by(mv.grain("day"))
    current = session.observe({"value": metric}, during=current_axis)
    baseline = session.observe({"value": metric}, during=baseline_axis)
    with pytest.raises(AnalysisError, match="complete"):
        current.compare(baseline, match=mv.previous_year()).execute()
    result = current.compare(
        baseline, match=mv.previous_year(), how="outer", time_axis="current"
    ).execute()
    frame = result.to_pandas()
    assert frame.iloc[:, 0].tolist() == [
        datetime(2024, 2, 28, tzinfo=timezone.utc),
        datetime(2024, 2, 29, tzinfo=timezone.utc),
    ]
    assert frame[("presence", ("current",))].tolist() == [True, True]
    assert frame[("presence", ("baseline",))].tolist() == [True, False]
    assert frame[("value", "value.absolute")].iloc[0] == 0
    assert pd.isna(frame[("value", "value.absolute")].iloc[1])
