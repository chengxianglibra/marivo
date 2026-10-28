"""Native aggregate identity and composition through instance-scoped Ibis compilers."""

from collections.abc import Callable

import ibis
import ibis.expr.types as ir
import pytest

from marivo.datasource.errors import DatasourceSourceCapabilityError
from tests.datasource.aggregate_fixtures import compiler_backend

OPERATIONS: dict[str, Callable[[ir.NumericColumn], ir.Scalar]] = {
    "count_distinct": lambda x: x.nunique(),
    "approx_nunique": lambda x: x.approx_nunique(),
    "quantile": lambda x: x.quantile(0.95),
    "median": lambda x: x.median(),
    "approx_quantile": lambda x: x.approx_quantile(0.95),
    "approx_median": lambda x: x.approx_median(),
}
EXPECTED = {
    "duckdb": ("count(distinct", "approx_count_distinct", "quantile_cont", "approx_quantile"),
    "postgres": ("count(distinct", None, "percentile_cont", None),
    "mysql": ("count(distinct", None, None, None),
    "trino": ("count(distinct", "approx_distinct", None, "approx_percentile"),
    "clickhouse": ("uniqexact", "uniqhll12", "quantileexactinclusive", "quantiletdigest"),
}


@pytest.mark.parametrize("backend", EXPECTED)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_native_algorithm_identity_and_rejection(backend: str, operation: str) -> None:
    table = ibis.table({"x": "float64"}, name="facts")
    expression = OPERATIONS[operation](table.x).name("result")
    index = {
        "count_distinct": 0,
        "approx_nunique": 1,
        "quantile": 2,
        "median": 2,
        "approx_quantile": 3,
        "approx_median": 3,
    }[operation]
    expected = EXPECTED[backend][index]
    owner = compiler_backend(backend)
    if expected is None:
        with pytest.raises(DatasourceSourceCapabilityError, match=backend):
            owner.compile(expression)
    else:
        sql = owner.compile(expression).lower()
        assert expected in sql
        if backend == "clickhouse":
            assert "uniq(" not in sql
            assert "quantile(" not in sql
            assert "quantileexactexclusive(" not in sql


@pytest.mark.parametrize("q", [0, 0.5, 0.95, 1])
def test_corrected_aggregates_compose_with_relational_and_scalar_operations(q: float) -> None:
    events = ibis.table({"id": "int64", "key": "string", "amount": "float64"}, name="events")
    regions = ibis.table({"key": "string", "region": "string"}, name="regions")
    joined = events.join(regions, "key").filter(events.amount >= 0)
    grouped = joined.group_by("region").aggregate(
        users=joined.id.nunique(where=joined.amount > 0),
        value=joined.amount.quantile(q, where=joined.amount > 0),
        total=joined.amount.sum(),
    )
    result = grouped.mutate(ratio=grouped.total / grouped.users)
    sql = compiler_backend("clickhouse").compile(result).lower()
    assert "uniqexactif(" in sql
    assert f"quantileexactinclusiveif({float(q)})" in sql or f"quantileexactinclusiveif({q})" in sql
    assert "join" in sql and "where" in sql and "group by" in sql and "sum(" in sql
    assert "/" in sql
    original = ibis.to_sql(events.amount.quantile(q), dialect="clickhouse").lower()
    assert "quantile(" in original and "quantileexactinclusive" not in original


@pytest.mark.parametrize("dtype", ["decimal(18,2)", "string"])
def test_clickhouse_exact_quantile_rejects_unsupported_native_input(dtype: str) -> None:
    table = ibis.table({"x": dtype}, name="facts")
    with pytest.raises(DatasourceSourceCapabilityError, match="clickhouse") as error:
        compiler_backend("clickhouse").compile(table.x.quantile(0.95))
    assert str(ibis.dtype(dtype)) in str(error.value)
