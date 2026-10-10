"""Comparisons propagate Present non-Defined operands per Cell instead of failing the Run."""

from __future__ import annotations

import itertools
import os
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.cell_arrow import column as cell_column
from marivo.analysis.methods.comparison import propagated_cell
from marivo.refs import MetricKind, Ref
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT

_TAGS = ("defined", "null", "undefined", "unknown")


def _case(factory: DslCaseFactory, parquet: bool, *, double: bool = False) -> DslCase:
    case = factory("j2")
    if double:
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
    if parquet:
        export_dsl_parquet_models(case, case.root)
    if double or parquet:
        ms.load(workspace_dir=case.root)
    return case


def _members(case: DslCase) -> mv.LogicalAnalysisDomain:
    members = case.session.members(ms.ref.entity("sales.customer"))
    assert isinstance(members, mv.LogicalAnalysisDomain)
    return members


def _monthly(
    members: mv.LogicalAnalysisDomain, metric: Ref[MetricKind] | RuntimeMetricExpr, month: int
) -> mv.LogicalNumericRelation | mv.LogicalRatioRelation:
    result = members.observe(
        metric,
        during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    assert isinstance(result, (mv.LogicalNumericRelation, mv.LogicalRatioRelation))
    return result


def _endpoint_cell(
    result: mv.MaterializedNumericRelation, role: str, member: str
) -> tuple[object, object]:
    assert result._dataset is not None
    table = next(part.table for part in result._dataset.verified().parts if part.role == role)
    index = table["key_0"].to_pylist().index(member)
    return (
        cell_column(table, f"{role}__cell_tag").to_pylist()[index],
        cell_column(table, f"{role}__cell_reason").to_pylist()[index],
    )


def test_propagated_cell_priority_is_unknown_undefined_null() -> None:
    rank = {"null": 0, "undefined": 1, "unknown": 2}
    for current, baseline in itertools.product(_TAGS, repeat=2):
        result = propagated_cell(current, f"{current}_c", baseline, f"{baseline}_b")
        if current == baseline == "defined":
            assert result is None
            continue
        assert result is not None
        if baseline == "defined" or (current != "defined" and rank[current] >= rank[baseline]):
            assert result == (current, f"{current}_c")
        else:
            assert result == (baseline, f"{baseline}_b")


@pytest.mark.parametrize("parquet", [True])
@pytest.mark.parametrize(
    "design",
    [mv.exact(), mv.outer(missing="keep"), mv.outer(missing="empty_value")],
    ids=("exact", "keep", "metric_empty"),
)
def test_null_operand_propagates_in_time_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
    design: mv.ExactAlignment | mv.OuterAlignment,
) -> None:
    case = _case(analysis_dsl_case_factory, parquet)
    metric = ms.ref.metric("sales.revenue")
    members = _members(case)
    current, baseline = _monthly(members, metric, 9), _monthly(members, metric, 8)
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        for value, expected in (
            ("difference", {"A": -30, "B": 80, "C": 0}),
            ("relative_change", {"A": -0.5, "B": 80 / 120}),
        ):
            result = getattr(
                left.compare(right, align=design),
                "absolute" if value == "difference" else "relative",
            ).execute()
            frame = result.to_pandas().set_index("member")
            for member, number in expected.items():
                assert frame.loc[member, "cell_tag"] == "defined"
                assert frame.loc[member, "value"] == pytest.approx(number)
            # Customer D has no September order: the sum Metric finishes Null.
            assert frame.loc["D", "cell_tag"] == "null"
            assert frame.loc["D", "cell_reason"] == "empty_contribution"
            assert pd.isna(frame.loc["D", "value"])
            assert _endpoint_cell(result, "current_endpoint", "D") == ("null", "empty_contribution")
            assert _endpoint_cell(result, "baseline_endpoint", "D") == ("defined", None)


