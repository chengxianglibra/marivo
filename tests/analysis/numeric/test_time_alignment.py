"""Current Frame calendar correspondence and endpoint recovery."""

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.test_period_alignment import paired_tables, period
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
    comparison = selected.compare(
        baseline,
        match=mv.ordinal(),
        how="outer",
        on=tuple(zip(selected.axes, baseline.axes, strict=True)),
    )
    result = comparison.persist()
    frame = result.to_pandas()
    assert frame[("presence", "left_present")].sum() == 1
    assert frame[("presence", "right_present")].sum() == lengths[1] * 4
    assert frame[("value", "absolute.value")].dropna().tolist() == [1]
    mapping, left_directory, _ = paired_tables(frame)
    retained = set(frame.loc[frame[("presence", "left_present")]].iloc[:, 0])
    keys = set(mapping.loc[mapping.iloc[:, 0].isin(retained), "left.0"].dropna())
    assert left_directory.loc[left_directory.iloc[:, 0].isin(keys), "ordinal"].tolist() == [0]
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(result.artifact.id).to_pandas(), frame
    )


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
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
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
    current = session.observe({"value": metric}, by=mv.product(current_axis, members))
    baseline = session.observe({"value": metric}, by=mv.product(baseline_axis, members))
    result = current.compare(
        baseline, match=match, on=tuple(zip(current.axes, baseline.axes, strict=True))
    ).compute()
    frame = result.to_pandas()
    assert len(frame) == 8 and frame["presence"].all().all()
    assert frame[("value", "absolute.value")].tolist() == expected
    assert frame.attrs["axes"][0]["role"] == "paired"
    mapping, left_directory, right_directory = paired_tables(frame)
    assert len(mapping) == len(left_directory) == len(right_directory) == 2


def test_previous_year_leap_day_is_missing_and_current_projection_is_exact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    current_axis = session.time_scope(start="2024-02-28", end="2024-03-01").by(mv.grain("day"))
    baseline_axis = session.time_scope(start="2023-02-28", end="2023-03-01").by(mv.grain("day"))
    current = session.observe({"value": metric}, by=(current_axis,))
    baseline = session.observe({"value": metric}, by=(baseline_axis,))
    with pytest.raises(AnalysisError, match="complete"):
        current.compare(
            baseline,
            match=mv.previous_year(),
            on=tuple(zip(current.axes, baseline.axes, strict=True)),
        ).compute()
    result = current.compare(
        baseline,
        match=mv.previous_year(),
        how="outer",
        on=tuple(zip(current.axes, baseline.axes, strict=True)),
    ).compute()
    frame = result.to_pandas()
    mapping, left_directory, _ = paired_tables(frame)
    labels = dict(zip(left_directory.iloc[:, 0], left_directory["nominal_label"], strict=True))
    pairs = mapping.set_index(mapping.columns[0])
    assert [labels[pairs.loc[key, "left.0"]] for key in frame.iloc[:, 0]] == [
        "2024-02-28",
        "2024-02-29",
    ]
    assert frame[("presence", "left_present")].tolist() == [True, True]
    assert frame[("presence", "right_present")].tolist() == [True, False]
    assert frame[("value", "absolute.value")].iloc[0] == 0
    assert pd.isna(frame[("value", "absolute.value")].iloc[1])
