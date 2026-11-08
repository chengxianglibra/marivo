"""Native aggregate identity and composition through instance-scoped Ibis compilers."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest

from tests.datasource.aggregate_fixtures import compiler_backend
from tests.datasource.source_cases import SourceData, source_case, source_params

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


@pytest.mark.parametrize(
    ("backend", "operation"),
    [
        (backend, operation)
        for backend, row in EXPECTED.items()
        for operation, index in (
            ("count_distinct", 0),
            ("approx_nunique", 1),
            ("quantile", 2),
            ("median", 2),
            ("approx_quantile", 3),
            ("approx_median", 3),
        )
        if row[index] is not None
    ],
)
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
    sql = compiler_backend(backend).compile(expression).lower()
    assert expected in sql


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


def _uint64_sketch_query(table: ir.Table) -> ir.Table:
    from marivo._numeric_types import exact_unsigned
    from marivo.semantic._numeric_lowering import force_cast

    projected = table.select("k", v=exact_unsigned(table.v)).filter(lambda t: t.k != "z")
    value = projected.v
    sketch = force_cast(value.cast("float64").approx_quantile(0.5), value.type())
    return projected.group_by("k").aggregate(
        q=(value.count() == 0).ifelse(ibis.null().cast(value.type()), sketch),
        total=value.sum(),
    )


@pytest.mark.parametrize("backend", ["clickhouse", "duckdb"])
def test_uint64_sketch_inputs_skip_the_decimal_carrier(backend: str) -> None:
    from marivo._numeric_types import native_unsigned_aggregates

    expression = _uint64_sketch_query(ibis.table({"k": "string", "v": "uint64"}, name="t"))
    rewritten = native_unsigned_aggregates(expression)
    assert rewritten.schema() == expression.schema()
    sql = compiler_backend(backend).compile(rewritten).lower()
    carrier = "nullable(decimal(20, 0))" if backend == "clickhouse" else "decimal(20, 0)"
    floating = "nullable(float64)" if backend == "clickhouse" else "double"
    # The sketch and its COUNT guard read UInt64; exact SUM keeps the Decimal carrier.
    assert f'cast("t1"."v:uint64" as {floating})' in sql
    assert 'count("t1"."v:uint64")' in sql
    assert 'sum("t1"."v")' in sql
    assert native_unsigned_aggregates(rewritten).op() == rewritten.op()


def test_uint64_sketch_input_rewrite_preserves_native_results() -> None:
    import pandas as pd

    from marivo._numeric_types import native_unsigned_aggregates

    big = (2**53 + 1, 2**64 - 1, 2**63 + 1025, 7)
    con = ibis.duckdb.connect()
    con.raw_sql("CREATE TABLE t (k VARCHAR, v UBIGINT)")
    con.raw_sql(
        "INSERT INTO t VALUES "
        + ", ".join(f"('a', {value})" for value in big)
        + ", ('a', NULL), ('b', NULL), ('z', 1)"
    )
    expression = _uint64_sketch_query(con.table("t"))
    expected = con.to_pyarrow(expression.order_by("k")).to_pandas()
    actual = con.to_pyarrow(native_unsigned_aggregates(expression).order_by("k")).to_pandas()
    pd.testing.assert_frame_equal(actual, expected)
    assert expected["k"].tolist() == ["a", "b"] and pd.isna(expected["q"].iloc[1])


def test_uint64_rewrite_keeps_distinct_and_union_input_schemas() -> None:
    from marivo._numeric_types import exact_unsigned, native_unsigned_aggregates

    table = ibis.table({"v": "uint64"}, name="t")
    projected = table.select(v=exact_unsigned(table.v))
    filtered = projected.filter(projected.v.cast("float64") > 0)
    distinct = filtered.distinct()
    # The aggregate shares its Decimal input with the non-aggregate branch.
    aggregate = filtered.aggregate(v=filtered.v.count().cast("decimal(20,0)"))
    for query in (distinct, filtered.union(aggregate)):
        rewritten = native_unsigned_aggregates(query)
        assert rewritten.schema() == ibis.schema({"v": "decimal(20,0)"})
        for backend in ("clickhouse", "duckdb"):
            compiler_backend(backend).compile(rewritten)


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "mergetree")])
)
def test_uint64_extrema_keep_decimal_and_nulls_through_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, profile: str
) -> None:
    from marivo._numeric_types import exact_unsigned, native_unsigned_aggregates

    low, high = 2**53 + 1, 2**64 - 1
    source_rows: list[dict[str, object]] = [
        {"k": "a", "v": value} for value in (low, high, high, None)
    ]
    source_rows.append({"k": "b", "v": None})
    data = SourceData(
        "k VARCHAR, v UBIGINT",
        f"('a',{low}),('a',{high}),('a',{high}),('a',NULL),('b',NULL)",
        "k String, v Nullable(UInt64)",
        source_rows,
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        raw = case.session.bind(case.source, source_identity="unsigned-extrema").relation
        carrier = raw.mutate(exact=exact_unsigned(raw.v))
        aliased = carrier.select("k", amount=carrier.exact)
        query = aliased.group_by("k").aggregate(
            minimum=aliased.amount.min(),
            maximum=aliased.amount.max(),
            distinct=aliased.amount.nunique(),
            selected=aliased.amount.min(where=aliased.amount > low),
        )
        rewritten = native_unsigned_aggregates(query)
        assert rewritten.schema() == query.schema()
        assert native_unsigned_aggregates(rewritten).op() == rewritten.op()
        sql = case.session._backend.compile(rewritten).lower()
        assert "amount:uint64" in sql
        expected_type = "Nullable(Decimal(20, 0))" if backend == "clickhouse" else "DECIMAL(20,0)"
        rows = case.session.collect_bounded(
            rewritten.mutate(physical_type=rewritten.minimum.typeof()).order_by("k"),
            purpose="numeric.unsigned_extrema",
            max_rows=2,
        )
        assert rows["k"].tolist() == ["a", "b"]
        assert rows["distinct"].tolist() == [2, 0]
        assert rows["physical_type"].tolist() == [expected_type, expected_type]
        for name, expected in (("minimum", low), ("maximum", high), ("selected", high)):
            assert isinstance(rows[name].iloc[0], Decimal)
            assert rows[name].iloc[0] == Decimal(expected)
            assert pd.isna(rows[name].iloc[1])

        empty = aliased.filter(aliased.k == "absent")
        result = native_unsigned_aggregates(
            empty.aggregate(minimum=empty.amount.min(), maximum=empty.amount.max())
        )
        rows = case.session.collect_bounded(
            result.mutate(physical_type=result.maximum.typeof()),
            purpose="numeric.empty_extrema",
            max_rows=1,
        )
        assert len(rows) == 1 and rows[["minimum", "maximum"]].isna().all().all()
        assert rows["physical_type"].tolist() == [expected_type]
