"""Current Frame calendar correspondence and endpoint recovery."""

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.test_period_alignment import (
    comparison_tables,
    paired_tables,
    period,
    temporal_keys,
)
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
        {"reference": baseline},
        match={"reference": mv.ordinal()},
        on={"reference": tuple(zip(selected.axes, baseline.axes, strict=True))},
    )
    paired = lengths[1] > lengths[0]
    if paired:
        with pytest.raises(AnalysisError, match="actual baseline time row"):
            comparison.compute()
        comparison = selected.align(
            baseline, match=mv.ordinal(), on=tuple(zip(selected.axes, baseline.axes, strict=True))
        )
    result = comparison.persist()
    frame = result.to_pandas()
    presence = (
        ("left_present", "right_present") if paired else ("current.present", "reference.present")
    )
    assert frame[presence[0]].sum() == 1
    assert frame[presence[1]].sum() == lengths[1] * 4
    changes = (
        result.select({"change": result["left.value"] - result["right.value"]})
        .compute()
        .to_pandas()["change"]
        if paired
        else frame["reference.absolute.value"]
    )
    assert changes.dropna().tolist() == [1]
    mapping, left_directory, _ = (
        paired_tables(frame) if paired else comparison_tables(result, frame)
    )
    retained = set(temporal_keys(result).loc[frame[presence[0]]])
    current_column = "left.0" if paired else mapping.columns[0]
    keys = set(mapping.loc[mapping.iloc[:, 0].isin(retained), current_column].dropna())
    assert left_directory.loc[left_directory.iloc[:, 0].isin(keys), "ordinal"].tolist() == [0]
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(result.artifact.id).to_pandas(), frame
    )


@pytest.mark.parametrize("kind", ["ordinal", "periods", "years"])
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    current_axis = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    if kind == "years":
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.offset(years=-1)
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    elif kind == "periods":
        baseline_axis = session.time_scope(start="2026-07-31", end="2026-08-02").by(mv.grain("day"))
        match = mv.offset(periods=-1)
        expected = [30, 0, 0, 0, -30, 15, 0, 0]
    else:
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.ordinal()
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    current = session.observe({"value": metric}, by=mv.product(current_axis, members))
    baseline = session.observe({"value": metric}, by=mv.product(baseline_axis, members))
    result = current.compare(
        {"reference": baseline},
        match={"reference": match},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    ).compute()
    frame = result.to_pandas()
    assert len(frame) == 8 and frame[["current.present", "reference.present"]].all().all()
    assert frame["reference.absolute.value"].tolist() == expected
    assert frame.attrs["axes"][0]["role"] == "calendar"
    mapping, left_directory, right_directory = comparison_tables(result, frame)
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
    result = current.compare(
        {"reference": baseline},
        match={"reference": mv.offset(years=-1)},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    ).compute()
    frame = result.to_pandas()
    mapping, left_directory, _ = comparison_tables(result, frame)
    assert set(mapping.iloc[:, 0]) == set(left_directory.iloc[:, 0])
    assert frame["calendar:day"].tolist() == [
        "2024-02-28",
        "2024-02-29",
    ]
    assert frame["current.present"].tolist() == [True, True]
    assert frame["reference.present"].tolist() == [True, False]
    assert frame["reference.absolute.value"].iloc[0] == 0
    assert pd.isna(frame["reference.absolute.value"].iloc[1])
