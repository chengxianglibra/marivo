"""Period correspondence retains complete keys, endpoint projections and presence."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import CalendarAxis
from marivo.analysis.core.data_schema import CalendarComparisonBinding, CalendarDataAxis, DataSchema
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import (
    DslCaseFactory,
    catalog_value,
    export_dsl_parquet_models,
    terminal_runs,
)


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
    members = session.members(session.catalog.get("entity:sales.customer").value)
    domain = (
        members.classify(
            by=(catalog_value(session.catalog, "sales.customer.region", kind="field"),)
        )
        if grouped
        else members
    )
    return axis, session.observe(
        {"value": catalog_value(session.catalog, "sales." + metric, kind="metric")},
        by=mv.product(axis, domain),
    )


def temporal_keys(result: mv.AnalysisFrame) -> pd.Series:
    """Inspect internal storage keys for finite-map integrity assertions."""
    backing = result.compute()._backing
    assert isinstance(backing, MemoryFrameBacking)
    return backing.data.copy_rows()[backing.data.schema.axes[0].columns[0].slot_id]


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


def comparison_tables(
    result: mv.AnalysisFrame, frame: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read the named comparison's actual current, baseline and consumed map."""
    schema = result._schema
    assert isinstance(schema, DataSchema)
    binding = next(item.binding for item in schema.comparisons if item.alias == "reference")
    assert isinstance(binding, CalendarComparisonBinding)
    current = next(axis for axis in schema.axes if axis.token == binding.current_axis_token)
    assert isinstance(current, CalendarDataAxis) and current.frozen is not None
    assert binding.baseline.frozen is not None
    parts = {part["name"]: part["rows"] for part in frame.attrs["frozen_coordinates"]}
    return (
        parts[binding.mapping_name],
        parts[current.frozen.directory_name],
        parts[binding.baseline.frozen.directory_name],
    )


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
    on = tuple(zip(current.axes, baseline.axes, strict=True))
    comparison = current.compare({"reference": mv.baseline(baseline, match=mv.ordinal(), on=on)})
    if lengths[1] > lengths[0]:
        with pytest.raises(AnalysisError, match="actual baseline time row"):
            comparison.compute()
        aligned = current.align(baseline, match=mv.ordinal(), on=on).compute()
        frame = aligned.to_pandas()
        mapping, left_directory, right_directory = paired_tables(frame)
        assert len(frame) == 12
        assert set(temporal_keys(aligned)) == set(mapping.iloc[:, 0])
        for side, length, directory in zip(
            ("left", "right"), lengths, (left_directory, right_directory), strict=True
        ):
            assert frame[side + "_present"].sum() == length * 4
            assert set(mapping[side + ".0"].dropna()) == set(directory.iloc[:, 0])
        difference = aligned.select(
            {"change": aligned["left.value"] - aligned["right.value"]}
        ).compute()
        assert difference.to_pandas()["change"].dropna().sum() == 1
        return
    fixed = comparison.compute()
    frame = fixed.to_pandas()
    assert len(frame) == 12
    assert [axis["role"] for axis in frame.attrs["axes"]] == ["calendar", "identity"]
    assert set(frame["sales.customer"]) == {"A", "B", "C", "D"}
    assert frame["current.present"].sum() == lengths[0] * 4
    assert frame["reference.present"].sum() == lengths[1] * 4
    assert frame["reference.absolute.value"].dropna().sum() == 1
    tail = frame[~frame["reference.present"]]
    assert len(tail) == 4
    for field in ("absolute", "relative"):
        assert tail["reference." + field + ".value"].isna().all()
    result = fixed.persist()
    recovered = case.session.persisted_frame(result.artifact_id)
    pd.testing.assert_frame_equal(recovered.to_pandas(), frame)


def test_period_outer_retains_unequal_calendars(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 2)
    result = (
        current.compare(
            {
                "reference": mv.baseline(
                    baseline,
                    match=mv.ordinal(),
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                )
            }
        )
        .compute()
        .to_pandas()
    )
    assert len(result) == 12
    assert result["current.present"].tolist() == [True] * 12
    assert result["reference.present"].tolist() == [True] * 8 + [False] * 4
    assert terminal_runs(case.session) == ()


def test_selected_periods_keep_original_positions_and_nullable_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    selected = current.where(current["value"] > 0)
    paired = selected.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(selected.axes, baseline.axes, strict=True)),
            )
        }
    )
    frame = paired.compute().to_pandas()
    assert len(frame) == 12
    assert frame["current.present"].sum() == 1
    assert frame["reference.present"].all()
    assert frame["reference.absolute.value"].dropna().tolist() == [1]
    for field in ("absolute", "relative"):
        view = paired.select({"value": paired["reference." + field + ".value"]})
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
    assert set(observed["sales.customer.region"]) == {"east", "south", "west"}
    empty = observed[observed["sales.customer.region"].isin(("south", "west"))]
    assert len(empty) == 6 and empty["value"].eq(0).all()
    comparison = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
    )
    frame = comparison.compute().to_pandas()
    assert len(frame) == 9 and frame[["current.present", "reference.present"]].all().all()
    assert frame["reference.absolute.value"].sum() == (1 if metric == "order_count" else 60)
    assert frame["reference.relative.value"].isna().all()


