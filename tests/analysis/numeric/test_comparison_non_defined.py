"""Nullable values remain distinct from absent coordinates in comparisons."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models


def _case(factory: DslCaseFactory, *, double: bool = False) -> DslCase:
    case = factory("j2")
    if double:
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    return case


def _observe(case: DslCase, start: str, end: str, *, daily: bool = False) -> mv.AnalysisFrame:
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    during = session.time_scope(start=start, end=end)
    return session.observe(
        {"value": amount.sum(time=time, empty="null")},
        during=during.by(mv.grain("day")) if daily else during,
        by=session.members(ms.ref.entity("sales.customer")),
    )


def test_nullable_arithmetic_propagates_missing_values_in_both_directions() -> None:
    left = pd.Series([pd.NA, 2, pd.NA, 0], dtype=object)
    right = pd.Series([2, pd.NA, pd.NA, 0], dtype=object)
    for first, second in ((left, right), (right, left)):
        for operation in ("add", "subtract", "multiply"):
            result = nullable_arithmetic(operation, first, second)
            assert result.iloc[:3].isna().all()
            assert result.iloc[3] == 0
        assert nullable_arithmetic("divide", first, second).isna().all()


def test_null_operand_propagates_in_time_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = _case(analysis_dsl_case_factory)
    current = _observe(case, "2026-09-01", "2026-10-01")
    baseline = _observe(case, "2026-08-01", "2026-09-01")
    for first, second, sign in ((current, baseline, 1), (baseline, current, -1)):
        result = first.compare(
            second, on=tuple(zip(first.axes, second.axes, strict=True))
        ).compute()
        frame = result.to_pandas()
        frame = frame.set_index(frame.columns[0])
        assert frame.loc["A", ("value", "absolute.value")] == -30 * sign
        assert frame.loc["B", ("value", "absolute.value")] == 80 * sign
        assert frame.loc["C", ("value", "absolute.value")] == 0
        assert pd.isna(frame.loc["D", ("value", "absolute.value")])
        assert pd.isna(frame.loc["D", ("value", "relative.value")])
        assert frame.loc["D", "presence"].all()
        empty_endpoint = "left.value" if sign == 1 else "right.value"
        populated_endpoint = "right.value" if sign == 1 else "left.value"
        assert pd.isna(frame.loc["D", ("value", empty_endpoint)])
        assert frame.loc["D", ("value", populated_endpoint)] == 0
    frame = (
        current.compare(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))
        .compute()
        .to_pandas()
    )
    assert frame[("value", "relative.value")].dropna().tolist() == pytest.approx([-0.5, 2 / 3])


def test_null_operands_propagate_in_period_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = _case(analysis_dsl_case_factory)
    current = _observe(case, "2026-08-14", "2026-08-17", daily=True)
    baseline = _observe(case, "2026-07-09", "2026-07-12", daily=True)
    result = current.compare(
        baseline, match=mv.ordinal(), on=tuple(zip(current.axes, baseline.axes, strict=True))
    ).persist()
    frame = result.to_pandas()
    assert len(frame) == 12
    absolute = frame[("value", "absolute.value")]
    assert sorted(absolute.dropna().tolist()) == [-50, 0, 20]
    assert absolute.isna().sum() == 9
    assert frame["presence"].all().all()
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)


def test_null_and_zero_denominator_both_remain_nullable_in_both_directions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = _case(analysis_dsl_case_factory, double=True)
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    q = session.observe(
        {
            "revenue": amount.sum(time=time, empty="null"),
            "count": session.catalog.metrics.get("sales.order_count"),
        },
        during=session.time_scope(start="2026-09-01", end="2026-10-01"),
        by=session.members(ms.ref.entity("sales.customer")),
    )
    endpoints = q.select({"revenue": q["revenue"], "count_ratio": q["count"] / q["count"]})
    values = endpoints.compute().to_pandas()
    values = values.set_index(values.columns[0])
    assert pd.isna(values.loc["D", ("value", "revenue")])
    assert pd.isna(values.loc["D", ("value", "count_ratio")])
    assert values.loc["A", ("value", "revenue")] == 30.0
    assert values.loc["A", ("value", "count_ratio")] == 1.0
    for first, second, expected in (
        ("revenue", "count_ratio", 30.0),
        ("count_ratio", "revenue", 1 / 30),
    ):
        frame = (
            endpoints.select({"value": endpoints[first] / endpoints[second]}).compute().to_pandas()
        )
        frame = frame.set_index(frame.columns[0])
        assert frame.loc["A", ("value", "value")] == pytest.approx(expected)
        assert pd.isna(frame.loc["D", ("value", "value")])


def test_nonfinite_operand_still_fails_the_whole_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute("UPDATE \"order\" SET amount = 'NaN' WHERE order_id = 'j2_sa'")
    ms.load(workspace_dir=case.root)
    current = _observe(case, "2026-09-01", "2026-10-01")
    baseline = _observe(case, "2026-08-01", "2026-09-01")
    with pytest.raises(AnalysisError) as caught:
        current.compare(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True))).compute()
    assert caught.value.expected and caught.value.received
    assert case.session.graph().artifacts == ()
