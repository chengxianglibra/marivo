"""Exact interpolation and accuracy-preserving native algorithm selection."""

from decimal import Decimal

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo._definition.expressions import RowField, SourceAggregate
from marivo._definition.types import ExactRef, SumAll
from marivo.analysis.compiler.graph_plan import ReductionRead, SourceRead
from marivo.analysis.compiler.observation import ReductionInput, lower_observation
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.value_types import data_type
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.materialization.graph_source_execution import _complete_source_frame
from marivo.refs import ref
from marivo.semantic.errors import SemanticExpressionError


def _reduction(
    backend: str, q: float, *, approximate: bool = False, strict: bool = False
) -> ReductionInput:
    field = RowField(
        ExactRef(ref.measure("sales.facts.x"), "v1"),
        ExactRef(ref.entity("sales.facts"), "v1"),
        None,
        SumAll(),
    )
    return ReductionInput(
        SourceAggregate(
            field,
            "approx_quantile" if approximate else "quantile",
            None,
            q=q,
            nulls="error" if strict else "ignore",
        ),
        (),
        "value",
        provider=backend,
    )


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
def test_quantile_native_sql_preserves_algorithm(backend: str) -> None:
    table = ibis.table({"x": "float64"}, name="facts")
    for approximate in (False, True):
        reduction = _reduction(backend, 0.95, approximate=approximate)
        if approximate and backend in ("postgres", "mysql"):
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": approx_quantile"):
                lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
            continue
        expression, _ = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
        sql = ibis.to_sql(expression, dialect=backend).lower()
        if approximate:
            expected = {
                "duckdb": "approx_quantile",
                "trino": "approx_percentile",
                "clickhouse": "quantiletdigest",
            }
            assert expected[backend] in sql
            assert "percentile_cont" not in sql
        elif backend in ("mysql", "trino", "clickhouse"):
            assert "row_number()" in sql
            assert "__quantile_lower" in sql
            assert "__quantile_upper" in sql
            assert "approx_percentile(" not in sql
            assert "quantile(" not in sql
            assert "quantileexactexclusive(" not in sql
        else:
            assert "percentile_cont" in sql or "quantile_cont" in sql


@pytest.mark.parametrize(
    "cases",
    [
        pytest.param(
            (
                ([0, 10, None], "int64", 0.95, 9.5),
                ([9007199254740993, 9007199254740995], "int64", 0.5, 9007199254740994.0),
                ([Decimal("0"), Decimal("10"), None], "decimal(18,2)", 0.95, Decimal("9.50")),
            ),
            id="numeric_carriers",
        ),
        pytest.param(
            (
                ([0.0, 10.0, None], "float64", 0.0, 0.0),
                ([0.0, 10.0, None], "float64", 1.0, 10.0),
                ([0.0, 10.0], "float64", 5e-324, 5e-323),
            ),
            id="probability_edges",
        ),
        pytest.param(
            (
                ([None, None], "float64", 0.95, None),
                ([], "float64", 0.95, None),
                ([7], "int64", 0.95, 7.0),
            ),
            id="cardinality_edges",
        ),
        pytest.param(
            (
                ([7, 7, 7, None], "int64", 0.95, 7.0),
                ([-10, 0, 10], "int64", 0.25, -5.0),
                ([-1e308, 1e308], "float64", 0.5, 0.0),
            ),
            id="interpolation_shapes",
        ),
    ],
)
def test_portable_bounds_and_typed_interpolation(
    cases: tuple[
        tuple[list[int | float | Decimal | None], str, float, float | Decimal | None], ...
    ],
) -> None:
    for values, dtype, q, expected in cases:
        connection = ibis.duckdb.connect(threads=1)
        try:
            table = connection.create_table(
                "facts",
                ibis.memtable(
                    pa.table({"x": pa.array(values, type=ibis.dtype(dtype).to_pyarrow())})
                ),
            )
            reduction = _reduction("trino", q)
            expression, checks = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
            raw = connection.execute(expression)
            # Bounds transport is independent of input cardinality.
            assert len(raw) == 1, (values, dtype, q)
            stage = SourceRead(
                "read:0",
                "facts",
                (0,),
                (DataColumn("value", "value", data_type(expression.value.type()), True),),
                (),
                ReductionRead(reduction),
                (("sales.facts.x", "x"),),
                (),
                None,
            )
            value = _complete_source_frame(stage, raw, checks).table.value.iloc[0]
            if expected is None:
                assert pd.isna(value), (values, dtype, q)
            else:
                assert value == expected, (values, dtype, q)
        finally:
            connection.disconnect()


@pytest.mark.parametrize("bad", [None, float("inf"), float("nan")])
def test_bounds_do_not_hide_strict_or_nonfinite_inputs(bad: float | None) -> None:
    for deferred in (False, True):
        connection = ibis.duckdb.connect(threads=1)
        try:
            # SQL literals preserve NaN instead of a pandas conversion to NULL.
            literal = "NULL" if bad is None else "'NaN'" if pd.isna(bad) else "'Infinity'"
            table = connection.sql(f"SELECT CAST({literal} AS DOUBLE) AS x UNION ALL SELECT 10.0")
            reduction = _reduction("trino", 0.95, strict=True)
            expression, checks = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
            stage = SourceRead(
                "read:0",
                "facts",
                (0,),
                (
                    DataColumn("value", "value", data_type(expression.value.type()), True),
                    *(
                        DataColumn(name, name, ScalarDataType("boolean"), False)
                        for name, _ in checks
                        if deferred
                    ),
                ),
                (),
                ReductionRead(reduction),
                (("sales.facts.x", "x"),),
                (),
                None,
            )
            if deferred:
                completed = _complete_source_frame(stage, connection.execute(expression), checks)
                assert any(bool(completed.table[name].iloc[0]) for name, _ in checks)
            else:
                with pytest.raises(SemanticExpressionError):
                    _complete_source_frame(stage, connection.execute(expression), checks)
        finally:
            connection.disconnect()