def test_period_comparison_preserves_independent_spatial_endpoint_oracles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current_axis, current = period(case.session, 8, 3, grouped=True)
    baseline_axis, baseline = period(case.session, 7, 3, grouped=True)
    current_totals = case.session.observe(
        {"value": case.session.catalog.get("metric:sales.order_count").value}, by=(current_axis,)
    )
    baseline_totals = case.session.observe(
        {"value": case.session.catalog.get("metric:sales.order_count").value}, by=(baseline_axis,)
    )
    comparison = current_totals.compare(
        {
            "reference": mv.baseline(
                baseline_totals,
                match=mv.ordinal(),
                on=tuple(zip(current_totals.axes, baseline_totals.axes, strict=True)),
            )
        }
    )
    frame = comparison.compute().to_pandas()
    assert frame["value"].tolist() == [1, 0, 0]
    assert frame["reference.baseline.value"].tolist() == [0, 0, 0]
    assert frame["reference.absolute.value"].tolist() == [1, 0, 0]
    assert frame["reference.relative.value"].isna().all()


def test_period_outer_distinguishes_absent_groups_from_zero_activity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET channel='mobile' WHERE order_id='j2_aa'")
    session = case.session
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")

    def observe(month: int, start: int) -> mv.AnalysisFrame:
        axis = session.time_scope(
            start=f"2026-{month:02}-{start:02}", end=f"2026-{month:02}-{start + 3:02}"
        ).by(mv.grain("day"))
        return session.observe(
            {"value": session.catalog.get("metric:sales.order_count").value}, by=(axis, channel)
        )

    current, baseline = observe(8, 1), observe(7, 9)
    frame = (
        current.compare(
            {
                "reference": mv.baseline(
                    baseline,
                    match=mv.ordinal(),
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                )
            }
        )
        .compute()
        .to_pandas()
    )
    assert len(frame) == 6 and set(frame["sales.order.channel"]) == {"mobile", "web"}
    assert frame["reference.absolute.value"].isna().all()
    assert frame["current.present"].sum() == 3
    assert frame["reference.present"].sum() == 3


def test_period_saved_output_checks_endpoint_projections_and_presence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_storage import read_output

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 2)
    saved = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
    ).persist()
    backing = saved._backing
    assert isinstance(backing, StoredFrameBacking)
    data = backing.data
    read_output(case.root, data.schema, data.rows)
    with pytest.raises(AnalysisError):
        binding = data.schema.comparisons[0].binding
        assert isinstance(binding, CalendarComparisonBinding)
        mapping = next(part for part in data.attachments if part.name == binding.mapping_name)
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
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
    )
    if lengths[1] > lengths[0]:
        with pytest.raises(AnalysisError, match="actual baseline time row"):
            comparison.compute()
        aligned = current.align(
            baseline, match=mv.ordinal(), on=tuple(zip(current.axes, baseline.axes, strict=True))
        ).compute()
        frame = aligned.to_pandas()
        mapping, left_directory, right_directory = paired_tables(frame)
        current_column, baseline_column = "left.0", "right.0"
        presence = ("left_present", "right_present")
        changes = (
            aligned.select({"change": aligned["left.value"] - aligned["right.value"]})
            .compute()
            .to_pandas()
        )
    else:
        fixed = comparison.compute()
        frame = fixed.to_pandas()
        mapping, left_directory, right_directory = comparison_tables(fixed, frame)
        current_column, baseline_column = mapping.columns[0], "baseline.0"
        presence = ("current.present", "reference.present")
        changes = frame.rename(columns={"reference.absolute.value": "change"})
    left_ordinals = dict(zip(left_directory.iloc[:, 0], left_directory["ordinal"], strict=True))
    right_ordinals = dict(zip(right_directory.iloc[:, 0], right_directory["ordinal"], strict=True))
    pairs = mapping.set_index(mapping.columns[0])
    keys = temporal_keys(aligned if lengths[1] > lengths[0] else fixed)
    for key, (index, row) in zip(keys, frame.iterrows(), strict=True):
        pair = pairs.loc[key]
        left = key if current_column == mapping.columns[0] else pair[current_column]
        right = pair[baseline_column]
        a, b = pd.notna(left), pd.notna(right)
        position = left_ordinals[left] if a else right_ordinals[right]
        customer = row["sales.customer"]
        assert row[presence[0]] == a
        assert row[presence[1]] == b
        expected = int(position == 0 and customer == "A")
        if a and b:
            assert changes.loc[index, "change"] == expected
        else:
            assert pd.isna(changes.loc[index, "change"])
