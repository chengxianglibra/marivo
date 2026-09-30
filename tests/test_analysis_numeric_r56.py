"""Definition-owned exactness and independent R5.6 numerical oracles."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.semantic.ir import AggKind
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
@pytest.mark.parametrize(
    "agg",
    [
        "count_distinct",
        "approx_count_distinct",
        "median",
        "approx_median",
        ("percentile", 0.25),
        ("approx_percentile", 0.25),
    ],
)
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("floating", [False, True])
def test_direct_distribution(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, parquet: bool, floating: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    if floating:
        import duckdb

        with duckdb.connect(str(case.database_path)) as db:
            db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=agg, label="distribution"
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    if agg in ("count_distinct", "approx_count_distinct"):
        assert rows["value"].tolist() == [1, 1, 1, 0]
    else:
        assert rows.loc[["A", "B", "C"], "value"].tolist() == [450, 150, 400]
        assert rows.loc["D", "cell_tag"] == "null"
    assert not any(action.call == "relation.rollup()" for action in fixed.contract().actions)
    with pytest.raises(AnalysisError):
        fixed.rollup()
    assert fixed.summarize(mv.count()).execute().to_pandas()["value"].tolist() == [4]
    for relation in (observed, fixed):
        with pytest.raises(AnalysisError):
            relation.group_by(ms.ref.entity("sales.customer")).rollup()


@pytest.mark.runtime
@pytest.mark.parametrize("decimal_input", [False, True])
def test_native_quantile_precision_is_source_owned(
    analysis_dsl_case_factory: DslCaseFactory, decimal_input: bool
) -> None:
    from decimal import Decimal

    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        if decimal_input:
            db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(18,2)')
        db.execute('DELETE FROM "order"')
        amounts = [Decimal("1.01"), Decimal("1.02")] if decimal_input else [2**53 + 1, 2**53 + 3]
        for index, amount in enumerate(amounts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", "web", "paid", "2026-08-15", amount],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + "\nprecision = ms.aggregate(name='precision', measure=amount, agg=('percentile', 0.25), time=ordered_at)\n"
    )
    # Runtime aggregates use the Entity's declared default temporal axis.
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=("percentile", 0.25), label="exact"
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(metric, via=ms.ref.relationship("sales.order_buyer"))
        .execute()
    )
    result = fixed.to_pandas().set_index("member").loc["A", "value"]
    # Native DuckDB interpolation rounds int64 through double and truncates to Decimal scale.
    expected = Decimal("1.01") if decimal_input else float(2**53)
    assert result == expected
    if decimal_input:
        assert isinstance(result, Decimal)


@pytest.mark.runtime
@pytest.mark.parametrize("weighted", [False, True])
def test_float_original_mean_state(
    analysis_dsl_case_factory: DslCaseFactory, weighted: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        db.execute('ALTER TABLE "order" ADD COLUMN weight DOUBLE DEFAULT 0.5')
        db.execute('DELETE FROM "order"')
        for index, (member, amount, weight) in enumerate(
            [("A", 1.25, 0.5), ("A", 2.75, 1.5), ("B", 8.0, 2.0), ("B", None, 99.0)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [str(index), member, "web", "paid", "2026-08-15", amount, weight],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    ms.load(workspace_dir=case.root)
    metric = (
        mv.runtime_metric.weighted_mean(
            ms.ref.measure("sales.order.amount"),
            ms.ref.measure("sales.order.weight"),
            label="weighted",
        )
        if weighted
        else mv.runtime_metric.aggregate(
            ms.ref.measure("sales.order.amount"), agg="mean", label="mean"
        )
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric, via=ms.ref.relationship("sales.order_buyer")
    )
    fixed = observed.execute()
    expected = 20.75 / 4 if weighted else 4.0
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [expected]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [expected]


@pytest.mark.runtime
@pytest.mark.parametrize("agg, expected", [("min", 150), ("max", 450)])
def test_original_extrema(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, expected: int
) -> None:
    case = analysis_dsl_case_factory("j1")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=agg, label="extreme"
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = logical.execute()
    assert fixed.to_pandas().set_index("member").loc["D", "cell_tag"] == "null"
    assert logical.rollup().execute().to_pandas()["value"].tolist() == [expected]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [expected]


@pytest.mark.parametrize(
    "agg, backend, alternate",
    [
        ("count_distinct", "clickhouse", "approx_count_distinct"),
        ("median", "trino", "approx_median"),
        (("percentile", 0.95), "clickhouse", "('approx_percentile', 0.95)"),
        (("percentile", 0.95), "sqlite", "('approx_percentile', 0.95)"),
    ],
)
def test_unsupported_exact_aggregate_suggests_definition(
    agg: AggKind, backend: str, alternate: str
) -> None:
    from marivo.semantic._aggregate_accuracy import aggregate_repair

    action = aggregate_repair(agg, backend)
    assert action is not None and alternate in action
    if backend == "sqlite":
        assert "does not support" in action
    else:
        assert "If approximation is acceptable" in action


@pytest.mark.parametrize(
    "agg",
    [
        "count_distinct",
        "median",
        ("percentile", 0.95),
        "approx_count_distinct",
        "approx_median",
        ("approx_percentile", 0.95),
    ],
)
def test_duckdb_native_definitions_are_available(agg: AggKind) -> None:
    from marivo.semantic._aggregate_accuracy import aggregate_repair

    assert aggregate_repair(agg, "duckdb") is None


@pytest.mark.runtime
@pytest.mark.parametrize(
    "agg, expected", [("sum", "1000.75"), ("min", "150.25"), ("max", "450.25")]
)
def test_decimal_original_state(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, expected: str
) -> None:
    from decimal import Decimal

    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(18,2)')
        db.execute('UPDATE "order" SET amount=amount+0.25')
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=agg, label="decimal"
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = logical.execute()
    assert logical.rollup().execute().to_pandas()["value"].tolist() == [Decimal(expected)]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [Decimal(expected)]
    assert fixed.summarize(mv.count()).execute().to_pandas()["value"].tolist() == [4]


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["sum", "min", "max", "mean", "weighted", "ratio", "linear"])
@pytest.mark.parametrize("floating", [False, True])
@pytest.mark.parametrize("coordinates", [False, True])
def test_numeric_composition_matrix(
    analysis_dsl_case_factory: DslCaseFactory, kind: str, floating: bool, coordinates: bool
) -> None:
    from decimal import ROUND_HALF_EVEN, Decimal, localcontext
    from fractions import Fraction

    import duckdb

    case = analysis_dsl_case_factory("j1")
    dtype = "DOUBLE" if floating else "DECIMAL(30,6)"
    facts = [
        ("A", "1.000000", "1.000000"),
        ("A", "1.000001", "1.000000"),
        ("B", "4.000001", "2.000000"),
        ("B", None, "100.000000"),
        ("C", "7.000000", None),
        ("D", "3.000000", "0.000000"),
    ]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {dtype}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN weight {dtype}')
        db.execute('DELETE FROM "order"')
        for index, (member, amount, weight) in enumerate(facts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    str(index),
                    member,
                    "web" if index % 2 else "app",
                    "paid",
                    "2026-08-15",
                    amount,
                    weight,
                ],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    ms.load(workspace_dir=case.root)
    value, weight = ms.ref.measure("sales.order.amount"), ms.ref.measure("sales.order.weight")
    base = mv.runtime_metric.aggregate(value, agg="sum", label="sum")
    expr = (
        mv.runtime_metric.aggregate(value, agg=kind, label=kind)
        if kind in ("sum", "min", "max", "mean")
        else mv.runtime_metric.weighted_mean(value, weight, label="weighted")
        if kind == "weighted"
        else mv.runtime_metric.ratio(base, base, label="ratio")
        if kind == "ratio"
        else mv.runtime_metric.linear(add=[base, base], label="linear")
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        expr,
        via=ms.ref.relationship("sales.order_buyer"),
        coordinates=(ms.ref.dimension("sales.order.channel"),) if coordinates else (),
    )
    fixed = logical.execute()
    if kind == "weighted":
        assert "zero_weight_sum" in fixed.to_pandas().cell_reason.tolist()
    numbers = [Fraction(amount) for _, amount, _ in facts if amount is not None]
    pairs = [
        (Fraction(amount), Fraction(weight))
        for _, amount, weight in facts
        if amount is not None and weight is not None
    ]
    expected = (
        sum(numbers)
        if kind == "sum"
        else min(numbers)
        if kind == "min"
        else max(numbers)
        if kind == "max"
        else sum(numbers) / len(numbers)
        if kind == "mean"
        else sum(a * w for a, w in pairs) / sum(w for _, w in pairs)
        if kind == "weighted"
        else Fraction(1)
        if kind == "ratio"
        else sum(numbers) * 2
    )
    with localcontext() as context:
        context.prec = 100
        oracle = (
            float(expected)
            if floating
            else (Decimal(expected.numerator) / Decimal(expected.denominator)).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_EVEN
            )
        )
    results = [logical.rollup().execute(), fixed.rollup().execute()]
    if coordinates:
        results.append(
            fixed.group_by(ms.ref.dimension("sales.order.channel"))
            .rollup()
            .execute()
            .rollup()
            .execute()
        )
    for result in results:
        actual = result.to_pandas()["value"].tolist()
        assert actual == pytest.approx([oracle]) if floating else actual == [oracle]
    if not floating and kind == "mean":
        assert Decimal("1.000000") in fixed.to_pandas()["value"].tolist()


@pytest.mark.runtime
@pytest.mark.parametrize(
    "unit,parquet,coordinates",
    [
        ("us", False, False),
        ("s", True, False),
        ("ms", True, False),
        ("us", True, False),
        ("ns", True, False),
        ("ns", True, True),
    ],
)
@pytest.mark.parametrize(
    "kind", ["sum", "min", "max", "mean", "weighted", "ratio", "linear", "count_distinct"]
)
def test_duration_matrix(
    analysis_dsl_case_factory: DslCaseFactory,
    unit: str,
    parquet: bool,
    kind: str,
    coordinates: bool,
) -> None:
    from fractions import Fraction

    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT')
        db.execute('DELETE FROM "order"')
        for index, (member, ticks, weight) in enumerate(
            [("A", 0, 1), ("A", 1, 1), ("B", 2, 2), ("B", 4, 2), ("C", None, 99)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    str(index),
                    member,
                    "web" if index % 2 else "app",
                    "paid",
                    "2026-08-15",
                    ticks,
                    weight,
                ],
            )
        if not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        path = case.root / "source_files/order.parquet"
        data = pq.read_table(path)
        pq.write_table(
            data.set_column(
                data.schema.get_field_index("amount"),
                "amount",
                data["amount"].cast(pa.duration(unit)),
            ),
            path,
        )
    ms.load(workspace_dir=case.root)
    measure = ms.ref.measure("sales.order.amount")
    base = mv.runtime_metric.aggregate(measure, agg="sum", label="sum")
    expr = (
        mv.runtime_metric.weighted_mean(
            measure, ms.ref.measure("sales.order.weight"), label="weighted"
        )
        if kind == "weighted"
        else mv.runtime_metric.ratio(base, base, label="ratio")
        if kind == "ratio"
        else mv.runtime_metric.linear(add=[base, base], label="linear")
        if kind == "linear"
        else mv.runtime_metric.aggregate(measure, agg=kind, label=kind)
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        expr,
        via=ms.ref.relationship("sales.order_buyer"),
        coordinates=(ms.ref.dimension("sales.order.channel"),)
        if coordinates and kind != "count_distinct"
        else (),
    )
    fixed = logical.execute()
    if kind == "count_distinct":
        assert fixed.to_pandas()["value"].tolist() == [2, 2, 0, 0]
    else:
        expected = {
            "sum": 7,
            "min": 0,
            "max": 4,
            "mean": round(Fraction(7, 4)),
            "weighted": round(Fraction(13, 6)),
            "ratio": 1.0,
            "linear": 14,
        }[kind]
        results = [logical.rollup().execute(), fixed.rollup().execute()]
        if coordinates:
            results.append(
                fixed.group_by(ms.ref.dimension("sales.order.channel"))
                .rollup()
                .execute()
                .rollup()
                .execute()
            )
        for result in results:
            frame = result.to_pandas()
            if kind == "ratio":
                assert frame["value"].tolist() == [expected]
            else:
                actual = (
                    pa.array(frame["value"]).cast(pa.duration(unit)).cast(pa.int64()).to_pylist()
                )
                assert actual == [expected]
    assert fixed.summarize(mv.count()).execute().to_pandas()["value"].tolist() == [
        5 if coordinates and kind != "count_distinct" else 4
    ]


@pytest.mark.runtime
@pytest.mark.parametrize("family", ["int64", "float64", "decimal", "duration"])
@pytest.mark.parametrize("kind", ["first", "last", "mean", "min", "max", "cumulative"])
def test_typed_temporal_matrix(
    analysis_dsl_case_factory: DslCaseFactory, family: str, kind: str
) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    import duckdb
    import pyarrow as pa

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("DELETE FROM customer WHERE customer_id IN ('C','D')")
        db.execute('DELETE FROM "order"')
        for index, (member, day, value) in enumerate(
            [("A", 1, 0), ("A", 2, 1), ("B", 1, 2), ("B", 2, 4)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), member, "web", "paid", f"2026-08-0{day}T00:00:00Z", value],
            )
        if family == "decimal":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
        elif family == "float64":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        elif family == "duration":
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text()
        + f"""
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold={("mean" if kind == "cumulative" else kind)!r})
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
running = ms.cumulative(name='running', base=revenue)
"""
    )
    ms.load(workspace_dir=case.root)
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.running" if kind == "cumulative" else "sales.folded"),
        at=datetime(2026, 8, 3, tzinfo=timezone.utc) if kind == "cumulative" else None,
        during=None
        if kind == "cumulative"
        else mv.time_scope(start="2026-08-01", end="2026-08-03"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = logical.execute()
    expected = {"first": 2, "last": 5, "min": 2, "max": 5, "mean": 3.5, "cumulative": 7}[kind]
    if family == "duration":
        expected = round(expected)
    elif family == "decimal":
        expected = Decimal(str(expected)).quantize(Decimal("0.000001"))
    for result in (logical.rollup().execute(), fixed.rollup().execute()):
        frame = result.to_pandas()
        actual = (
            pa.array(frame.value).cast(pa.duration("us")).cast(pa.int64()).to_pylist()
            if family == "duration"
            else frame.value.tolist()
        )
        assert actual == [expected]


@pytest.mark.runtime
def test_native_integer_ratio_matches_independent_fraction() -> None:
    import random
    from fractions import Fraction

    import ibis

    from marivo.analysis.compiler.numeric_sql import integer_divide

    randomizer = random.Random(56)
    pairs = [(2**53 + 1, 3), (2**63 - 1, 2**63 - 3), (-(2**63), 7), (0, 7), (1, 2**63 - 1)]
    pairs += [
        (randomizer.randrange(-(2**63), 2**63), randomizer.randrange(1, 2**63)) for _ in range(200)
    ]
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "ratios", {"n": [n for n, _ in pairs], "d": [d for _, d in pairs]}
        )
        result = integer_divide(table, "n", "d").select("numeric_result").execute()
        assert result.numeric_result.tolist() == [float(Fraction(n, d)) for n, d in pairs]
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.parametrize("scale", [0, 2, 6, 18])
def test_native_decimal_ratio_one_half_even_round(scale: int) -> None:
    from decimal import ROUND_HALF_EVEN, Decimal, localcontext
    from fractions import Fraction

    import ibis
    import ibis.expr.datatypes as dt
    import pyarrow as pa

    from marivo.analysis.compiler.numeric_sql import decimal_divide

    pairs = [(1, 2), (3, 2), (-1, 2), (-3, 2), (10**30 + 1, 3)]
    with localcontext() as context:
        context.prec = 120
        values = [Decimal(n).scaleb(-scale) for n, _ in pairs]
        oracle = []
        output_scale = max(scale, 6)
        for (n, d), value in zip(pairs, values, strict=True):
            ratio = Fraction(value) / d
            oracle.append(
                (Decimal(ratio.numerator) / Decimal(ratio.denominator)).quantize(
                    Decimal(1).scaleb(-output_scale), rounding=ROUND_HALF_EVEN
                )
            )
        backend = ibis.duckdb.connect()
        try:
            table = backend.create_table(
                "ratios",
                pa.table(
                    {
                        "n": pa.array(values, type=pa.decimal128(38, scale)),
                        "d": [d for _, d in pairs],
                    }
                ),
            )
            result = (
                decimal_divide(table, "n", "d", dt.Decimal(38, output_scale))
                .select("numeric_result")
                .to_pyarrow()
            )
            assert result["numeric_result"].to_pylist() == oracle
        finally:
            backend.disconnect()


@pytest.mark.runtime
@pytest.mark.parametrize(
    "family,parquet",
    [
        (family, parquet)
        for family in ("int64", "float64", "decimal", "duration")
        for parquet in (False, True)
    ],
)
def test_numeric_states_produce_offline_and_cold(
    analysis_dsl_case_factory: DslCaseFactory, family: str, parquet: bool
) -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("DELETE FROM customer WHERE customer_id IN ('C','D')")
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT')
        for index, (member, day, amount, weight) in enumerate(
            [("A", 1, 0, 1), ("A", 2, 1, 1), ("B", 1, 2, 2), ("B", 2, 4, 2)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [str(index), member, "web", "paid", f"2026-08-0{day}T00:00:00Z", amount, weight],
            )
        if family == "decimal":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
            db.execute('ALTER TABLE "order" ALTER weight TYPE DECIMAL(30,6)')
        elif family == "float64":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
            db.execute('ALTER TABLE "order" ALTER weight TYPE DOUBLE')
        elif family == "duration" and not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + """
weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)), status_time_dimension=ordered_at, status_time_fold='mean')
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
"""
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if family == "duration":
            source = case.root / "source_files/order.parquet"
            data = pq.read_table(source)
            pq.write_table(
                data.set_column(
                    data.schema.get_field_index("amount"),
                    "amount",
                    data["amount"].cast(pa.duration("ns")),
                ),
                source,
            )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    producer = """
import json, sys
import marivo.analysis as mv
import marivo.semantic as ms
ms.load(workspace_dir='.')
session = mv.session.resume(sys.argv[1], by='id')
amount, weight = ms.ref.measure('sales.order.amount'), ms.ref.measure('sales.order.weight')
base = mv.runtime_metric.aggregate(amount, agg='sum', label='sum')
metrics = {kind: mv.runtime_metric.aggregate(amount, agg=kind, label=kind) for kind in ('sum','min','max','mean')}
metrics.update(weighted=mv.runtime_metric.weighted_mean(amount, weight, label='weighted'), ratio=mv.runtime_metric.ratio(base,base,label='ratio'), linear=mv.runtime_metric.linear(add=[base,base],label='linear'), fold=ms.ref.metric('sales.folded'))
artifacts = {}
contracts = {}
from dataclasses import asdict
from pathlib import Path
from marivo.analysis.materialization.graph_protocol import SIGNATURE, encode
for kind, metric in metrics.items():
    fixed = session.members(ms.ref.entity('sales.customer')).observe(metric, during=mv.time_scope(start='2026-08-01',end='2026-08-03'),via=ms.ref.relationship('sales.order_buyer')).execute()
    artifacts[kind] = fixed.state.artifact_ref.ref
    contracts[kind] = {'K': asdict(fixed.contract()), 'signature': encode(fixed._node.root.signature, SIGNATURE)}
