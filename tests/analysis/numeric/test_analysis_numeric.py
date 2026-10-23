"""Definition-owned exactness and independent numerical oracles."""

from __future__ import annotations

from typing import Literal

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.errors import AnalysisError
from marivo.semantic.ir import AggKind
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models, terminal_runs
from tests.support.paths import PROJECT_ROOT


def _distribution(case: DslCase, agg: AggKind, *, overall: bool = False) -> mv.AnalysisFrame:
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
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"),)
        if overall
        else mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            session.members(session.catalog.entities.get("sales.customer")),
        ),
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
        models.read_text().replace("parse=ms.timestamp(", "is_default=True, parse=ms.timestamp(", 1)
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    observed = _distribution(case, agg)
    fixed = observed.compute()
    frame = fixed.to_pandas()
    rows = frame.set_index(ordinary_columns(frame)[0])[("value", "value")]
    if agg in ("count_distinct", "approx_count_distinct"):
        assert rows.tolist() == [1, 1, 1, 0]
    else:
        assert rows.loc[["A", "B", "C"]].tolist() == [450, 150, 400]
        assert rows.loc[["D"]].isna().all()
    assert observed.reduce({"rows": mv.row_count()}).compute().to_pandas()[
        ("value", "rows")
    ].tolist() == [4]
    if agg not in ("approx_count_distinct", "approx_median") and not (
        isinstance(agg, tuple) and agg[0] == "approx_percentile"
    ):
        total = (
            _distribution(case, agg, overall=True).compute().to_pandas()[("value", "value")].iloc[0]
        )
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
            duplicate_result = _distribution(case, duplicate_agg).compute().to_pandas()
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
    result_frame = _distribution(case, ("percentile", 0.25)).compute().to_pandas()
    result = result_frame.set_index(ordinary_columns(result_frame)[0]).loc["A", ("value", "value")]
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
        for index, (member, seed_amount, seed_weight, seed_decimal_amount) in enumerate(
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
                    seed_amount,
                    seed_weight,
                    seed_decimal_amount,
                ],
            )
    models = case.root / "models/semantic/sales/models.py"
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\ndecimal_amount = ms.measure_column(name='decimal_amount', entity=orders, column='decimal_amount', additivity=ms.additive_all())\n"
    models.write_text(
        models.read_text().replace("# Named expressions", physical + "# Named expressions", 1)
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    decimal_amount = session.catalog.fields.get("sales.order.decimal_amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    members = session.members(session.catalog.entities.get("sales.customer"))
    for metric, expected, by_member in (
        (amount.mean(time=time), 4.0, [2.0, 8.0]),
        (amount.weighted_mean(weight=weight, time=time), 20.75 / 4, [2.375, 8.0]),
    ):
        observed = session.observe({"value": metric}, by=members)
        frame = observed.compute().to_pandas()[("value", "value")]
        assert frame.iloc[:2].tolist() == by_member
        assert frame.iloc[2:].isna().all()
        assert session.observe(
            {"value": metric}, where=mv.in_population(members)
        ).compute().to_pandas()[("value", "value")].tolist() == [expected]
    for metric, decimal_expected in (
        (decimal_amount.sum(time=time), "1000.75"),
        (decimal_amount.min(time=time), "150.25"),
        (decimal_amount.max(time=time), "450.25"),
    ):
        logical = session.observe(
            {"value": metric},
            by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
        )
        decimal_frame = logical.compute().to_pandas()
        assert len(decimal_frame) == 4
        assert session.observe(
            {"value": metric},
            by=(session.time_scope(start="2026-08-01", end="2026-09-01"),),
            where=mv.in_population(members),
        ).compute().to_pandas()[("value", "value")].tolist() == [Decimal(decimal_expected)]
        assert logical.reduce({"rows": mv.row_count()}).compute().to_pandas()[
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
        models.read_text().replace("# Named expressions", physical + "# Named expressions", 1)
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    floating_value = session.catalog.fields.get("sales.order.float_amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    members = session.members(session.catalog.entities.get("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    customer_id = session.catalog.fields.get("sales.order.customer_id")
    numbers = [Fraction(amount) for _, amount, _ in facts if amount is not None]
    pairs = [
        (Fraction(amount), Fraction(weight))
        for _, amount, weight in facts
        if amount is not None and weight is not None
    ]
    kinds = ("sum", "min", "max", "mean", "weighted", "ratio", "linear")
    base = value.sum(time=time)
    expressions: dict[str, Deferred] = {
        "sum": base,
        "min": floating_value.min(time=time),
        "max": value.max(time=time),
        "mean": value.mean(time=time),
        "weighted": value.weighted_mean(weight=weight, time=time),
        "ratio": base / base,
        "linear": base + base,
    }
    member_kinds = ("sum", "min", "max", "mean", "ratio")
    member_rows = (
        session.observe({kind: expressions[kind] for kind in member_kinds}, by=members)
        .compute()
        .to_pandas()
    )
    coordinate_kinds = ("weighted", "linear")
    coordinate_rows = (
        session.observe(
            {kind: expressions[kind] for kind in coordinate_kinds},
            by=(customer_id, channel),
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
    )
    rolled_rows = (
        session.observe(
            {kind: expressions[kind] for kind in kinds},
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
    )
    grouped = session.observe(
        {kind: expressions[kind] for kind in coordinate_kinds},
        by=(channel,),
        where=mv.in_population(members),
    )
    assert grouped.output_names == coordinate_kinds

    for kind in kinds:
        floating = kind == "min"
        coordinates = kind in ("weighted", "linear")
        fine = coordinate_rows if coordinates else member_rows
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
                fine.set_index(ordinary_columns(fine)[0])[("value", kind)].to_dict()
                == expected_by_member
            )
        if kind == "weighted":
            assert (
                fine.loc[fine[ordinary_columns(fine)[0]].isin(("C", "D")), ("value", kind)]
                .isna()
                .all()
            )
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
                    Decimal("0.00001") if kind == "linear" else Decimal("0.000001"),
                    rounding=ROUND_HALF_EVEN,
                )
            )
        actual = rolled_rows[("value", kind)].tolist()
        assert (
            actual == pytest.approx([oracle])
            if floating or kind in ("weighted", "ratio")
            else actual == [oracle]
        ), kind
        if kind == "mean":
            backend = ibis.duckdb.connect(str(case.database_path))
            try:
                table = backend.table("order")
                native = backend.to_pyarrow(
                    table.group_by("customer_id").aggregate(value=table.amount.mean())
                ).to_pandas()
            finally:
                backend.disconnect()
            assert fine.set_index(ordinary_columns(fine)[0])[("value", "mean")].to_dict() == dict(
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
        for index, (member, ticks, seed_weight) in enumerate(
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
                    seed_weight,
                ],
            )
        if not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
    models = case.root / "models/semantic/sales/models.py"
    physical = "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    models.write_text(
        models.read_text().replace("# Named expressions", physical + "# Named expressions", 1)
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    channel = session.catalog.fields.get("sales.order.channel")
    customer_id = session.catalog.fields.get("sales.order.customer_id")
    expressions: dict[str, Deferred] = {}
    for kind in kinds:
        expressions[kind] = (
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
    member_kinds = tuple(kind for kind in kinds if kind not in ("weighted", "linear"))
    coordinate_kinds = tuple(kind for kind in kinds if kind in ("weighted", "linear"))
    member_fine = (
        session.observe({kind: expressions[kind] for kind in member_kinds}, by=members)
        .compute()
        .to_pandas()
        if member_kinds
        else None
    )
    coordinate_fine = (
        session.observe(
            {kind: expressions[kind] for kind in coordinate_kinds},
            by=(customer_id, channel),
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
        if coordinate_kinds
        else None
    )
    overall_kinds = tuple(kind for kind in kinds if kind != "count_distinct")
    overall = (
        session.observe(
            {kind: expressions[kind] for kind in overall_kinds},
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
        if overall_kinds
        else None
    )
    for kind in kinds:
        coordinates = kind in ("weighted", "linear")
        fine = coordinate_fine if coordinates else member_fine
        assert fine is not None
        if kind == "count_distinct":
            assert fine[("value", kind)].tolist() == [2, 2, 0, 0]
        else:
            assert overall is not None
            expected = {
                "sum": 7,
                "min": 0,
                "max": 4,
                "mean": round(Fraction(7, 4)),
                "weighted": round(Fraction(13, 6)),
                "ratio": 1.0,
                "linear": 14,
            }[kind]
            if kind == "ratio":
                assert overall[("value", kind)].tolist() == [expected]
            else:
                actual = (
                    pa.array(overall[("value", kind)])
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
        for index, (member, day, seed_value) in enumerate(
            [("A", 1, 0), ("A", 2, 1), ("B", 1, 2), ("B", 2, 4)]
        ):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), member, "web", "paid", f"2026-08-0{day}T00:00:00Z", seed_value],
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
    members = session.members(session.catalog.entities.get("sales.customer"))
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
            by=mv.product(session.time_scope(start="2026-08-01", end="2026-08-03"), members),
        )
        fine = logical.compute().to_pandas()
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
        for result in (
            session.observe(
                {"value": metric},
                by=(session.time_scope(start="2026-08-01", end="2026-08-03"),),
                where=mv.in_population(members),
            ).compute(),
        ):
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


@pytest.mark.release
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
        path.read_text().replace("# Named expressions", physical + "# Named expressions", 1)
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
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
import marivo.semantic as ms
from tests.analysis.materialization.domain_recovery_worker import snapshot
ms.load(workspace_dir='.')
session = mv.session.resume(sys.argv[1])
amount = session.catalog.fields.get('sales.order.amount')
time = session.catalog.fields.get('sales.order.ordered_at')
base = amount.sum(time=time)
metric = base if sys.argv[2] == 'decimal' else base.per(time).mean()
members = session.members(session.catalog.entities.get('sales.customer'))
scope = session.time_scope(start='2026-08-01',end='2026-08-03')
query = session.observe({'value':metric}, by=mv.product(scope, members))
fine = query.persist()
total = session.observe({'value':metric}, by=(scope,), where=mv.in_population(members)).persist()
artifacts = {'fine':fine.artifact.id,'total':total.artifact.id}
Path('recovery-manifests.json').write_text(json.dumps({name:snapshot(value) for name,value in (('fine',fine),('total',total))}))
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
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
import marivo.semantic as ms
from tests.analysis.materialization.domain_recovery_worker import snapshot
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('numeric output consulted a source')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
manifests = json.loads(Path('recovery-manifests.json').read_text())
session = mv.session.resume(sys.argv[1])
before = run_ids(session)
output = {}
for name, artifact in json.loads(sys.argv[2]).items():
    saved = session.persisted_frame(artifact)
    assert snapshot(saved) == manifests[name]
    frame = saved.to_pandas()
    values = frame[('value','value')]
    if sys.argv[3] == 'duration':
        values = pa.array(values).cast(pa.duration('ns')).cast(pa.int64()).to_pylist()
    output[name] = {'values':[str(value) for value in values], 'artifact':saved.artifact.id}
assert run_ids(session) == before
print(json.dumps(output, sort_keys=True))
"""
    first = run(consumer, json.dumps(artifacts), family)
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
    if case_kind in ("nonfinite", "calendar"):
        with pytest.raises(AnalysisError):
            logical.compute()
    else:
        expected = float(sum(numbers)) / len(numbers) if case_kind == "large_mean" else sum(numbers)
        fixed = logical.persist()
        assert fixed.to_pandas()[("value", "boundary")].tolist() == [expected]
        assert case.session.observe({"boundary": metric}).compute().to_pandas()[
            ("value", "boundary")
        ].tolist() == [expected]
        assert case.session.persisted_frame(fixed.artifact.id).to_pandas().equals(fixed.to_pandas())


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
    assert terminal_runs(session) == ()
    for q, expected in ((0, 150), (1, 450)):
        result = session.observe(
            {"value": value.quantile(q, time=time)},
            by=(session.time_scope(start="2026-08-01", end="2026-09-01"),),
        ).compute()
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
    fingerprints = set()
    for query in queries:
        backing = query._backing
        assert isinstance(backing, ExpressionFrameBacking)
        assert isinstance(backing.root, SourceFrameBinding)
        fingerprints.add(backing.root.graph.fingerprint)
    assert len(fingerprints) == len(variants)
    assert terminal_runs(case.session) == ()


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
        path.read_text().replace("# Named expressions", physical + "# Named expressions", 1)
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    time = session.catalog.fields.get("sales.order.ordered_at")
    first, small, cancel = (
        session.catalog.fields.get("sales.order." + name).sum(time=time)
        for name in ("amount", "small", "cancel")
    )
    members = session.members(session.catalog.entities.get("sales.customer"))
    original = session.observe({"value": first + small - cancel}, by=members)
    # Integer SUM outputs are Decimal, so this formerly overflowing int64 child
    # is now representable without changing the equation.
    logical = original
    oracle = Fraction(large) + 1 - Fraction(large)
    for result in (
        logical.compute(),
        session.observe(
            {"value": first + small - cancel if floating else first - cancel + small},
            where=mv.in_population(members),
        ).compute(),
    ):
        actual = result.to_pandas()[("value", "value")].iloc[0]
        if floating:
            assert abs(actual - float(oracle)) <= (2 * abs(large) + 1) * 1e-12 + 1e-12
        else:
            assert actual == oracle


def test_current_row_counts_keep_numeric_null_policy(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")
    session = case.session
    value = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    logical = session.observe(
        {"value": value.sum(time=time)},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            session.members(session.catalog.entities.get("sales.customer")),
        ),
    )
    frame = logical.compute().to_pandas()
    assert len(frame) == 4 and frame[("value", "value")].notna().sum() == 4
    assert logical.reduce({"count": mv.row_count()}).compute().to_pandas()[
        ("value", "count")
    ].tolist() == [4]
    defined = logical.where(~logical["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).compute().to_pandas()[
        ("value", "count")
    ].tolist() == [4]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid statistic read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    before = terminal_runs(session)
    with pytest.raises(AnalysisError) as caught:
        logical.reduce({"invalid": object()})  # type: ignore[dict-item]  # Exercise the runtime statistic boundary.
    error = caught.value
    assert error.expected == "an unfinished Column statistic or row_count()"
    assert error.received == "object"
    assert error.repair is not None and "statistic" in error.repair.action
    assert terminal_runs(session) == before
