"""ClickHouse Decimal finite guards follow only their exact projection lineage."""

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pytest
from ibis.backends.clickhouse import Backend

from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import ProjectionRead, SourceRead
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _native_query_expression,
)
from marivo.datasource.adapters import SourceSession, provider_for
from tests.datasource.source_cases import datasource


def _rewrite(expression: ir.Table) -> ir.Table:
    stage = SourceRead("finite-check", "native", (), (), (), ProjectionRead((), None), (), (), None)
    source = SourceSession(
        provider_for("clickhouse"), datasource("clickhouse", {}), Backend(), owns_backend=False
    )
    try:
        prepared = PreparedSources(source, (), {}, {})
        return _native_query_expression(prepared, stage, LoweredRead(stage.stage_id, expression))
    finally:
        source.close()


@pytest.mark.parametrize("aliases", (0, 1, 3))
def test_decimal_division_finite_guards_follow_exact_aliases(aliases: int) -> None:
    table = ibis.table({"amount": "decimal(12,2)", "weight": "int64"}, name="numeric")
    value = table.amount / table.weight
    for index in range(aliases):
        table = table.select(**{f"alias_{index}": value})
        value = table[f"alias_{index}"]
    expression = value.as_table().select(nan=value.isnan(), inf=value.isinf())
    rewritten = _rewrite(expression)
    assert not rewritten.op().find((ops.IsNan, ops.IsInf))
    assert rewritten.op().find(ops.Divide) == expression.op().find(ops.Divide)
    assert "FALSE" in ibis.to_sql(rewritten, dialect="clickhouse")


@pytest.mark.parametrize("floating", ("float_value", "mixed_division", "float_division"))
def test_unrelated_decimal_alias_never_removes_float_finite_guards(floating: str) -> None:
    table = ibis.table(
        {"amount": "decimal(12,2)", "weight": "int64", "floating": "float64"}, name="numeric"
    )
    value = (
        table.floating
        if floating == "float_value"
        else table.amount / table.floating
        if floating == "mixed_division"
        else table.floating / table.weight
    )
    projected = table.select(decimal_sibling=table.amount / table.weight, value=value)
    renamed = projected.select(decimal_sibling=projected.decimal_sibling, alias=projected.value)
    expression = renamed.select(nan=renamed["alias"].isnan(), inf=renamed["alias"].isinf())
    rewritten = _rewrite(expression)
    assert rewritten.op().find((ops.IsNan, ops.IsInf)) == expression.op().find(
        (ops.IsNan, ops.IsInf)
    )
    sql = ibis.to_sql(rewritten, dialect="clickhouse")
    assert "isNaN" in sql and "isInfinite" in sql


def test_decimal_alias_preserves_zero_null_and_error_guards() -> None:
    table = ibis.table({"amount": "decimal(12,2)", "weight": "int64"}, name="numeric")
    projected = table.select(value=table.amount / table.weight, weight=table.weight)
    expression = projected.select(
        nan=projected.value.isnan(),
        inf=projected.value.isinf(),
        zero=projected.weight == 0,
        missing=projected.value.isnull(),
        invalid_weight=projected.weight < 0,
    )
    rewritten = _rewrite(expression)
    assert not rewritten.op().find((ops.IsNan, ops.IsInf))
    assert isinstance(expression.op(), ops.Project)
    assert isinstance(rewritten.op(), ops.Project)
    for name in ("zero", "missing", "invalid_weight"):
        assert rewritten.op().values[name] == expression.op().values[name]
    assert rewritten.op().find(ops.Divide) == expression.op().find(ops.Divide)


def test_aggregate_weighted_decimal_alias_preserves_native_finish() -> None:
    table = ibis.table({"amount": "decimal(18,2)", "weight": "int64"}, name="numeric")
    pair = table.amount.notnull() & table.weight.notnull()
    numerator = (table.amount * table.weight).sum(where=pair).fill_null(0)
    denominator = table.weight.sum(where=pair).fill_null(0)
    value = numerator / denominator.nullif(0)
    aggregated = table.aggregate(
        value=value,
        invalid=value.notnull() & (value.isnan() | value.isinf()),
    )
    # The public weighted finish normalizes NaN through an Aggregate field.
    expression = aggregated.select(
        value=aggregated.value.isnan().ifelse(ibis.null(), aggregated.value),
        invalid=aggregated.invalid,
    )
    rewritten = _rewrite(expression)
    assert not rewritten.op().find((ops.IsNan, ops.IsInf))
    for kind in (ops.Divide, ops.Sum, ops.NullIf, ops.NotNull):
        assert rewritten.op().find(kind) == expression.op().find(kind)
    sql = ibis.to_sql(rewritten, dialect="clickhouse")
    assert "isNaN" not in sql and "isInfinite" not in sql


def test_aggregate_float_alias_keeps_checks_with_decimal_sibling() -> None:
    table = ibis.table(
        {"amount": "decimal(12,2)", "weight": "int64", "floating": "float64"}, name="numeric"
    )
    aggregate = table.aggregate(
        decimal_sibling=table.amount.sum() / table.weight.sum(), value=table.floating.mean()
    )
    expression = aggregate.select(nan=aggregate.value.isnan(), inf=aggregate.value.isinf())
    rewritten = _rewrite(expression)
    assert rewritten.op().find((ops.IsNan, ops.IsInf)) == expression.op().find(
        (ops.IsNan, ops.IsInf)
    )


@pytest.mark.parametrize(
    "value,nan,inf",
    ((float("nan"), True, False), (float("inf"), False, True), (-float("inf"), False, True)),
    ids=("nan", "positive_infinity", "negative_infinity"),
)
def test_float_alias_finite_checks_still_detect_nonfinite_values(
    value: float, nan: bool, inf: bool
) -> None:
    table = ibis.literal(value, type="float64").name("floating").as_table()
    projected = table.select(value=table.floating)
    expression = projected.select(nan=projected.value.isnan(), inf=projected.value.isinf())
    rewritten = _rewrite(expression)
    backend = ibis.duckdb.connect()
    try:
        result = backend.execute(rewritten)
    finally:
        backend.disconnect()
    assert result["nan"].tolist() == [nan]
    assert result["inf"].tolist() == [inf]
