"""Period correspondence retains complete keys, endpoint projections and presence."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import CalendarAxis
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


def period(
    session: mv.Session,
    month: int,
    length: int,
    *,
    metric: str = "order_count",
    grouped: bool = False,
) -> tuple[CalendarAxis, mv.AnalysisFrame]:
    axis = session.time_scope(
        start=f"2026-{month:02d}-01", end=f"2026-{month:02d}-{length + 1:02d}"
    ).by(mv.grain("day"))
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    domain = (
        members.classify(by=(session.catalog.fields.get("sales.customer.region"),))
        if grouped
        else members
    )
    return axis, session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)}, by=mv.product(axis, domain)
    )


def paired_tables(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Locate the actual exported single-calendar pair and both frozen directories."""
    axis = frame.attrs["axes"][0]
    assert axis["role"] == "paired"
    descriptor = axis["descriptor"]
    parts = {part["name"]: part["rows"] for part in frame.attrs["frozen_coordinates"]}
    mapping = parts[descriptor["mapping_name"]]
    left = parts[descriptor["left"]["frozen"]["directory_name"]]
    right = parts[descriptor["right"]["frozen"]["directory_name"]]
    assert all(isinstance(table, pd.DataFrame) for table in (mapping, left, right))
    assert len(descriptor["left"]["columns"]) == len(descriptor["right"]["columns"]) == 1
    return mapping, left, right


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
@pytest.mark.parametrize("parquet", [False, True])
def test_period_outer_keeps_time_missing_and_original_keys(
    analysis_dsl_case_factory: DslCaseFactory,
    lengths: tuple[int, int],
    parquet: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    _, current = period(case.session, 8, lengths[0])
    _, baseline = period(case.session, 7, lengths[1])
    result = current.compare(
        baseline,
        match=mv.ordinal(),
        how="outer",
        on=tuple(zip(current.axes, baseline.axes, strict=True)),
    ).persist()
    frame = result.to_pandas()
    assert len(frame) == 12
    assert [axis["role"] for axis in frame.attrs["axes"]] == ["paired", "identity"]
    mapping, left_directory, right_directory = paired_tables(frame)
    assert set(frame.iloc[:, 0]) == set(mapping.iloc[:, 0])
    assert set(frame.iloc[:, 1]) == {"A", "B", "C", "D"}
    tail_key = mapping.loc[mapping["left.0"].isna() | mapping["right.0"].isna()].iloc[0, 0]
    tail = frame[frame.iloc[:, 0] == tail_key]
    assert len(tail) == 4
    for field in ("absolute", "relative"):
        assert tail[("value", field + ".value")].isna().all()
    for side, length, directory in zip(
        ("left", "right"), lengths, (left_directory, right_directory), strict=True
    ):
        assert frame[("presence", side + "_present")].sum() == length * 4
        present_keys = set(mapping[side + ".0"].dropna())
        assert present_keys == set(directory.iloc[:, 0])
        assert directory["ordinal"].tolist() == list(range(length))
        assert len(directory) == length
    assert frame[("value", "absolute.value")].dropna().sum() == 1
    recovered = case.session.persisted_frame(result.artifact.id)
    pd.testing.assert_frame_equal(recovered.to_pandas(), frame)


def test_period_exact_rejects_unequal_calendars_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 2)
    with pytest.raises(AnalysisError, match="complete"):
        current.compare(
            baseline, match=mv.ordinal(), on=tuple(zip(current.axes, baseline.axes, strict=True))
        ).compute()
    assert case.session.runs().items == ()


def test_selected_periods_keep_original_positions_and_nullable_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    selected = current.where(current["value"] > 0)
    paired = selected.compare(
        baseline,
        match=mv.ordinal(),
        how="outer",
        on=tuple(zip(selected.axes, baseline.axes, strict=True)),
    )
    frame = paired.compute().to_pandas()
    assert len(frame) == 12
    assert frame[("presence", "left_present")].sum() == 1
    assert frame[("presence", "right_present")].all()
    assert frame[("value", "absolute.value")].dropna().tolist() == [1]
    for field in ("absolute", "relative"):
        view = paired.select({"value": paired[field + ".value"]})
        defined = view.where(~view["value"].is_null())
        assert len(defined.compute().to_pandas()) == (1 if field == "absolute" else 0)


