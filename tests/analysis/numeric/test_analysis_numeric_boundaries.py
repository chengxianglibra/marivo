"""Independent regressions for numerical boundaries."""

from decimal import Decimal
from typing import Literal

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory, catalog_value, export_dsl_parquet_models


@pytest.mark.parametrize(
    "parquet,kind,split,coordinates",
    [
        (False, "ratio", False, False),
        (True, "ratio", True, True),
        (False, "weighted", True, False),
        (True, "weighted", False, True),
    ],
)
def test_native_float_nonzero_denominator_survives_cancellation(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
    kind: str,
    split: bool,
    coordinates: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        db.execute('ALTER TABLE "order" ADD COLUMN weight DOUBLE')
        for index, weight_value in enumerate([1e16, -1e16, 16.0]):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    str(index),
                    "ABC"[index] if split else "A",
                    "web",
                    "paid",
                    "2026-08-15",
                    1.0,
                    weight_value,
                ],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "# Named expressions",
            "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n# Named expressions",
        )
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    session = case.session
    value = catalog_value(session.catalog, "sales.order.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.order.weight", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    numerator, denominator = value.sum(time=time), weight.sum(time=time)
    metric = (
        numerator / denominator
        if kind == "ratio"
        else value.weighted_mean(weight=weight, time=time)
    )
    members = session.members(session.catalog.get("entity:sales.customer"))
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    logical = session.observe(
        {"value": metric},
        by=(customer, channel) if coordinates else members,
        where=mv.in_population(members),
    )
    # Native sums retain a nonzero denominator of 16 despite cancellation.
    expected_total = 3.0 / 16.0 if kind == "ratio" else 1.0
    fixed = logical.persist()
    rows = fixed.to_pandas()
    defined = dict(zip(rows.iloc[:, 0], rows["value"], strict=True))
    defined = {key: value for key, value in defined.items() if not pd.isna(value)}
    expected = (
        {"A": 1.0 / 1e16, "B": -1.0 / 1e16, "C": 1.0 / 16.0}
        if split and kind == "ratio"
        else dict.fromkeys("ABC", 1.0)
        if split
        else {"A": expected_total}
    )
    assert defined == expected
    assert all(isinstance(value, float) for value in defined.values())
    assert session.persisted_frame(fixed.artifact_id).to_pandas().equals(rows)
    results = [session.observe({"value": metric}, where=mv.in_population(members)).compute()]
    for result in results:
        assert result.to_pandas()["value"].tolist() == [expected_total]


@pytest.mark.parametrize(
    "parquet,kind",
    # Keep the Decimal intermediate-width boundary on both table and Parquet
    # sources; fold-kind dispatch is covered by the typed temporal matrix.
    [(False, "mean"), (True, "mean")],
)
def test_decimal_fold_retains_widened_sample_type(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, kind: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("DELETE FROM customer WHERE customer_id IN ('B','C','D')")
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(3,0)')
        for index in range(2):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", "web", "paid", "2026-08-02T00:00:00Z", 900],
            )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    session = case.session
    value = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    # The sample sum widens DECIMAL(3,0) before the ordinary mean finish.
    folded = value.sum(time=time).per(time).mean()
    logical = session.observe(
        {"value": folded},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-08-03"),
            session.members(session.catalog.get("entity:sales.customer")),
        ),
    )
    fixed = logical.persist()
    for result in (
        fixed,
        session.observe(
            {"value": folded},
            by=(session.time_scope(start="2026-08-01", end="2026-08-03"),),
            where=mv.in_population(session.members(session.catalog.get("entity:sales.customer"))),
        ).compute(),
    ):
        assert result.to_pandas()["value"].tolist() == [Decimal(1800)]
    assert session.persisted_frame(fixed.artifact_id).to_pandas().equals(fixed.to_pandas())


@pytest.mark.parametrize(
    "scale,negative,kind",
    [(0, False, "sum"), (38, True, "sum"), (6, True, "min"), (38, False, "max")],
)
def test_decimal_boundary_fixed_rollup_does_not_round_range_check(
    analysis_dsl_case_factory: DslCaseFactory,
    scale: int,
    negative: bool,
    kind: Literal["sum", "min", "max"],
) -> None:
    case = analysis_dsl_case_factory("j1")
    value = Decimal((int(negative), (9,) * 38, -scale))
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE DECIMAL(38,{scale}) USING 0')
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["one", "A", "web", "paid", "2026-08-15", str(value)],
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace("parse=ms.timestamp(", "is_default=True, parse=ms.timestamp(", 1)
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    metric = (
        amount.sum(time=time)
        if kind == "sum"
        else amount.min(time=time)
        if kind == "min"
        else amount.max(time=time)
    )
    logical = session.observe(
        {"value": metric},
        by=session.members(session.catalog.get("entity:sales.customer")),
    )
    fixed = logical.persist()
    for result in (
        session.observe(
            {"value": metric},
            where=mv.in_population(session.members(session.catalog.get("entity:sales.customer"))),
        ).compute(),
    ):
        assert result.to_pandas()["value"].tolist() == [value]
    assert session.persisted_frame(fixed.artifact_id).to_pandas().equals(fixed.to_pandas())


@pytest.mark.parametrize("reverse", [False, True])
def test_numeric_raw_order_and_exchange_batches(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    from fractions import Fraction

    case = analysis_dsl_case_factory("j1")
    facts = [("A", 1e16), ("A", 1.0), ("A", -1e16), ("B", 0.25), ("C", 3.0)]
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        for index, (member, value) in enumerate(reversed(facts) if reverse else facts):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), member, "web", "paid", "2026-08-15", value],
            )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace("parse=ms.timestamp(", "is_default=True, parse=ms.timestamp(", 1)
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    amount_field = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    logical = session.observe(
        {"value": amount_field.sum(time=time)},
        by=session.members(session.catalog.get("entity:sales.customer")),
    )
    fixed = logical.persist()
    assert session.persisted_frame(fixed.artifact_id).to_pandas().equals(fixed.to_pandas())
    exact = sum(Fraction(value) for _, value in facts)
    bound = sum(abs(value) for _, value in facts) * 1e-12 + 1e-12
    for result in (
        session.observe(
            {"value": amount_field.sum(time=time)},
            where=mv.in_population(session.members(session.catalog.get("entity:sales.customer"))),
        ).compute(),
    ):
        assert abs(result.to_pandas()["value"].iloc[0] - float(exact)) <= bound
