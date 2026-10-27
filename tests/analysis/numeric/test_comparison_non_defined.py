"""Nullable values remain distinct from absent coordinates in comparisons."""

from __future__ import annotations

import math

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    catalog_value,
    export_dsl_parquet_models,
    terminal_runs,
)


def _case(factory: DslCaseFactory, *, double: bool = False) -> DslCase:
    case = factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        if double:
            connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        # Unknown observations must be represented by actual NULL-valued facts;
        # an absent contribution now has the distinct value zero.
        days = ("2026-07-09", "2026-07-10", "2026-07-11", "2026-08-14", "2026-08-15", "2026-08-16")
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (
                    f"unknown-{day}-{customer}",
                    customer,
                    "web",
                    "paid",
                    day + "T12:00:00+00:00",
                    None,
                )
                for day in days
                for customer in "ABCD"
            ]
            + [("unknown-september-D", "D", "web", "paid", "2026-09-10", None)],
        )
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    return case


def _observe(case: DslCase, start: str, end: str, *, daily: bool = False) -> mv.AnalysisFrame:
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    during = session.time_scope(start=start, end=end)
    return session.observe(
        {"value": amount.sum(time=time)},
        by=mv.product(
            during.by(mv.grain("day")) if daily else during,
            session.members(session.catalog.get("entity:sales.customer").value),
        ),
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
            {
                "reference": mv.baseline(
                    second, on=tuple(zip(first.axes, second.axes, strict=True)), match=mv.ordinal()
                )
            }
        ).compute()
        frame = result.to_pandas()
        frame = frame.set_index(ordinary_columns(frame)[0])
        assert frame.loc["A", "reference.absolute.value"] == -30 * sign
        assert frame.loc["B", "reference.absolute.value"] == 80 * sign
        assert frame.loc["C", "reference.absolute.value"] == 0
        assert pd.isna(frame.loc["D", "reference.absolute.value"])
        assert pd.isna(frame.loc["D", "reference.relative.value"])
        assert frame.loc["D", ["current.present", "reference.present"]].all()
        empty_endpoint = "value" if sign == 1 else "reference.baseline.value"
        populated_endpoint = "reference.baseline.value" if sign == 1 else "value"
        assert pd.isna(frame.loc["D", empty_endpoint])
        assert frame.loc["D", populated_endpoint] == 0
    frame = (
        current.compare(
            {
                "reference": mv.baseline(
                    baseline,
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        )
        .compute()
        .to_pandas()
    )
    assert frame["reference.relative.value"].dropna().tolist() == pytest.approx([-0.5, 2 / 3])


def test_null_operands_propagate_in_period_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = _case(analysis_dsl_case_factory)
    current = _observe(case, "2026-08-14", "2026-08-17", daily=True)
    baseline = _observe(case, "2026-07-09", "2026-07-12", daily=True)
    result = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
    ).persist()
    frame = result.to_pandas()
    assert len(frame) == 12
    absolute = frame["reference.absolute.value"]
    assert sorted(absolute.dropna().tolist()) == [-50, 0, 20]
    assert absolute.isna().sum() == 9
    assert frame[["current.present", "reference.present"]].all().all()
    assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(frame)


def test_null_and_zero_denominator_both_remain_nullable_in_both_directions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = _case(analysis_dsl_case_factory, double=True)
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    q = session.observe(
        {
            "revenue": amount.sum(time=time),
            "count": amount.count(time=time),
        },
        by=mv.product(
            session.time_scope(start="2026-09-01", end="2026-10-01"),
            session.members(session.catalog.get("entity:sales.customer").value),
        ),
    )
    endpoints = q.select({"revenue": q["revenue"], "count_ratio": q["count"] / q["count"]})
    values = endpoints.compute().to_pandas()
    values = values.set_index(ordinary_columns(values)[0])
    assert pd.isna(values.loc["D", "revenue"])
    assert pd.isna(values.loc["D", "count_ratio"])
    assert values.loc["A", "revenue"] == 30.0
    assert values.loc["A", "count_ratio"] == 1.0
    for first, second, expected in (
        ("revenue", "count_ratio", 30.0),
        ("count_ratio", "revenue", 1 / 30),
    ):
        frame = (
            endpoints.select({"value": endpoints[first] / endpoints[second]}).compute().to_pandas()
        )
        frame = frame.set_index(ordinary_columns(frame)[0])
        assert frame.loc["A", "value"] == pytest.approx(expected)
        assert pd.isna(frame.loc["D", "value"])


def test_native_nan_operand_is_retained_until_local_comparison_arithmetic(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute("UPDATE \"order\" SET amount = 'NaN' WHERE order_id = 'j2_sa'")
    ms.load(workspace_dir=case.root)
    current = _observe(case, "2026-09-01", "2026-10-01")
    baseline = _observe(case, "2026-08-01", "2026-09-01")
    rows = current.compute().to_pandas()
    rows = rows.set_index(ordinary_columns(rows)[0])
    assert math.isnan(rows.loc["A", "value"])
    compared = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
                match=mv.ordinal(),
            )
        }
    )
    compared_rows = (
        compared.compute().to_pandas().set_index(ordinary_columns(rows.reset_index())[0])
    )
    assert math.isnan(compared_rows.loc["A", "reference.absolute.value"])
    assert math.isnan(compared_rows.loc["A", "reference.relative.value"])
    assert terminal_runs(case.session) == ()
