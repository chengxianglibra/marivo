"""Fixed sum semantics distinguish absent contributions from unknown values."""

import math

import duckdb
import ibis
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.native_formula import align_native_inputs
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, catalog_value


def test_population_preserves_empty_unknown_and_valid_zero_members(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" VALUES '
            "('a1','A','web','paid','2026-08-15',100),"
            "('a2','A','web','paid','2026-08-15',NULL),"
            "('c1','C','web','paid','2026-08-15',NULL),"
            "('d1','D','web','paid','2026-08-15',10),"
            "('d2','D','web','paid','2026-08-15',-10)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    orders = session.catalog.get("entity:sales.order").value
    members = session.members(session.catalog.get("entity:sales.customer").value)
    query = session.observe(
        {"total": amount.sum(), "valid": amount.count(), "orders": orders.count()},
        by=members,
    )
    fixed = query.compute()
    rows = fixed.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["A", "B", "C", "D"]
    assert rows["total"].iloc[[0, 1, 3]].tolist() == [100, 0, 0]
    assert pd.isna(rows["total"].iloc[2])
    assert rows["valid"].tolist() == [1, 0, 0, 2]
    assert rows["orders"].tolist() == [2, 0, 1, 2]
    summary = (
        fixed.reduce(
            {
                "total": fixed["total"].sum(),
                "mean": fixed["total"].mean(),
                "members": mv.row_count(),
            }
        )
        .compute()
        .to_pandas()
    )
    assert summary.iloc[0].tolist() == pytest.approx([100, 100 / 3, 4])
    pending_summary = (
        query.reduce(
            {
                "total": query["total"].sum(),
                "mean": query["total"].mean(),
                "members": mv.row_count(),
            }
        )
        .compute()
        .to_pandas()
    )
    pd.testing.assert_frame_equal(pending_summary, summary)
    samples = (
        session.observe({"mean": amount.sum().per(members).mean(), "members": members.count()})
        .compute()
        .to_pandas()
    )
    assert samples.iloc[0].tolist() == pytest.approx([100 / 3, 4])
    with pytest.raises(AnalysisError, match="non-NA internal samples"):
        session.observe({"mean": amount.sum().per(members).mean(nulls="error")}).compute()
    saved = fixed.persist()
    pd.testing.assert_frame_equal(session.persisted_frame(saved.artifact_id).to_pandas(), rows)
    empty = members.where(~mv.in_population(members))
    with pytest.raises(AnalysisError, match="missing output coordinates"):
        session.observe({"total": amount.sum()}, by=empty).compute()
    assert (
        session.observe({"total": amount.sum()}, by=empty, where=mv.in_population(empty))
        .compute()
        .to_pandas()
        .empty
    )


def test_native_formula_does_not_zero_fill_a_present_all_null_group(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" VALUES '
            "('a','A','web','paid','2026-08-15',100),"
            "('c','C','web','paid','2026-08-15',NULL)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    metric = amount.sum().where(customer == "A") + amount.sum().where(customer == "C")
    rows = session.observe({"total": metric}, by=(customer,)).compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == ["A", "C"]
    assert rows["total"].iloc[0] == 100
    assert pd.isna(rows["total"].iloc[1])


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_native_nonfinite_source_values_remain_distinct_from_null_during_selection(
    analysis_dsl_case_factory: DslCaseFactory, invalid: float
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        connection.execute('UPDATE "order" SET amount=? WHERE order_id=?', [invalid, "j1_a"])
        expected_nulls = connection.execute(
            'SELECT COUNT(*) FROM "order" WHERE amount IS NULL'
        ).fetchone()
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    members = session.members(session.catalog.get("entity:sales.order").value)
    read = session.read(members, {"amount": amount})
    observed = session.observe({"amount": amount.sum()})
    raw = read.compute().to_pandas()
    values = pa.array(raw["amount"])
    assert any(
        value.is_valid
        and (math.isnan(value.as_py()) if math.isnan(invalid) else value.as_py() == invalid)
        for value in values
    )
    missing = read.where(read["amount"].is_null()).compute().to_pandas()["amount"]
    assert expected_nulls is not None and len(missing) == expected_nulls[0]
    assert pa.array(missing).null_count == len(missing)
    assert read.top(0, by=read["amount"]).compute().to_pandas().empty
    assert read.select({"constant": 1}).compute().to_pandas()["constant"].tolist() == [1] * len(raw)
    total = observed.compute().to_pandas()["amount"]
    assert pa.array(total).is_valid().to_pylist() == [True]
    assert math.isnan(total.iloc[0]) if math.isnan(invalid) else total.iloc[0] == invalid
    assert observed.top(0, by=observed["amount"]).compute().to_pandas().empty


def test_native_presence_checks_boolean_value_for_default_filled_joins() -> None:
    # ClickHouse join_use_nulls=0 fills an absent nonnullable marker with False.
    # The marker's truth, rather than its nullness, distinguishes absent sums.
    left = ibis.memtable({"key": ["A"], "a": [100.0]}, schema={"key": "string", "a": "float64"})
    right = ibis.memtable({"key": ["C"], "b": [None]}, schema={"key": "string", "b": "float64"})
    aligned = align_native_inputs(
        (left, right), ("key",), ("a", "b"), provider="clickhouse", alignment="domain"
    )
    sql = ibis.to_sql(aligned, dialect="clickhouse")
    assert '"__formula_present_0", FALSE)' in sql
    assert '"__formula_present_1", FALSE)' in sql
    connection = ibis.duckdb.connect()
    try:
        rows = connection.execute(aligned.order_by("key"))
    finally:
        connection.disconnect()
    assert rows["a"].tolist() == [100.0, 0.0]
    assert rows["b"].iloc[0] == 0.0
    assert pd.isna(rows["b"].iloc[1])
