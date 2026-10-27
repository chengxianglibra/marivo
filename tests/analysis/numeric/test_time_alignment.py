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
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(selected.axes, baseline.axes, strict=True)),
            )
        }
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
        case.session.persisted_frame(result.artifact_id).to_pandas(), frame
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
    metric = session.catalog.get("metric:sales.revenue").value
    members = session.members(session.catalog.get("entity:sales.customer").value)
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
        {
            "reference": mv.baseline(
                baseline, match=match, on=tuple(zip(current.axes, baseline.axes, strict=True))
            )
        }
    ).compute()
    frame = result.to_pandas()
    assert len(frame) == 8 and frame[["current.present", "reference.present"]].all().all()
    assert frame["reference.absolute.value"].tolist() == expected
    assert frame.attrs["axes"][0]["role"] == "calendar"
    mapping, left_directory, right_directory = comparison_tables(result, frame)
    assert len(mapping) == len(left_directory) == len(right_directory) == 2


@pytest.mark.parametrize(
    ("grain", "current_start", "current_end", "baseline_start", "baseline_end", "offset"),
    [
        ("day", "2026-08-08", "2026-08-09", "2026-08-01", "2026-08-02", -7),
        ("week", "2026-08-10", "2026-08-17", "2026-08-03", "2026-08-10", -1),
    ],
)
def test_multi_axis_offset_inference_uses_the_consuming_grain(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    grain: str,
    current_start: str,
    current_end: str,
    baseline_start: str,
    baseline_end: str,
    offset: int,
) -> None:
    from marivo.analysis.materialization import frame_source_preparation

    case = analysis_dsl_case_factory("j2")
    session = case.session
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("current", "A", "web", "paid", f"{current_start}T12:00:00Z", 50),
                ("baseline", "A", "web", "paid", f"{baseline_start}T12:00:00Z", 20),
            ],
        )
    metric = session.catalog.get("metric:sales.revenue").value
    members = session.members(session.catalog.get("entity:sales.customer").value)
    current_axis = session.time_scope(start=current_start, end=current_end).by(mv.grain(grain))
    baseline_axis = session.time_scope(start=baseline_start, end=baseline_end).by(mv.grain(grain))
    current = session.observe({"value": metric}, by=mv.product(current_axis, members))
    baseline = session.observe({"value": metric}, by=mv.product(baseline_axis, members))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("constructing inferred correspondence must not read sources")

    with monkeypatch.context() as context:
        context.setattr(frame_source_preparation, "open_query_sources", forbidden)
        comparison = current.compare(baselines=baseline, match=mv.offset(periods=offset))
        aligned = current.align(baseline, match=mv.offset(periods=offset))
    table = comparison.compute().to_pandas()
    assert table["reference.absolute.value"].tolist() == [30, 0, 0, 0]
    assert table["reference.present"].tolist() == [True] * 4
    assert aligned.compute().to_pandas()["right_present"].tolist() == [True] * 4
    if grain == "week":
        with pytest.raises(AnalysisError) as caught:
            current.compare(baseline, match=mv.offset(periods=-7)).compute()
        error = caught.value
        assert "actual baseline time row" in error.expected
        for part in (
            "reference",
            "periods",
            "-7",
            "week",
            "step=1",
            "tz=",
            "current=",
            "baseline=",
            "key=",
            "bounds=",
        ):
            assert part in error.received
        assert "grain" in error.repair.action and "domain" in error.repair.action
        long_alias = "previous_week_" + "x" * 400
        with pytest.raises(AnalysisError) as long_error:
            current.compare(
                {long_alias: mv.baseline(baseline, match=mv.offset(periods=-7))}
            ).compute()
        assert long_alias in long_error.value.received
        assert "key=" in long_error.value.received and "bounds=" in long_error.value.received
        assert len(long_error.value.received) <= 2048
        from marivo.analysis.core.data_schema import CalendarDataAxis
        from marivo.analysis.core.frame_data import MemoryFrameBacking
        from marivo.analysis.core.frozen_calendar import read_calendar_directory
        from marivo.analysis.methods.frame_comparison import _unmapped_time_details

        shifted = session.observe(
            {"value": metric}, by=mv.product(current_axis.shift(mv.offset(periods=-1)), members)
        ).compute()
        fixed = current.compute()
        directories = []
        for frame in (fixed, shifted):
            assert isinstance(frame._backing, MemoryFrameBacking)
            axis = frame._schema.axes[0]
            assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
            part = next(
                item
                for item in frame._backing.data.copy_attachments()
                if item.name == axis.frozen.directory_name
            )
            directories.append(read_calendar_directory(axis, part.schema, part.rows))
        details = _unmapped_time_details(
            "reference", directories[0], directories[1], None, directories[1].buckets[0].key
        )
        assert "frozen_direct(periods=-1)" in details


def test_previous_year_leap_day_is_missing_and_current_projection_is_exact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = session.catalog.get("metric:sales.revenue").value
    current_axis = session.time_scope(start="2024-02-28", end="2024-03-01").by(mv.grain("day"))
    baseline_axis = session.time_scope(start="2023-02-28", end="2023-03-01").by(mv.grain("day"))
    current = session.observe({"value": metric}, by=(current_axis,))
    baseline = session.observe({"value": metric}, by=(baseline_axis,))
    result = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.offset(years=-1),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
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
