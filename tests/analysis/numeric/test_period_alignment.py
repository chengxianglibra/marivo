"""Period correspondence retains complete keys, endpoint projections and presence."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import CalendarAxis
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


def period(
    session: mv.Session,
    month: int,
    length: int,
    *,
    metric: str = "order_count",
    grouped: bool = False,
) -> tuple[CalendarAxis, mv.Query]:
    axis = session.time_scope(
        start=f"2026-{month:02d}-01", end=f"2026-{month:02d}-{length + 1:02d}"
    ).by(mv.grain("day"))
    members = session.members(ms.ref.entity("sales.customer"))
    domain = (
        members.classify(by=(session.catalog.fields.get("sales.customer.region"),))
        if grouped
        else members
    )
    return axis, session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)}, during=axis, by=domain
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
    result = current.compare(baseline, match=mv.ordinal(), how="outer").execute()
    frame = result.to_pandas()
    assert len(frame) == 12
    axes = frame.attrs["marivo"]["axes"]
    assert [axis["kind"] for axis in axes] == ["correspondence", "member"]
    expected_keys = {
        str((i if i < lengths[0] else None, i if i < lengths[1] else None)) for i in range(3)
    }
    assert set(frame.iloc[:, 0]) == expected_keys
    assert set(frame.iloc[:, 1]) == {"A", "B", "C", "D"}
    tail_key = str((2 if lengths[0] == 3 else None, 2 if lengths[1] == 3 else None))
    tail = frame[frame.iloc[:, 0] == tail_key]
    for field in ("absolute", "relative"):
        assert tail[("value", "value." + field)].isna().all()
    for side, length in zip(("current", "baseline"), lengths, strict=True):
        assert frame[("presence", (side,))].sum() == length * 4
        projections = {projection["path"]: projection for projection in axes[0]["projections"]}
        original = projections[(side,)]
        present = [value[0] for value in original["values"] if value[0] is not None]
        assert set(present) == set(range(length))
        assert len(original["bounds"]) == length
    assert frame[("value", "value.absolute")].dropna().sum() == 1
    recovered = case.session.artifact(result.id)
    pd.testing.assert_frame_equal(recovered.to_pandas(), frame)


def test_period_exact_rejects_unequal_calendars_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 2)
    with pytest.raises(AnalysisError, match="equal complete calendar bucket counts"):
        current.compare(baseline, match=mv.ordinal()).execute()
    assert case.session.runs().items == ()


def test_selected_periods_keep_original_positions_and_nullable_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    selected = current.where(current["value"] > 0)
    paired = selected.compare(baseline, match=mv.ordinal(), how="outer")
    frame = paired.execute().to_pandas()
    assert len(frame) == 12
    assert frame[("presence", ("current",))].sum() == 1
    assert frame[("presence", ("baseline",))].all()
    assert frame[("value", "value.absolute")].dropna().tolist() == [1]
    for field in ("absolute", "relative"):
        view = paired.select({"value": paired["value." + field]})
        defined = view.where(~view["value"].is_null())
        assert len(defined.execute().to_pandas()) == (1 if field == "absolute" else 0)


@pytest.mark.parametrize("metric", ["order_count", "revenue"])
@pytest.mark.parametrize("how", ["exact", "outer"])
def test_grouped_grid_includes_empty_groups_and_propagates_both_values(
    analysis_dsl_case_factory: DslCaseFactory, metric: str, how: Literal["exact", "outer"]
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3, metric=metric, grouped=True)
    _, baseline = period(case.session, 7, 3, metric=metric, grouped=True)
    observed = current.execute().to_pandas()
    assert len(observed) == 9
    assert set(observed.iloc[:, 1]) == {"east", "south", "west"}
    empty = observed[observed.iloc[:, 1].isin(("south", "west"))]
    assert len(empty) == 6 and empty[("value", "value")].eq(0).all()
    comparison = current.compare(baseline, match=mv.ordinal(), how=how)
    frame = comparison.execute().to_pandas()
    assert len(frame) == 9 and frame["presence"].all().all()
    assert frame[("value", "value.absolute")].sum() == (1 if metric == "order_count" else 60)
    assert frame[("value", "value.relative")].isna().all()


def test_period_comparison_preserves_independent_spatial_endpoint_oracles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current_axis, current = period(case.session, 8, 3, grouped=True)
    baseline_axis, baseline = period(case.session, 7, 3, grouped=True)
    current_totals = current.rollup(by=(current_axis,))
    baseline_totals = baseline.rollup(by=(baseline_axis,))
    comparison = current_totals.compare(baseline_totals, match=mv.ordinal())
    frame = comparison.execute().to_pandas()
    assert frame[("value", "value.current")].tolist() == [1, 0, 0]
    assert frame[("value", "value.baseline")].tolist() == [0, 0, 0]
    assert frame[("value", "value.absolute")].tolist() == [1, 0, 0]
    assert frame[("value", "value.relative")].isna().all()


def test_period_outer_distinguishes_absent_groups_from_zero_activity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET channel='mobile' WHERE order_id='j2_aa'")
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")

    def observe(month: int, start: int) -> mv.Query:
        axis = session.time_scope(
            start=f"2026-{month:02}-{start:02}", end=f"2026-{month:02}-{start + 3:02}"
        ).by(mv.grain("day"))
        return session.observe(
            {"value": session.catalog.metrics.get("sales.order_count")}, during=axis, by=(channel,)
        )

    current, baseline = observe(8, 1), observe(7, 9)
    frame = current.compare(baseline, match=mv.ordinal(), how="outer").execute().to_pandas()
    assert len(frame) == 6 and set(frame.iloc[:, 1]) == {"mobile", "web"}
    assert frame[("value", "value.absolute")].isna().all()
    assert frame[("presence", ("current",))].sum() == 3
    assert frame[("presence", ("baseline",))].sum() == 3


def test_period_saved_output_checks_endpoint_projections_and_presence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_storage import read_output

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 2)
    _, baseline = period(case.session, 7, 3)
    saved = current.compare(baseline, match=mv.ordinal(), how="outer").execute()
    data = saved._manifest.data
    read_output(case.root, data.schema, data.rows)
    with pytest.raises(AnalysisError):
        replace(
            data,
            coordinate_projections=(replace(data.coordinate_projections[0], columns=("forged",)),),
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
    comparison = current.compare(baseline, match=mv.ordinal(), how="outer")
    frame = comparison.execute().to_pandas()
    for _, row in frame.iterrows():
        import ast

        left, right = ast.literal_eval(row.iloc[0])
        position, customer = (left if left is not None else right), row.iloc[1]
        a, b = left is not None, right is not None
        assert row[("presence", ("current",))] == a
        assert row[("presence", ("baseline",))] == b
        expected = int(position == 0 and customer == "A")
        if a and b:
            assert row[("value", "value.absolute")] == expected
        else:
            assert pd.isna(row[("value", "value.absolute")])
