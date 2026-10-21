"""Bound result metadata and native execution have independent numeric oracles."""

from decimal import Decimal, localcontext

import ibis
import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.numeric_state import finish_decimal, sum_values
from marivo.semantic._numeric_lowering import NumericValue, _source_sum, numeric_binary
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "operation,left,right,expected",
    [
        ("add", "decimal(3,2)", "decimal(3,2)", "decimal(4,2)"),
        ("subtract", "decimal(3,2)", "int64", "decimal(22,2)"),
        ("multiply", "decimal(3,2)", "decimal(3,2)", "decimal(6,4)"),
        ("multiply", "decimal(30,10)", "decimal(20,5)", "decimal(38,3)"),
        ("add", "decimal(38,2)", "decimal(38,2)", "decimal(38,1)"),
        ("multiply", "decimal(38,38)", "decimal(38,38)", "decimal(38,38)"),
        ("add", "int64", "int64", "int64"),
        ("add", "decimal(3,2)", "float64", "float64"),
        ("divide", "decimal(3,2)", "decimal(3,2)", "float64"),
    ],
)
def test_bound_result_type(operation: str, left: str, right: str, expected: str) -> None:
    table = ibis.table({"x": left, "y": right})
    result = numeric_binary(operation, NumericValue(table.x), NumericValue(table.y))
    assert result.value.type() == dt.dtype(expected)
    assert all("overflow" not in check.received for check in result.checks)
    sql = str(ibis.to_sql(table.select(value=result.value), dialect="duckdb"))
    if expected.startswith("decimal"):
        assert "CAST(" in sql and expected.upper() in sql.replace(" ", "")
    if operation == "subtract" and right == "int64":
        assert "DECIMAL(19, 0)" in sql
    if operation == "divide" and left.startswith("decimal"):
        assert sql.count("AS DOUBLE") == 2


def test_explicit_decimal_product_reaches_native_arrow_reader() -> None:
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "amounts", pa.table({"x": pa.array([Decimal("1.23")], type=pa.decimal128(3, 2))})
        )
        result = numeric_binary("multiply", NumericValue(table.x), NumericValue(table.x)).value
        output = backend.to_pyarrow(table.select(value=result))
        assert output.schema.field("value").type == pa.decimal128(6, 4)
        assert output.column("value").to_pylist() == [Decimal("1.5129")]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("dialect", ["duckdb", "clickhouse"])
@pytest.mark.parametrize("dtype", ["int64", "decimal(31,2)"])
def test_sum_has_one_decimal_primary_accumulation(dtype: str, dialect: str) -> None:
    table = ibis.table({"x": dtype}, name="amounts")
    result = _source_sum(table.x)
    assert result.type() == dt.Decimal(38, 2 if "decimal" in dtype else 0)
    sql = str(ibis.to_sql(table.aggregate(value=result), dialect=dialect)).upper()
    assert sql.count("SUM(") == 1
    assert "9223372036854775807" not in sql
    assert "CAST(SUM(" in sql


@pytest.mark.parametrize("value", ["9.995", "-9.995", "1.245", "-1.245"])
def test_decimal_convergence_handles_ties_and_carry(value: str) -> None:
    expected = {"9.995": "10.00", "-9.995": "-10.00", "1.245": "1.25", "-1.245": "-1.25"}[value]
    result = finish_decimal(pd.Series([Decimal(value), None]), 4, 2)
    assert result.iloc[0] == Decimal(expected)
    assert pd.isna(result.iloc[1])
    assert pa.array(result).type == pa.decimal128(4, 2)
    if abs(Decimal(value)) > 9:
        with pytest.raises(SemanticExpressionError, match="representable Decimal"):
            finish_decimal(pd.Series([Decimal(value)]), 3, 2)


