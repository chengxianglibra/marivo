"""Exact interpolation and accuracy-preserving native algorithm selection."""

import math
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
        if (approximate and backend in ("postgres", "mysql")) or (
            not approximate and backend in ("mysql", "trino")
        ):
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": "):
                lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
            continue
        expression, _ = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
        from tests.datasource.aggregate_fixtures import compiler_backend

        sql = compiler_backend(backend).compile(expression).lower()
        if approximate:
            expected = {
                "duckdb": "approx_quantile",
                "trino": "approx_percentile",
                "clickhouse": "quantiletdigest",
            }
            assert expected[backend] in sql
            assert "percentile_cont" not in sql
        elif backend == "clickhouse":
            assert "quantileexactinclusive(0.95)" in sql
            assert "row_number()" not in sql
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
                ([-10.0, 10.0], "float64", 0.5, 0.0),
            ),
            id="interpolation_shapes",
        ),
    ],
)
def test_native_quantiles_and_typed_results(
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
            reduction = _reduction("duckdb", q)
            expression, checks = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
            raw = connection.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
            # Native aggregate transport is independent of input cardinality.
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
def test_native_quantiles_keep_strict_null_checks_and_nonfinite_values(bad: float | None) -> None:
    for deferred in (False, True):
        connection = ibis.duckdb.connect(threads=1)
        try:
            # SQL literals preserve NaN instead of a pandas conversion to NULL.
            literal = "NULL" if bad is None else "'NaN'" if pd.isna(bad) else "'Infinity'"
            table = connection.sql(f"SELECT CAST({literal} AS DOUBLE) AS x UNION ALL SELECT 10.0")
            reduction = _reduction("duckdb", 0.95, strict=True)
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
                completed = _complete_source_frame(
                    stage,
                    connection.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype),
                    checks,
                )
                assert any(bool(completed.table[name].iloc[0]) for name, _ in checks) is (
                    bad is None
                )
            elif bad is None:
                with pytest.raises(SemanticExpressionError):
                    _complete_source_frame(
                        stage,
                        connection.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype),
                        checks,
                    )
            else:
                value = _complete_source_frame(
                    stage,
                    connection.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype),
                    checks,
                ).table.value
                assert pa.array(value).is_valid().to_pylist() == [True]
                assert math.isnan(value.iloc[0]) if math.isnan(bad) else math.isinf(value.iloc[0])
        finally:
            connection.disconnect()


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    (
        ([float("inf"), float("inf")], 0.0, float("inf")),
        ([float("inf"), float("inf")], 0.5, float("inf")),
        ([-float("inf"), 10.0], 0.0, -float("inf")),
        ([-float("inf"), 10.0], 0.5, -float("inf")),
        ([10.0, float("inf")], 0.5, float("inf")),
        ([-float("inf"), float("inf")], 0.5, float("nan")),
        ([10.0, float("nan")], 0.5, float("nan")),
    ),
)
def test_native_interpolation_preserves_infinity_and_valid_nan(
    values: list[float], q: float, expected: float
) -> None:
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table(
            "facts", ibis.memtable(pa.table({"x": pa.array(values, from_pandas=False)}))
        )
        native = connection.raw_sql(f"SELECT quantile_cont(x, {q!r}) FROM facts").fetchone()
        assert native is not None and native[0] is not None
        assert math.isnan(native[0]) if math.isnan(expected) else native[0] == expected
        reduction = _reduction("duckdb", q)
        expression, checks = lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
        stage = SourceRead(
            "read:0",
            "facts",
            (0,),
            (DataColumn("value", "value", ScalarDataType("float64"), True),),
            (),
            ReductionRead(reduction),
            (("sales.facts.x", "x"),),
            (),
            None,
        )
        value = _complete_source_frame(
            stage, connection.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype), checks
        ).table.value
        assert pa.array(value).is_valid().to_pylist() == [True]
        assert math.isnan(value.iloc[0]) if math.isnan(native[0]) else value.iloc[0] == native[0]
    finally:
        connection.disconnect()


@pytest.mark.parametrize(
    ("backend", "dtype", "duration"),
    [("clickhouse", "decimal(18,2)", False), ("duckdb", "int64", True)],
)
def test_unsupported_source_quantile_types_fail_during_schema_compilation(
    backend: str, dtype: str, duration: bool
) -> None:
    from dataclasses import replace

    from marivo.analysis.core.data_types import DurationDataType

    table = ibis.table({"x": dtype}, name="facts")
    reduction = _reduction(backend, 0.95)
    if duration:
        reduction = replace(reduction, duration=DurationDataType("ns"))
    with pytest.raises(AnalysisStageUnavailableError, match=backend + ": quantile"):
        lower_observation(reduction, table, {"sales.facts.x": "x"}, ())
