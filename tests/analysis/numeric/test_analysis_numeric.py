"""Definition-owned exactness and independent numerical oracles."""

from __future__ import annotations

from typing import Literal

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.semantic.ir import AggKind
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


def _distribution(case: DslCase, agg: AggKind) -> mv.Query:
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    metric: Deferred
    if agg == "count_distinct":
        metric = amount.count_distinct(time=time)
    elif agg == "approx_count_distinct":
        metric = amount.approx_nunique(time=time)
    elif agg == "median":
        metric = amount.median(time=time)
    elif agg == "approx_median":
        metric = amount.approx_median(time=time)
    elif isinstance(agg, tuple) and agg[0] == "percentile":
        metric = amount.quantile(agg[1], time=time)
    elif isinstance(agg, tuple) and agg[0] == "approx_percentile":
        metric = amount.approx_quantile(agg[1], time=time)
    else:
        raise AssertionError(f"Unexpected distribution fixture: {agg!r}")
    return session.observe(
        {"value": metric},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.customer")),
    )


@pytest.mark.parametrize(
    "agg,parquet,floating",
    [
        ("count_distinct", False, False),
        ("approx_count_distinct", True, True),
        ("median", False, True),
        ("approx_median", True, False),
        (("percentile", 0.25), True, True),
        (("approx_percentile", 0.25), False, False),
    ],
)
def test_direct_distribution(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, parquet: bool, floating: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    if floating:
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
    observed = _distribution(case, agg)
    if isinstance(agg, tuple) and agg[0] == "approx_percentile":
        with pytest.raises(AnalysisError) as caught:
            observed.execute()
        assert isinstance(caught.value.__cause__, ms.errors.SemanticExpressionError)
        assert caught.value.__cause__.expected == "a qualified approximate quantile route"
        assert case.session.runs().items == ()
        # Choose the supported exact algorithm explicitly on the same input rows.
        agg = ("percentile", agg[1])
        observed = _distribution(case, agg)
    fixed = observed.execute()
    frame = fixed.to_pandas()
    rows = frame.set_index(frame.columns[0])[("value", "value")]
    if agg in ("count_distinct", "approx_count_distinct"):
        assert rows.tolist() == [1, 1, 1, 0]
    else:
        assert rows.loc[["A", "B", "C"]].tolist() == [450, 150, 400]
        assert rows.loc[["D"]].isna().all()
    assert observed.reduce({"rows": mv.row_count()}).execute().to_pandas()[
        ("value", "rows")
    ].tolist() == [4]
    if agg in ("approx_count_distinct", "approx_median") or (
        isinstance(agg, tuple) and agg[0] == "approx_percentile"
    ):
        with pytest.raises(AnalysisError):
            observed.rollup().execute()
    else:
        total = observed.rollup().execute().to_pandas()[("value", "value")].iloc[0]
        expected = 3 if agg == "count_distinct" else 400 if agg == "median" else 275
        assert total == expected
    if agg == "count_distinct" and not parquet:
        with duckdb.connect(str(case.database_path)) as db:
            db.execute('DELETE FROM "order"')
            for index, number in enumerate([1, 1, 2, 4, None]):
                db.execute(
                    'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                    [str(index), "A", "web", "paid", "2026-08-15", number],
                )
        duplicate_cases: tuple[tuple[AggKind, int | float], ...] = (
            ("count_distinct", 3),
            ("median", 1.5),
            (("percentile", 0.75), 2.5),
        )
        for duplicate_agg, duplicate_expected in duplicate_cases:
            duplicate_result = _distribution(case, duplicate_agg).execute().to_pandas()
            assert duplicate_result[("value", "value")].iloc[0] == duplicate_expected


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
    result_frame = _distribution(case, ("percentile", 0.25)).execute().to_pandas()
    result = result_frame.set_index(result_frame.columns[0]).loc["A", ("value", "value")]
    # Native DuckDB interpolation rounds int64 through double and truncates to Decimal scale.
    expected = Decimal("1.01") if decimal_input else float(2**53)
    assert result == expected
    if decimal_input:
        assert isinstance(result, Decimal)


def test_float_mean_and_decimal_original_state(analysis_dsl_case_factory: DslCaseFactory) -> None:
    from decimal import Decimal

    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        db.execute('ALTER TABLE "order" ADD COLUMN weight DOUBLE')
        db.execute('ALTER TABLE "order" ADD COLUMN decimal_amount DECIMAL(18,2)')
        db.execute('DELETE FROM "order"')
        for index, (member, amount, weight, decimal_amount) in enumerate(
            [
                ("A", 1.25, 0.5, "450.25"),
                ("A", 2.75, 1.5, "150.25"),
                ("B", 8.0, 2.0, "400.25"),
                ("B", None, 99.0, None),
            ]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    str(index),
                    member,
                    "web",
                    "paid",
                    "2026-08-15",
                    amount,
                    weight,
                    decimal_amount,
                ],
            )
    models = case.root / "models/semantic/sales/models.py"
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\ndecimal_amount = ms.measure_column(name='decimal_amount', entity=orders, column='decimal_amount', additivity=ms.additive_all())\n"
    models.write_text(
        models.read_text().replace(
            "finalized = ms.finalize()", physical + "finalized = ms.finalize()", 1
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    decimal_amount = session.catalog.fields.get("sales.order.decimal_amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    members = session.members(ms.ref.entity("sales.customer"))
    for metric, expected, by_member in (
        (amount.mean(time=time), 4.0, [2.0, 8.0]),
        (amount.weighted_mean(weight=weight, time=time), 20.75 / 4, [2.375, 8.0]),
    ):
        observed = session.observe({"value": metric}, by=members)
        frame = observed.execute().to_pandas()[("value", "value")]
        assert frame.iloc[:2].tolist() == by_member
        assert frame.iloc[2:].isna().all()
        assert observed.rollup().execute().to_pandas()[("value", "value")].tolist() == [expected]
    for metric, decimal_expected in (
        (decimal_amount.sum(time=time), "1000.75"),
        (decimal_amount.min(time=time), "150.25"),
        (decimal_amount.max(time=time), "450.25"),
    ):
        logical = session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=members,
        )
        decimal_frame = logical.execute().to_pandas()
        assert len(decimal_frame) == 4
        assert logical.rollup().execute().to_pandas()[("value", "value")].tolist() == [
            Decimal(decimal_expected)
        ]
        assert logical.reduce({"rows": mv.row_count()}).execute().to_pandas()[
            ("value", "rows")
        ].tolist() == [4]


def test_numeric_composition_matrix(analysis_dsl_case_factory: DslCaseFactory) -> None:
    from decimal import ROUND_HALF_EVEN, Decimal, localcontext
    from fractions import Fraction

    import duckdb
    import ibis

    case = analysis_dsl_case_factory("j1")
    dtype = "DECIMAL(30,6)"
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
        for index, (member, amount, weight_text) in enumerate(facts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    str(index),
                    member,
                    "web" if index % 2 else "app",
                    "paid",
                    "2026-08-15",
                    amount,
                    weight_text,
                ],
            )
        db.execute('ALTER TABLE "order" ADD COLUMN float_amount DOUBLE')
        db.execute('UPDATE "order" SET float_amount = CAST(amount AS DOUBLE)')
    models = case.root / "models/semantic/sales/models.py"
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\nfloat_amount = ms.measure_column(name='float_amount', entity=orders, column='float_amount', additivity=ms.additive_all())\n"
    models.write_text(
        models.read_text().replace(
            "finalized = ms.finalize()", physical + "finalized = ms.finalize()", 1
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    floating_value = session.catalog.fields.get("sales.order.float_amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    members = session.members(ms.ref.entity("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    customer_id = session.catalog.fields.get("sales.order.customer_id")
    numbers = [Fraction(amount) for _, amount, _ in facts if amount is not None]
    pairs = [
        (Fraction(amount), Fraction(weight))
        for _, amount, weight in facts
        if amount is not None and weight is not None
    ]
    kinds = ("sum", "min", "max", "mean", "weighted", "ratio", "linear")
    for kind in kinds:
        floating = kind == "min"
        source_value = floating_value if floating else value
        base = source_value.sum(time=time)
        expression = (
            base
            if kind == "sum"
            else source_value.min(time=time)
            if kind == "min"
            else source_value.max(time=time)
            if kind == "max"
            else source_value.mean(time=time)
            if kind == "mean"
            else value.weighted_mean(weight=weight, time=time)
            if kind == "weighted"
            else base / base
            if kind == "ratio"
            else base + base
        )
        coordinates = kind in ("weighted", "linear")
        logical = session.observe(
            {"value": expression},
            by=(customer_id, channel) if coordinates else members,
            members=members if coordinates else None,
        )
        fine = logical.execute().to_pandas()
        if kind in ("min", "max"):
            expected_by_member = (
                {"A": 1.0, "B": 4.000001, "C": 7.0, "D": 3.0}
                if kind == "min"
                else {
                    "A": Decimal("1.000001"),
                    "B": Decimal("4.000001"),
                    "C": Decimal("7.000000"),
                    "D": Decimal("3.000000"),
                }
            )
            assert (
                fine.set_index(fine.columns[0])[("value", "value")].to_dict() == expected_by_member
            )
        if kind == "weighted":
            assert fine.loc[fine.iloc[:, 0].isin(("C", "D")), ("value", "value")].isna().all()
        expected = (
            sum(numbers, Fraction())
            if kind == "sum"
            else min(numbers)
            if kind == "min"
            else max(numbers)
            if kind == "max"
            else sum(numbers, Fraction()) / len(numbers)
            if kind == "mean"
            else sum((a * w for a, w in pairs), Fraction()) / sum((w for _, w in pairs), Fraction())
            if kind == "weighted"
            else Fraction(1)
            if kind == "ratio"
            else sum(numbers, Fraction()) * 2
        )
        with localcontext() as context:
            context.prec = 100
            oracle = (
                float(expected)
                if floating or kind in ("weighted", "ratio")
                else (Decimal(expected.numerator) / Decimal(expected.denominator)).quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_EVEN
                )
            )
        rolled = logical.rollup()
        actual = rolled.execute().to_pandas()[("value", "value")].tolist()
        assert (
            actual == pytest.approx([oracle])
            if floating or kind in ("weighted", "ratio")
            else actual == [oracle]
        ), kind
        if coordinates:
            again = (
                logical.rollup(by=(channel,))
                .rollup()
                .execute()
                .to_pandas()[("value", "value")]
                .tolist()
            )
            assert again == pytest.approx([oracle]) if kind == "weighted" else again == [oracle]
        if kind == "mean":
            backend = ibis.duckdb.connect(str(case.database_path))
            try:
                table = backend.table("order")
                native = backend.execute(
                    table.group_by("customer_id").aggregate(value=table.amount.mean())
                )
            finally:
                backend.disconnect()
            assert fine.set_index(fine.columns[0])[("value", "value")].to_dict() == dict(
                zip(native.customer_id, native.value, strict=True)
            )


@pytest.mark.parametrize(
    "unit,parquet,kinds",
    [
        ("us", False, ("sum", "linear")),
        ("ns", True, ("mean", "weighted", "ratio", "count_distinct")),
    ],
)
def test_duration_matrix(
    analysis_dsl_case_factory: DslCaseFactory,
    unit: str,
    parquet: bool,
    kinds: tuple[
        Literal["sum", "min", "max", "mean", "weighted", "ratio", "linear", "count_distinct"],
        ...,
    ],
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
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    models.write_text(
        models.read_text().replace(
            "finalized = ms.finalize()", physical + "finalized = ms.finalize()", 1
        )
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
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    time = session.catalog.fields.get("sales.order.ordered_at")
    base = value.sum(time=time)
    members = session.members(ms.ref.entity("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    customer_id = session.catalog.fields.get("sales.order.customer_id")
    for kind in kinds:
        coordinates = kind in ("weighted", "linear")
        expression = (
            value.weighted_mean(weight=weight, time=time)
            if kind == "weighted"
            else base / base
            if kind == "ratio"
            else base + base
            if kind == "linear"
            else value.mean(time=time)
            if kind == "mean"
            else value.min(time=time)
            if kind == "min"
            else value.max(time=time)
            if kind == "max"
            else value.count_distinct(time=time)
            if kind == "count_distinct"
            else base
        )
        logical = session.observe(
            {"value": expression},
            by=(customer_id, channel) if coordinates else members,
            members=members if coordinates else None,
        )
        fine = logical.execute().to_pandas()
        if kind == "count_distinct":
            assert fine[("value", "value")].tolist() == [2, 2, 0, 0]
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
            results = [logical.rollup().execute()]
            if coordinates:
                results.append(logical.rollup(by=(channel,)).rollup().execute())
            for result in results:
                frame = result.to_pandas()
                if kind == "ratio":
                    assert frame[("value", "value")].tolist() == [expected]
                else:
                    actual = (
                        pa.array(frame[("value", "value")])
                        .cast(pa.duration(unit))
                        .cast(pa.int64())
                        .to_pylist()
                    )
                    assert actual == [expected]
        assert len(fine) == (5 if coordinates else 4)


@pytest.mark.parametrize(
    ("family", "kinds"),
    [
        ("int64", ("first", "max")),
        ("float64", ("last",)),
        ("decimal", ("mean", "cumulative")),
        ("duration", ("min",)),
    ],
)
def test_typed_temporal_matrix(
    analysis_dsl_case_factory: DslCaseFactory, family: str, kinds: tuple[str, ...]
) -> None:
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
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    members = session.members(ms.ref.entity("sales.customer"))
    for kind in kinds:
        base = value.sum(time=time)
        samples = base.per(time)
        metric = (
            base.window(mv.all_history())
            if kind == "cumulative"
            else samples.first()
            if kind == "first"
            else samples.last()
            if kind == "last"
            else samples.min()
            if kind == "min"
            else samples.max()
            if kind == "max"
            else samples.mean()
        )
        logical = session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-08-01", end="2026-08-03"),
            by=members,
        )
        fine = logical.execute().to_pandas()
        assert len(fine) == 2
        expected: int | float | Decimal = {
            "first": 2,
            "last": 5,
            "min": 2,
            "max": 5,
            "mean": 3.5,
            "cumulative": 7,
        }[kind]
        if family == "duration":
            expected = round(expected)
        elif family == "decimal":
            expected = Decimal(str(expected)).quantize(Decimal("0.000001"))
        for result in (logical.rollup().execute(), logical.rollup().rollup().execute()):
            frame = result.to_pandas()
            actual = (
                pa.array(frame[("value", "value")])
                .cast(pa.duration("us"))
                .cast(pa.int64())
                .to_pylist()
                if family == "duration"
                else frame[("value", "value")].tolist()
            )
            assert actual == [expected]


def test_native_integer_ratio_matches_independent_fraction() -> None:
    from fractions import Fraction

    import ibis

    from marivo.analysis.compiler.numeric_sql import integer_divide

    pairs = [
        (2**53 + 1, 3),
        (2**63 - 1, 2**63 - 3),
        (-(2**63), 7),
        (0, 7),
        (1, 2**63 - 1),
        (-1, 2),
        (3, 2),
        (2**62, 1),
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


@pytest.mark.parametrize("scale", [0, 18])
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


@pytest.mark.slow
@pytest.mark.parametrize(
    "family,parquet",
    [("decimal", True), ("duration", True)],
)
def test_numeric_states_produce_offline_and_cold(
    analysis_dsl_case_factory: DslCaseFactory, family: str, parquet: bool
) -> None:
    import json
    import os
    import subprocess
    import sys

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
    path = case.root / "models/semantic/sales/models.py"
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    path.write_text(
        path.read_text().replace(
            "finalized = ms.finalize()", physical + "finalized = ms.finalize()", 1
        )
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
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    producer = """
import json, sys
from pathlib import Path
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.terminal_protocol import encode
ms.load(workspace_dir='.')
session = mv.session.resume(sys.argv[1], by='id')
amount = session.catalog.fields.get('sales.order.amount')
time = session.catalog.fields.get('sales.order.ordered_at')
base = amount.sum(time=time)
metric = base if sys.argv[2] == 'decimal' else base.per(time).mean()
query = session.observe({'value':metric}, during=session.time_scope(start='2026-08-01',end='2026-08-03'), by=session.members(ms.ref.entity('sales.customer')))
fine, total = query.execute(), query.rollup().execute()
artifacts = {'fine':fine.id,'total':total.id}
Path('recovery-manifests.json').write_text(json.dumps({name:encode(value._manifest) for name,value in (('fine',fine),('total',total))}))
print(json.dumps(artifacts))
"""

    def run(script: str, *args: str) -> object:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, *args],
            cwd=case.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout.splitlines()[-1])

    artifacts = run(producer, family)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    if parquet:
        (case.root / "source_files").rename(case.root / "source_files.offline")
    consumer = """
import json, sys
from pathlib import Path
import duckdb, ibis
import pyarrow as pa
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.terminal_protocol import encode
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('numeric output consulted a source')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
manifests = json.loads(Path('recovery-manifests.json').read_text())
session = mv.session.resume(sys.argv[1], by='id')
before = run_ids(session)
output = {}
for name, artifact in json.loads(sys.argv[2]).items():
    saved = session.artifact(artifact)
    assert encode(saved._manifest) == manifests[name]
    frame = saved.to_pandas()
    values = frame[('value','value')]
    if sys.argv[3] == 'duration':
        values = pa.array(values).cast(pa.duration('ns')).cast(pa.int64()).to_pylist()
    output[name] = {'values':[str(value) for value in values], 'artifact':saved.id, 'run':saved._manifest.run_id}
assert run_ids(session) == before
print(json.dumps(output, sort_keys=True))
"""
    first = run(consumer, json.dumps(artifacts), family)
    second = run(consumer, json.dumps(artifacts), family)
    assert first == second
    assert isinstance(first, dict)
    expected = (
        {"fine": ["1.000000", "6.000000"], "total": ["7.000000"]}
        if family == "decimal"
        else {"fine": ["0", "3"], "total": ["4"]}
    )
    assert {name: data["values"] for name, data in first.items()} == expected


@pytest.mark.parametrize(
    "case_kind",
    ["large_mean", "cancellation", "overflow", "nonfinite", "calendar"],
)
def test_numeric_boundary_facts(analysis_dsl_case_factory: DslCaseFactory, case_kind: str) -> None:

    import duckdb

    case = analysis_dsl_case_factory("j1")
    numbers = (
        [2**53 + 1, 0, 0]
        if case_kind == "large_mean"
        else [2**63 - 1, 1, -(2**63 - 1)]
        if case_kind == "cancellation"
        else [2**63 - 1, 1]
    )
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
    amount = case.session.catalog.fields.get("sales.order.amount")
    metric = amount.mean() if case_kind == "large_mean" else amount.sum()
    customer = case.session.catalog.fields.get("sales.order.customer_id")
    logical = case.session.observe({"boundary": metric}, by=(customer,))
    if case_kind in ("overflow", "nonfinite", "calendar"):
        with pytest.raises(AnalysisError):
            logical.execute()
    else:
        expected = float(sum(numbers)) / len(numbers) if case_kind == "large_mean" else sum(numbers)
        fixed = logical.execute()
        assert fixed.to_pandas()[("value", "boundary")].tolist() == [expected]
        assert case.session.observe({"boundary": metric}).execute().to_pandas()[
            ("value", "boundary")
        ].tolist() == [expected]
        assert case.session.artifact(fixed.id).to_pandas().equals(fixed.to_pandas())


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
        exact = sum((Fraction(value) for value in values), Fraction()) / len(values)
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


def test_definition_quantile_checks_q_and_accepts_endpoints(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    for q in (True, -0.01, 1.01, float("nan"), float("inf")):
        for expression in (value.quantile(q, time=time), value.approx_quantile(q, time=time)):
            with pytest.raises(ms.errors.SemanticExpressionError):
                session.observe({"value": expression})
    assert session.runs().items == ()
    for q, expected in ((0, 150), (1, 450)):
        result = session.observe(
            {"value": value.quantile(q, time=time)},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        ).execute()
        assert result.to_pandas()[("value", "value")].tolist() == [expected]


def test_exact_and_approximate_definition_identities_never_alias(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    variants: list[AggKind] = [
        "count_distinct",
        "approx_count_distinct",
        "median",
        "approx_median",
        ("percentile", 0.25),
        ("approx_percentile", 0.25),
        ("percentile", 0.75),
    ]
    queries = [_distribution(case, agg) for agg in variants]
    assert len({query._graph.fingerprint for query in queries}) == len(variants)
    assert case.session.runs().items == ()


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
    physical = "small = ms.measure_column(name='small', entity=orders, column='small', additivity=ms.additive_all(), unit='CNY')\ncancel = ms.measure_column(name='cancel', entity=orders, column='cancel', additivity=ms.additive_all(), unit='CNY')\n"
    path.write_text(
        path.read_text().replace(
            "finalized = ms.finalize()", physical + "finalized = ms.finalize()", 1
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    time = session.catalog.fields.get("sales.order.ordered_at")
    first, small, cancel = (
        session.catalog.fields.get("sales.order." + name).sum(time=time)
        for name in ("amount", "small", "cancel")
    )
    members = session.members(ms.ref.entity("sales.customer"))
    original = session.observe({"value": first + small - cancel}, by=members)
    if not floating:
        # Each binary node checks its own domain; subtraction cannot erase the
        # overflowing maxint+1 child.
        for query in (original, original.rollup()):
            before = session.graph().artifacts
            with pytest.raises(AnalysisError) as caught:
                query.execute()
            assert isinstance(caught.value.__cause__, ms.errors.SemanticExpressionError)
            assert caught.value.__cause__.expected == "signed int64 arithmetic"
            assert session.graph().artifacts == before
    # This distinct bounded expression preserves the business cancellation.
    logical = (
        original if floating else session.observe({"value": (first - cancel) + small}, by=members)
    )
    oracle = Fraction(large) + 1 - Fraction(large)
    for result in (
        logical.execute(),
        logical.rollup().execute(),
        logical.rollup().rollup().execute(),
    ):
        actual = result.to_pandas()[("value", "value")].iloc[0]
        if floating:
            assert abs(actual - float(oracle)) <= (2 * abs(large) + 1) * 1e-12 + 1e-12
        else:
            assert actual == oracle


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


def test_current_row_counts_keep_numeric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    logical = session.observe(
        {"value": value.sum(time=time, empty="null")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.customer")),
    )
    frame = logical.execute().to_pandas()
    assert len(frame) == 4 and frame[("value", "value")].notna().sum() == 3
    assert logical.reduce({"count": mv.row_count()}).execute().to_pandas()[
        ("value", "count")
    ].tolist() == [4]
    defined = logical.where(~logical["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).execute().to_pandas()[
        ("value", "count")
    ].tolist() == [3]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid statistic read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    before = session.runs().items
    with pytest.raises(AnalysisError) as caught:
        logical.reduce({"invalid": object()})  # type: ignore[dict-item]  # Exercise the runtime statistic boundary.
    error = caught.value
    assert error.expected == "statistics from this exact Query"
    assert error.received == "object"
    assert error.repair is not None and "Column.sum/mean" in error.repair.action
    assert session.runs().items == before
