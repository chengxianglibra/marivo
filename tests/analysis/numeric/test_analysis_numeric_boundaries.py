"""Independent regressions for numerical boundaries."""

from decimal import Decimal
from typing import Literal

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.state_validation import state_matches
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("kind", ["ratio", "weighted"])
@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("coordinates", [False, True])
def test_float_denominator_interval_rejects_before_publication(
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
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    value, weight = ms.ref.measure("sales.order.amount"), ms.ref.measure("sales.order.weight")
    numerator = mv.runtime_metric.aggregate(value, agg="sum", label="numerator")
    denominator = mv.runtime_metric.aggregate(weight, agg="sum", label="denominator")
    metric = (
        mv.runtime_metric.ratio(numerator, denominator, label="ratio")
        if kind == "ratio"
        else mv.runtime_metric.weighted_mean(value, weight, label="weighted")
    )
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        metric,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            ms.ref.entity("sales.customer"),
            *((ms.ref.dimension("sales.order.channel"),) if coordinates else ()),
        ),
    )
    # Exact denominator is 16, but its required error budget exceeds 20000.
    if not split:
        with pytest.raises(AnalysisError, match="denominator error interval spans zero"):
            logical.execute()
    else:
        fixed = logical.execute()
        with pytest.raises(AnalysisError, match="denominator error interval spans zero"):
            logical.rollup().execute()
        with pytest.raises(AnalysisError, match="denominator error interval spans zero"):
            fixed.rollup().execute()
        if coordinates:
            with pytest.raises(AnalysisError, match="denominator error interval spans zero"):
                fixed.group_by(ms.ref.dimension("sales.order.channel")).rollup().execute()


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("kind", ["first", "last", "mean", "min", "max"])
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
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + f"""
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold={kind!r})
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
"""
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.folded"),
        during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = logical.execute()
    for result in (fixed, logical.rollup().execute(), fixed.rollup().execute()):
        assert result.to_pandas().value.tolist() == [Decimal(1800)]


@pytest.mark.runtime
@pytest.mark.parametrize("scale", [0, 6, 38])
@pytest.mark.parametrize("negative", [False, True])
@pytest.mark.parametrize("kind", ["sum", "min", "max"])
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
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        mv.runtime_metric.aggregate(ms.ref.measure("sales.order.amount"), agg=kind, label=kind),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = logical.execute()
    for result in (logical.rollup().execute(), fixed.rollup().execute()):
        assert result.to_pandas().value.tolist() == [value]


def test_float_sum_requires_finite_complete_absolute_state() -> None:
    primary = {"value": 1.0, "cell_tag": "defined", "cell_reason": None}
    state = {"original_state__sum": 1.0, "original_state__non_null_count": 3}
    assert not state_matches("original_sum", primary, state)
    for invalid in (-1.0, float("nan"), float("inf")):
        assert not state_matches(
            "original_sum", primary, {**state, "original_state__absolute_sum": invalid}
        )
    assert state_matches("original_sum", primary, {**state, "original_state__absolute_sum": 2e16})


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("batch_size", [1, 2, 1024])
@pytest.mark.runtime
def test_numeric_raw_order_and_exchange_batches(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
    batch_size: int,
) -> None:
    from fractions import Fraction

    from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession

    original_batches = SourceSession.batches
    submitted: list[int] = []

    def batches(self: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        submitted.append(batch_size)
        return original_batches(self, read, chunk_size=batch_size)

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
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
    )
    ms.load(workspace_dir=case.root)
    monkeypatch.setattr(SourceSession, "batches", batches)
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        mv.runtime_metric.aggregate(ms.ref.measure("sales.order.amount"), agg="sum", label="sum"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = logical.execute()
    exact = sum(Fraction(value) for _, value in facts)
    bound = sum(abs(value) for _, value in facts) * 1e-12 + 1e-12
    for result in (logical.rollup().execute(), fixed.rollup().execute()):
        assert abs(result.to_pandas().value.iloc[0] - float(exact)) <= bound
    assert submitted and set(submitted) == {batch_size}