@pytest.mark.parametrize("metric", ["order_count", "revenue"])
@pytest.mark.parametrize("how", ["exact", "outer"])
def test_grouped_grid_includes_empty_groups_and_propagates_both_values(
    analysis_dsl_case_factory: DslCaseFactory, metric: str, how: Literal["exact", "outer"]
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3, metric=metric, grouped=True)
    _, baseline = period(case.session, 7, 3, metric=metric, grouped=True)
    observed = current.compute().to_pandas()
    assert len(observed) == 9
    assert set(observed.iloc[:, 1]) == {"east", "south", "west"}
    empty = observed[observed.iloc[:, 1].isin(("south", "west"))]
    assert len(empty) == 6 and empty[("value", "value")].eq(0).all()
    comparison = current.compare(
        baseline,
        match=mv.ordinal(),
        how=how,
        on=tuple(zip(current.axes, baseline.axes, strict=True)),
    )
    frame = comparison.compute().to_pandas()
    assert len(frame) == 9 and frame["presence"].all().all()
    assert frame[("value", "absolute.value")].sum() == (1 if metric == "order_count" else 60)
    assert frame[("value", "relative.value")].isna().all()


def test_period_comparison_preserves_independent_spatial_endpoint_oracles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current_axis, current = period(case.session, 8, 3, grouped=True)
    baseline_axis, baseline = period(case.session, 7, 3, grouped=True)
    current_totals = case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.order_count")}, by=(current_axis,)
    )
    baseline_totals = case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.order_count")}, by=(baseline_axis,)
    )
    comparison = current_totals.compare(
        baseline_totals,
        match=mv.ordinal(),
        on=tuple(zip(current_totals.axes, baseline_totals.axes, strict=True)),
    )
    frame = comparison.compute().to_pandas()
    assert frame[("value", "left.value")].tolist() == [1, 0, 0]
    assert frame[("value", "right.value")].tolist() == [0, 0, 0]
    assert frame[("value", "absolute.value")].tolist() == [1, 0, 0]
    assert frame[("value", "relative.value")].isna().all()


def test_period_outer_distinguishes_absent_groups_from_zero_activity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET channel='mobile' WHERE order_id='j2_aa'")
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")

    def observe(month: int, start: int) -> mv.AnalysisFrame:
        axis = session.time_scope(
            start=f"2026-{month:02}-{start:02}", end=f"2026-{month:02}-{start + 3:02}"
        ).by(mv.grain("day"))
        return session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")}, by=(axis, channel)
        )

    current, baseline = observe(8, 1), observe(7, 9)
    frame = (
        current.compare(
            baseline,
            match=mv.ordinal(),
            how="outer",
            on=tuple(zip(current.axes, baseline.axes, strict=True)),
        )
        .compute()
        .to_pandas()
    )
    assert len(frame) == 6 and set(frame.iloc[:, 1]) == {"mobile", "web"}
    assert frame[("value", "absolute.value")].isna().all()
    assert frame[("presence", "left_present")].sum() == 3
    assert frame[("presence", "right_present")].sum() == 3


def test_period_saved_output_checks_endpoint_projections_and_presence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_storage import read_output

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 2)
    _, baseline = period(case.session, 7, 3)
    saved = current.compare(
        baseline,
        match=mv.ordinal(),
        how="outer",
        on=tuple(zip(current.axes, baseline.axes, strict=True)),
    ).persist()
    backing = saved._backing
    assert isinstance(backing, StoredFrameBacking)
    data = backing.data
    read_output(case.root, data.schema, data.rows)
    with pytest.raises(AnalysisError):
        mapping = next(part for part in data.attachments if part.name.startswith("mapping."))
        read_output(
            case.root,
            replace(
                mapping.schema,
                values=(
                    replace(mapping.schema.values[0], slot_id="forged"),
                    *mapping.schema.values[1:],
                ),
            ),
            mapping.rows,
        )
    with pytest.raises(AnalysisError):
        replace(data.schema, presence=(replace(data.schema.presence[0], nullable=True),))
    assert len(data.schema.values) == 4 and len(data.schema.presence) == 2


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
def test_period_source_values_match_independent_count_oracle(
    analysis_dsl_case_factory: DslCaseFactory, lengths: tuple[int, int]
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, lengths[0])
    _, baseline = period(case.session, 7, lengths[1])
    comparison = current.compare(
        baseline,
        match=mv.ordinal(),
        how="outer",
        on=tuple(zip(current.axes, baseline.axes, strict=True)),
    )
    frame = comparison.compute().to_pandas()
    mapping, left_directory, right_directory = paired_tables(frame)
    left_ordinals = dict(zip(left_directory.iloc[:, 0], left_directory["ordinal"], strict=True))
    right_ordinals = dict(zip(right_directory.iloc[:, 0], right_directory["ordinal"], strict=True))
    pairs = mapping.set_index(mapping.columns[0])
    for _, row in frame.iterrows():
        pair = pairs.loc[row.iloc[0]]
        left, right = pair["left.0"], pair["right.0"]
        a, b = pd.notna(left), pd.notna(right)
        position = left_ordinals[left] if a else right_ordinals[right]
        customer = row.iloc[1]
        assert row[("presence", "left_present")] == a
        assert row[("presence", "right_present")] == b
        expected = int(position == 0 and customer == "A")
        if a and b:
            assert row[("value", "absolute.value")] == expected
        else:
            assert pd.isna(row[("value", "absolute.value")])