Path('recovery-contracts.json').write_text(json.dumps(contracts))
print(json.dumps(artifacts))
"""

    def run(script: str, *args: str) -> object:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, *args],
            cwd=case.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout.splitlines()[-1])

    artifacts = run(producer)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    if parquet:
        (case.root / "source_files").rename(case.root / "source_files.offline")
    consumer = """
import json, sys
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_exchange import numeric_primary
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('fixed numeric continuation touched a source')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
from pathlib import Path
from dataclasses import asdict
from marivo.analysis.materialization.graph_protocol import SIGNATURE, encode
contracts = json.loads(Path('recovery-contracts.json').read_text())
session = mv.session.resume(sys.argv[1], by='id')
output = {}
for kind, artifact in json.loads(sys.argv[2]).items():
    fixed = session.artifact(artifact)
    assert any(action.call == 'relation.rollup()' for action in fixed.contract().actions)
    assert json.loads(json.dumps(asdict(fixed.contract()))) == contracts[kind]['K']
    assert encode(fixed._node.root.signature, SIGNATURE) == contracts[kind]['signature']
    result = fixed.rollup().execute()
    again = fixed.rollup().execute()
    assert again.state.artifact_ref == result.state.artifact_ref
    assert session._runtime.statistics.primary_queries == 0
    verified = result._dataset.verified()
    output[kind] = {'value': [str(value) for value in numeric_primary(verified.primary)['value'].to_pylist()], 'state': [part.table.to_pylist() for part in verified.parts if part.role == 'original_state'], 'K': asdict(result.contract()), 'signature': encode(result._node.root.signature, SIGNATURE), 'artifact': result.state.artifact_ref.ref, 'run': result.state.producing_run_ref, 'execution_key': result._dataset.artifact.descriptor.execution_key_digest}