@pytest.mark.parametrize("parquet", [True])
def test_null_operands_propagate_in_period_comparison(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = _case(analysis_dsl_case_factory, parquet)
    members = case.session.members(ms.ref.entity("sales.customer"))

    def observe(month: int, day: int) -> mv.LogicalNumericRelation:
        grid = mv.time_grid(
            during=mv.time_scope(
                start=f"2026-{month:02d}-{day:02d}", end=f"2026-{month:02d}-{day + 3:02d}"
            ),
            grain=mv.grain("day"),
        )
        result = members.observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        assert isinstance(result, mv.LogicalNumericRelation)
        return result

    current, baseline = observe(8, 14), observe(7, 9)
    design = mv.exact(time=mv.ordinal())
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        frame = left.compare(right, align=design).absolute.execute().to_pandas()
        assert len(frame) == 12
        defined = frame[frame["cell_tag"] == "defined"]
        assert sorted(defined["value"].tolist()) == [-50, 0, 20]
        assert frame["cell_tag"].tolist().count("null") == 9
        assert frame.loc[frame["cell_tag"] == "null", "cell_reason"].eq("empty_contribution").all()


@pytest.mark.parametrize("parquet", [True])
def test_both_non_defined_operands_take_the_higher_priority_tag(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    # Ratio operands must share one numeric family; the count ratio is float64.
    case = _case(analysis_dsl_case_factory, parquet, double=True)
    members = _members(case)
    revenue = _monthly(members, ms.ref.metric("sales.revenue"), 9)
    count_ratio = _monthly(
        members,
        mv.runtime_metric.ratio(
            ms.ref.metric("sales.order_count"),
            ms.ref.metric("sales.order_count"),
            label="count_ratio",
        ),
        9,
    )
    for left, right in ((revenue, count_ratio), (revenue.execute(), count_ratio.execute())):
        for first, second in ((left, right), (right, left)):
            result = first.ratio(second).execute()
            frame = result.to_pandas().set_index("member")
            assert frame.loc["A", "cell_tag"] == "defined"
            # D: revenue is Null(empty_contribution), the ratio Metric is
            # Undefined(zero_denominator); Undefined wins on either side.
            assert frame.loc["D", "cell_tag"] == "undefined"
            assert frame.loc["D", "cell_reason"] == "zero_denominator"
            reasons = {
                _endpoint_cell(result, role, "D")[1]
                for role in ("current_endpoint", "baseline_endpoint")
            }
            assert reasons == {"empty_contribution", "zero_denominator"}


@pytest.mark.parametrize("parquet", [True])
def test_null_on_both_sides_stays_null(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = _case(analysis_dsl_case_factory, parquet)
    metric = ms.ref.metric("sales.revenue")
    # Nobody orders in October; D also has no September order.
    members = _members(case)
    current, baseline = _monthly(members, metric, 10), _monthly(members, metric, 9)
    for left, right in ((current, baseline), (current.execute(), baseline.execute())):
        frame = left.compare(right).absolute.execute().to_pandas()
        assert frame["cell_tag"].eq("null").all()
        assert frame["cell_reason"].eq("empty_contribution").all()


def test_nonfinite_operand_still_fails_the_whole_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute("UPDATE \"order\" SET amount = 'NaN' WHERE order_id = 'j2_sa'")
    ms.load(workspace_dir=case.root)
    members = _members(case)
    metric = ms.ref.metric("sales.revenue")
    current, baseline = _monthly(members, metric, 9), _monthly(members, metric, 8)
    with pytest.raises(MaterializationError):
        current.compare(baseline).absolute.execute()


@pytest.mark.slow
def test_propagated_null_survives_cold_recovery(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = _members(case)
    metric = ms.ref.metric("sales.revenue")
    saved = _monthly(members, metric, 9).compare(_monthly(members, metric, 8)).absolute.execute()
    source = case.database_path
    offline = source.with_name(source.name + ".offline")
    source.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("cold comparison cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
saved = mv.session.resume(sys.argv[1], by="id").artifact(sys.argv[2])
assert isinstance(saved, mv.MaterializedDifferenceRelation)
frame = saved.to_pandas().set_index("member")
assert frame.loc["D", "cell_tag"] == "null"
assert frame.loc["D", "cell_reason"] == "empty_contribution"
defined = saved.where(saved.value.is_defined()).execute().to_pandas()
assert sorted(defined["member"].tolist()) == ["A", "B", "C"]
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, saved.state.artifact_ref.ref],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        offline.rename(source)
    assert completed.returncode == 0, completed.stderr