@pytest.mark.parametrize("sign", ["", "-"])
def test_precision_38_rounding_and_accumulation_ignore_caller_context(sign: str) -> None:
    # 38-digit input carries to a 36-digit integer after reducing scale by one.
    value = Decimal(sign + "9" * 35 + ".995")
    with localcontext() as context:
        context.prec = 6
        rounded = finish_decimal(pd.Series([value]), 38, 2)
        total = sum_values(
            [Decimal("12345678901234567890123456789.01"), Decimal("0.01")], pa.decimal128(31, 2)
        )
    assert rounded.iloc[0] == Decimal(sign + "1" + "0" * 35 + ".00")
    assert total == Decimal("12345678901234567890123456789.02")


def test_local_recipe_checks_integer_child_before_cancellation() -> None:
    table = ibis.table({"x": "int64"}, name="amounts")
    expression = (table.x + 1) * 0
    with pytest.raises(SemanticExpressionError) as error:
        evaluate_numeric(
            expression.op(), {"x": pd.Series([2**63 - 1], dtype=object)}, pd.RangeIndex(1)
        )
    assert "overflow" in error.value.received


def test_public_decimal_nodes_and_integer_sum_preserve_saved_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = case.session.values(
        by=(case.catalog.fields.get("sales.customer.customer_id"),),
        columns={"x": None, "big": None},
        rows=(
            (("A",), {"x": Decimal("1.23"), "big": 2**63 - 1}),
            (("B",), {"x": Decimal("1.24"), "big": 1}),
        ),
    )
    selected = frame.select({"product": frame["x"] * frame["x"]})
    saved = selected.persist()
    assert saved.to_pandas()["value", "product"].tolist() == [Decimal("1.5129"), Decimal("1.5376")]
    assert pa.array(saved.to_pandas()["value", "product"]).type == pa.decimal128(6, 4)
    assert selected.where(selected["product"] > Decimal("1.52")).compute().to_pandas().iloc[
        :, 0
    ].tolist() == ["B"]
    total = frame.reduce({"total": frame["big"].sum()})
    result = total.persist()
    assert result.to_pandas()["value", "total"].tolist() == [Decimal(2**63)]
    assert pa.array(result.to_pandas()["value", "total"]).type == pa.decimal128(38, 0)
    assert case.session.persisted_frame(saved.artifact.id).to_pandas().equals(saved.to_pandas())
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(result.to_pandas())


def test_integer_sum_and_decimal_product_cold_read_without_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    from tests.support.paths import PROJECT_ROOT

    case = analysis_dsl_case_factory("j1")
    frame = case.session.values(
        by=(), columns={"x": None, "big": None}, rows=(((), {"x": Decimal("1.23"), "big": 2**62}),)
    )
    product = frame.select({"product": frame["x"] * frame["x"]}).persist()
    total = frame.reduce({"sum": (frame["big"] + 1).sum()}).persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    code = """
import sys
from decimal import Decimal
import pyarrow as pa
from unittest.mock import patch
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
with patch.object(SourceSession, 'execute', side_effect=AssertionError('source read')):
    session = mv.session.resume(sys.argv[1])
    product = session.persisted_frame(sys.argv[2]).to_pandas()['value', 'product']
    total = session.persisted_frame(sys.argv[3]).to_pandas()['value', 'sum']
    assert product.tolist() == [Decimal('1.5129')]
    assert pa.array(product).type == pa.decimal128(6, 4)
    assert total.tolist() == [Decimal(2**62 + 1)]
    assert pa.array(total).type == pa.decimal128(38, 0)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, case.session.id, product.artifact.id, total.artifact.id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "MARIVO_PROJECT_ROOT": str(case.root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_comparison_uses_bound_decimal_difference_for_relative_value(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")

    def total(value: str) -> mv.AnalysisFrame:
        rows = case.session.values(by=(), columns={"x": None}, rows=(((), {"x": Decimal(value)}),))
        return rows.reduce({"x": rows["x"].sum()}).compute()

    current, baseline = total("1.05"), total("1.00")
    result = current.compare({"base": baseline}).compute().to_pandas()
    assert result["value", "base.absolute.x"].tolist() == [Decimal("0.1")]
    assert result["value", "base.relative.x"].tolist() == [0.1]