print(json.dumps(output, default=str, sort_keys=True))
"""
    first = run(consumer, json.dumps(artifacts))
    second = run(consumer, json.dumps(artifacts))
    assert first == second
    expected = (
        {
            "sum": "7.000000",
            "min": "0.000000",
            "max": "4.000000",
            "mean": "1.750000",
            "weighted": "2.166667",
            "ratio": "1.000000",
            "linear": "14.000000",
            "fold": "3.500000",
        }
        if family == "decimal"
        else {
            "sum": "7",
            "min": "0",
            "max": "4",
            "mean": "2",
            "weighted": "2",
            "ratio": "1.0",
            "linear": "14",
            "fold": "4",
        }
    )
    if family in ("int64", "float64"):
        expected = {
            "sum": "7",
            "min": "0",
            "max": "4",
            "mean": "1.75",
            "weighted": "2.1666666666666665",
            "ratio": "1.0",
            "linear": "14",
            "fold": "3.5",
        }
        if family == "float64":
            for kind in ("sum", "min", "max", "linear"):
                expected[kind] += ".0"
    assert isinstance(first, dict)
    assert {kind: data["value"][0] for kind, data in first.items()} == expected

    # Each newly typed state is rejected when its file, receipt, or implementation
    # version is damaged. Restore only the test fixture between independent faults.
    from marivo.analysis.materialization import graph_store
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode

    assert isinstance(artifacts, dict)
    store = case.session._runtime.store
    for artifact in artifacts.values():
        with store._read() as connection:
            record = graph_store.artifact(store, connection, artifact)
        assert record is not None
        descriptor = encode(record.descriptor, DESCRIPTOR)
        part = next(part for part in record.descriptor.parts if part.role == "original_state")
        state_path = (
            case.root / part.local.project_relative_path / part.local.file_manifest[0].relative_path
        )
        original_bytes = state_path.read_bytes()
        for damage in ("missing", "corrupt", "receipt", "version"):
            if damage == "missing":
                state_path.unlink()
            elif damage == "corrupt":
                state_path.write_bytes(b"invalid numeric state")
            else:
                payload = json.loads(descriptor)
                if damage == "receipt":
                    payload["primary_receipt"]["input_binding"] = "foreign-numeric-state"
                else:
                    payload["method_state"]["contract_version"] += 1
                with store._write() as connection:
                    connection.execute(
                        "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                        (json.dumps(payload), artifact),
                    )
            try:
                with pytest.raises(AnalysisError):
                    case.session.artifact(artifact).rollup().execute()
            finally:
                state_path.write_bytes(original_bytes)
                with store._write() as connection:
                    connection.execute(
                        "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                        (descriptor, artifact),
                    )


@pytest.mark.runtime
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "case_kind", ["large_mean", "cancellation", "overflow", "nonfinite", "calendar"]
)
def test_numeric_boundary_facts(
    analysis_dsl_case_factory: DslCaseFactory, case_kind: str, reverse: bool
) -> None:
    from fractions import Fraction

    import duckdb

    case = analysis_dsl_case_factory("j1")
    numbers = (
        [2**53 + 1, 0, 0]
        if case_kind == "large_mean"
        else [2**63 - 1, 1, -(2**63 - 1)]
        if case_kind == "cancellation"
        else [2**63 - 1, 1]
    )
    if reverse:
        numbers.reverse()
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        for index, number in enumerate(numbers):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", "web", "paid", "2026-08-15", number],
            )
        if case_kind == "nonfinite":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
            db.execute("UPDATE \"order\" SET amount='Infinity'::DOUBLE")
        elif case_kind == "calendar":
            db.execute("ALTER TABLE \"order\" ALTER amount TYPE INTERVAL USING INTERVAL '1 month'")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"),
        agg="mean" if case_kind == "large_mean" else "sum",
        label="boundary",
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric, via=ms.ref.relationship("sales.order_buyer")
    )
    if case_kind in ("overflow", "nonfinite", "calendar"):
        with pytest.raises(AnalysisError):
            logical.execute()
    else:
        expected = (
            float(Fraction(sum(numbers), len(numbers)))
            if case_kind == "large_mean"
            else sum(numbers)
        )
        fixed = logical.execute()
        assert fixed.to_pandas().loc[0, "value"] == expected
        assert logical.rollup().execute().to_pandas().loc[0, "value"] == expected
        assert fixed.rollup().execute().to_pandas().loc[0, "value"] == expected


@pytest.mark.runtime
@pytest.mark.parametrize(
    "agg,expected",
    [
        ("count_distinct", 3),
        ("median", 1.5),
        (("percentile", 0.25), 1.0),
        (("percentile", 0.75), 2.5),
    ],
)
def test_distribution_duplicates_nulls_and_interpolation(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, expected: float
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        for index, number in enumerate([1, 1, 2, 4, None]):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", "web", "paid", "2026-08-15", number],
            )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    ms.load(workspace_dir=case.root)
    metric = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=agg, label="distribution"
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(metric, via=ms.ref.relationship("sales.order_buyer"))
        .execute()
    )
    assert fixed.to_pandas().loc[0, "value"] == expected
    with pytest.raises(AnalysisError):
        fixed.rollup()


def test_fixed_numeric_merge_order_and_partition_oracle() -> None:
    from decimal import Decimal
    from fractions import Fraction
    from itertools import permutations

    import pyarrow as pa

    from marivo.analysis.methods.numeric_state import merge_original
    from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType

    for values, carrier, output in (
        ([2**53 + 1, -(2**53), 2], pa.int64(), ScalarType("float64")),
        (
            [Decimal("1.000000"), Decimal("1.000001"), Decimal("4.000001")],
            pa.decimal128(38, 6),
            DecimalType(38, 6),
        ),
        ([0, 1, 3], pa.int64(), DurationType("ns")),
        ([1.0, 0.25, 3.0], pa.float64(), ScalarType("float64")),
    ):
        rows = [{"sum": value, "non_null_count": 1} for value in values]
        schema = pa.schema(
            [("original_state__sum", carrier), ("original_state__non_null_count", pa.int64())]
        )
        exact = sum(Fraction(value) for value in values) / len(values)
        expected = (
            round(exact)
            if isinstance(output, DurationType)
            else Decimal(exact.numerator) / Decimal(exact.denominator)
            if isinstance(output, DecimalType)
            else float(exact)
        )
        if isinstance(expected, Decimal):
            expected = expected.quantize(Decimal("0.000001"))
        for permutation in permutations(rows):
            direct = merge_original(
                permutation, schema, ("sum", "non_null_count"), "mean", output, ()
            )
            for split in (1, 2):
                left = merge_original(
                    permutation[:split], schema, ("sum", "non_null_count"), "mean", output, ()
                )
                right = merge_original(
                    permutation[split:], schema, ("sum", "non_null_count"), "mean", output, ()
                )
                tree = merge_original(
                    [left[0], right[0]], schema, ("sum", "non_null_count"), "mean", output, ()
                )
                assert direct == tree
                assert tree[1] == expected


@pytest.mark.parametrize("q", [True, False, 0, 1, -0.1, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("operation", ["percentile", "approx_percentile"])
def test_definition_quantile_rejects_invalid_q(q: object, operation: str) -> None:
    with pytest.raises(ValueError):
        mv.runtime_metric.aggregate(
            ms.ref.measure("sales.order.amount"), agg=(operation, q), label="invalid"
        )


def test_exact_approximate_replay_identities_never_alias() -> None:
    from marivo.semantic.runtime_metric import replay_payload

    measure = ms.ref.measure("sales.order.amount")
    variants = [
        "count_distinct",
        "approx_count_distinct",
        "median",
        "approx_median",
        ("percentile", 0.25),
        ("approx_percentile", 0.25),
        ("percentile", 0.75),
    ]
    payloads = [
        replay_payload(mv.runtime_metric.aggregate(measure, agg=agg, label="same"))
        for agg in variants
    ]
    assert len({repr(payload) for payload in payloads}) == len(variants)


def test_float_linear_validator_uses_owner_error_bound() -> None:
    from marivo.analysis.methods.state_validation import state_matches

    state = {
        "original_state__plus_0_sum": 1e16,
        "original_state__plus_0_non_null_count": 1,
        "original_state__plus_1_sum": 1.0,
        "original_state__plus_1_non_null_count": 1,
        "original_state__minus_2_sum": 1e16,
        "original_state__minus_2_non_null_count": 1,
    }
    for value, expected in ((0.0, True), (1.0, True), (1e9, False)):
        assert (
            state_matches(
                "original_linear",
                {"value": value, "cell_tag": "defined", "cell_reason": None},
                state,
                empty_rules=("null",) * 3,
            )
            is expected
        )


@pytest.mark.runtime
@pytest.mark.parametrize("floating", [False, True])
def test_linear_cancellation_checks_final_integer_and_float_bound(
    analysis_dsl_case_factory: DslCaseFactory, floating: bool
) -> None:
    from fractions import Fraction

    import duckdb

    case = analysis_dsl_case_factory("j1")
    large = 1e16 if floating else 2**63 - 1
    dtype = "DOUBLE" if floating else "BIGINT"
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("DELETE FROM customer WHERE customer_id IN ('B','C','D')")
        db.execute('DELETE FROM "order"')
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {dtype}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN small {dtype}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN cancel {dtype}')
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            ["one", "A", "web", "paid", "2026-08-15", large, 1, large],
        )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + """
small = ms.measure_column(name='small', entity=orders, column='small', additivity=ms.additive_all())
cancel = ms.measure_column(name='cancel', entity=orders, column='cancel', additivity=ms.additive_all())
"""
    )
    ms.load(workspace_dir=case.root)
    leaves = [
        mv.runtime_metric.aggregate(ms.ref.measure("sales.order." + name), agg="sum", label=name)
        for name in ("amount", "small", "cancel")
    ]
    expr = mv.runtime_metric.linear(add=leaves[:2], subtract=[leaves[2]], label="cancellation")
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        expr, via=ms.ref.relationship("sales.order_buyer")
    )
    fixed = logical.execute()
    oracle = Fraction(large) + 1 - Fraction(large)
    for result in (fixed, logical.rollup().execute(), fixed.rollup().execute()):
        actual = result.to_pandas().value.iloc[0]
        if floating:
            assert abs(actual - float(oracle)) <= (2 * abs(large) + 1) * 1e-12 + 1e-12
        else:
            assert actual == oracle


@pytest.mark.runtime
def test_native_duration_tick_rounding_ties_and_sign() -> None:
    from fractions import Fraction

    import ibis
    import ibis.expr.datatypes as dt

    from marivo.analysis.compiler.numeric_sql import decimal_divide

    numerators = [-5, -3, -1, 1, 3, 5]
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table("ticks", {"n": numerators, "d": [2] * len(numerators)})
        result = (
            decimal_divide(source, "n", "d", dt.Decimal(38, 0))
            .select("numeric_result")
            .to_pyarrow()
        )
        assert result["numeric_result"].to_pylist() == [round(Fraction(n, 2)) for n in numerators]
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.parametrize("family", ["int64", "float64", "decimal", "duration"])
def test_current_row_counts_keep_numeric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory, family: str
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        if family == "float64":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        elif family == "decimal":
            db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(18,2)')
        elif family == "duration":
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    ms.load(workspace_dir=case.root)
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = logical.execute()
    for relation in (logical, fixed):
        assert relation.summarize(mv.count()).execute().to_pandas().value.tolist() == [4]
        assert relation.summarize(mv.count_defined()).execute().to_pandas().value.tolist() == [3]
