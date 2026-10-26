"""Exact contribution uses the real Frame schedule and independent small oracles."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo._grouping import Grouping
from marivo.analysis.core.attribute import (
    AttributeFineFiber,
    AttributeInputError,
    AttributeNumericWitness,
    AttributeParentFiber,
)
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    MemoryFrameBacking,
    OperationFrameBinding,
)
from marivo.analysis.core.frame_expression import AttributeContributionValues
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import Population
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


@pytest.mark.parametrize("method", ["mean", "ratio", "weighted"])
def test_pending_attribute_component_units_allow_direct_composition(
    session: Session, method: str
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = (
        amount.mean()
        if method == "mean"
        else amount.sum() / amount.sum()
        if method == "ratio"
        else amount.weighted_mean(weight=session.catalog.fields.get("sales.facts.weight"))
    )
    pending = session.observe({"q": metric}).attribute(by=(channel,))
    fixed = pending.compute()
    for frame in (pending, fixed):
        numerator = frame["component.value.numerator.q"]
        denominator = frame["component.value.denominator.q"]
        difference = numerator / denominator - frame["detail.value.q"]
        rows = frame.select({"difference": difference}).compute().to_pandas()
        assert rows["difference"].tolist() == pytest.approx([0, 0])
    assert [(c.name, c.unit) for c in pending._schema.values] == [
        (c.name, c.unit) for c in fixed._schema.values
    ]
    if method == "mean":
        from marivo.analysis.errors import AnalysisError

        with pytest.raises(AnalysisError, match="matching units"):
            pending["component.value.denominator.q"] + pending["detail.value.q"]


def test_pending_weighted_components_keep_dimensional_weight_unit(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    pending = session.observe({"q": amount.weighted_mean(weight=amount)}).attribute(by=(channel,))
    for frame in (pending, pending.compute()):
        assert frame["component.value.numerator.q"]._unit == "USD.USD"
        assert frame["component.value.denominator.q"]._unit == "USD"


@pytest.mark.parametrize(
    ("rescale", "divisor"), [(False, 3), (True, 3), (False, 2.5), (True, 2.5), (False, -2)]
)
def test_attribute_constant_division_retains_original_operation(
    session: Session, rescale: bool, divisor: int | float
) -> None:
    import marivo.semantic as ms

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    phase = session.catalog.fields.get("sales.facts.phase")
    metric = (
        ms.rescale(amount.mean(), divisor=divisor, unit="GiB/s")
        if rescale
        else amount.mean() / divisor
    )
    current = session.observe({"q": metric}, where=phase == "current")
    baseline = session.observe({"q": metric}, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    report = compared.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert rows["contribution.q"].tolist() == pytest.approx([80 / divisor, 40 / divisor])
    assert rows["contribution.base.absolute.q"].tolist() == pytest.approx(
        [30 / divisor, -10 / divisor]
    )
    assert rows["contribution.base.relative.q"].tolist() == pytest.approx(
        [0.3 if divisor > 0 else -0.3, -0.1 if divisor > 0 else 0.1]
    )
    assert report["total.q"]._unit == ("GiB/s" if rescale else "USD")


@pytest.mark.parametrize("divisor", [3, 3.0, Decimal("3.00")])
def test_attribute_division_rounds_operands_before_large_integer_cancellation(
    session: Session, tmp_path: Path, divisor: int | float | Decimal
) -> None:
    import marivo.semantic as ms

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN channel='B' THEN 0 "
            "WHEN phase='current' THEN 9007199254740993 ELSE 3 END, "
            "weight=CASE WHEN channel='B' THEN 0 "
            "WHEN phase='current' THEN 3002399751580331 ELSE 1 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    channel = session.catalog.fields.get("sales.facts.channel")
    phase = session.catalog.fields.get("sales.facts.phase")
    scaled = amount.sum() / divisor
    rescaled = ms.rescale(amount.sum(), divisor=divisor, unit="GiB/s")
    metrics = {
        "scaled": scaled,
        "rescaled": rescaled,
        "residual": scaled - ms.rescale(weight.sum(), divisor=1, unit="USD"),
    }
    current = session.observe(metrics, where=phase == "current").compute()
    report = current.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    # Float64 conversion rounds 2**53 + 1 down before division, not afterwards.
    for name, expected in (
        ("scaled", 3002399751580330.5),
        ("rescaled", 3002399751580330.5),
        ("residual", -0.5),
    ):
        assert rows[f"contribution.{name}"].tolist() == [expected, 0.0]
        assert rows[f"detail.value.{name}"].tolist() == [expected, 0.0]
        for role in ("total", "observed_total"):
            assert rows[f"{role}.{name}"].tolist() == [expected, expected]
    assert report["contribution.scaled"]._unit == "USD"
    assert report["contribution.rescaled"]._unit == "GiB/s"
    baseline = session.observe(metrics, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    changes = compared.select({"q": compared["base.absolute.residual"]})
    rows = changes.attribute(by=(channel,)).compute().to_pandas()
    assert rows["contribution.q"].tolist() == [-0.5, 0.0]
    assert rows["total.q"].tolist() == [-0.5, -0.5]


@pytest.mark.parametrize("wrapper", ["multiply", "add", "subtract", "negate"])
def test_nested_additive_scaling_retains_ordered_level_and_paired_facts(
    session: Session, wrapper: str
) -> None:
    import marivo.semantic as ms

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    phase = session.catalog.fields.get("sales.facts.phase")
    scaled = ms.rescale(amount.sum() / Decimal("3.00"), divisor=2, unit="GiB/s")
    metric = (
        2 * scaled
        if wrapper == "multiply"
        else scaled + scaled
        if wrapper == "add"
        else scaled - scaled
        if wrapper == "subtract"
        else -scaled
    )
    current = session.observe({"q": metric}, where=phase == "current")
    baseline = session.observe({"q": metric}, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    report = (
        compared.select({"q": compared["q"], "change": compared["base.absolute.q"]})
        .attribute(by=(channel,))
        .compute()
    )
    methods = tuple(metric for facts in report.method_facts for metric in facts.parents[0].metrics)
    assert {metric_fact.kind for metric_fact in methods} == {"level", "absolute"}
    for metric_fact in methods:
        method = metric_fact.method
        assert method.kind in ("level_linear", "linear")
        assert [term.coefficient for term in method.terms] == (
            [2]
            if wrapper == "multiply"
            else [1, 1]
            if wrapper == "add"
            else [1, -1]
            if wrapper == "subtract"
            else [-1]
        )
        for term in method.terms:
            outer = term.method
            assert outer.kind in ("level_division", "division")
            assert outer.operation == "rescale" and type(outer.divisor) is int
            assert outer.divisor == 2
            inner = outer.method
            assert inner.kind in ("level_division", "division")
            assert inner.operation == "divide" and type(inner.divisor) is Decimal
            assert inner.divisor.as_tuple() == Decimal("3.00").as_tuple()
            if outer.kind == "level_division":
                assert outer.value.column.unit == "GiB/s"
                assert inner.kind == "level_division" and inner.value.column.unit == "USD"
            else:
                assert outer.left.column.unit == "GiB/s"
                assert inner.kind == "division" and inner.left.column.unit == "USD"
    rows = report.to_pandas()
    factor = 2 if wrapper in ("multiply", "add") else 0 if wrapper == "subtract" else -1
    assert rows["total.q"].tolist() == [40.0 * factor] * 2
    assert rows["contribution.change"].tolist() == pytest.approx([10 * factor, -10 / 3 * factor])


@pytest.mark.parametrize("nested", [False, True])
def test_decimal_attribute_division_retains_operation_and_cold_facts(
    session: Session, tmp_path: Path, nested: bool
) -> None:
    import os
    import subprocess
    import sys
    from decimal import Decimal

    import marivo
    import marivo.semantic as ms

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN amount TYPE DECIMAL(24,6)")
        connection.execute(
            "UPDATE facts SET amount=CASE channel WHEN 'A' THEN 10 ELSE 20 END WHERE phase='current'"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    phase = session.catalog.fields.get("sales.facts.phase")
    metric = ms.rescale(amount.sum(), divisor=Decimal("3"), unit="GiB/s")
    if nested:
        metric = 2 * metric
    observed = session.observe({"q": metric}, where=phase == "current").compute()
    report = observed.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert rows["contribution.q"].tolist() == (
        [6.666666666666667, 13.333333333333334]
        if nested
        else [3.3333333333333335, 6.666666666666667]
    )
    assert rows["total.q"].tolist() == [20.0 if nested else 10.0] * 2
    assert rows["observed_total.q"].tolist() == [20.0 if nested else 10.0] * 2
    fact = report.method_facts[0].parents[0].metrics[0].method
    if nested:
        assert fact.kind == "level_linear" and fact.terms[0].coefficient == 2
        fact = fact.terms[0].method
    assert fact.kind == "level_division" and fact.divisor == Decimal("3")
    assert type(fact.divisor) is Decimal and fact.operation == "rescale"
    saved = report.persist()
    checkout = Path(marivo.__file__).resolve().parents[1]
    code = """
import sys
from decimal import Decimal
from unittest.mock import patch
import marivo
import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
def forbidden(*args, **kwargs):
    raise AssertionError('cold facts attempted a source/model read')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden):
    saved = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
    fact = saved.compute().method_facts[0].parents[0].metrics[0].method
    if sys.argv[3] == 'True':
        assert fact.kind == 'level_linear' and fact.value.value == 20.0
        assert len(fact.terms) == 1 and fact.terms[0].coefficient == 2
        fact = fact.terms[0].method
    assert fact.kind == 'level_division' and type(fact.divisor) is Decimal
    assert fact.divisor == Decimal('3') and fact.operation == 'rescale'
    assert fact.value.value == 10.0
    assert fact.method.value.value == Decimal('30')
    assert fact.value.column.unit == 'GiB/s' and fact.method.value.column.unit == 'USD'
    fact.show()
    marivo.help('analysis.frame_level_division_method.divisor')
"""
    cold = subprocess.run(
        [sys.executable, "-c", code, session.id, saved.artifact.id, str(nested)],
        cwd=checkout,
        env={**os.environ, "PYTHONPATH": str(checkout), "MARIVO_PROJECT_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_rescaled_ratio_keeps_nested_linear_methods(session: Session) -> None:
    import marivo.semantic as ms

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    phase = session.catalog.fields.get("sales.facts.phase")
    metric = 2 * ms.rescale(amount.sum() / amount.sum(), divisor=3, unit="GiB/s")
    report = session.observe({"q": metric}).attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert rows["contribution.q"].tolist() == pytest.approx([13 / 33, 3 / 11])
    assert rows["total.q"].tolist() == pytest.approx([2 / 3] * 2)
    assert report["total.q"]._unit == "GiB/s"
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.kind == "level_linear"
    child = method.terms[0].method
    assert child.kind == "level_division" and child.method.kind == "level_ratio_component"
    assert child.method.value.column.unit == "1"
    current = session.observe({"q": metric}, where=phase == "current")
    baseline = session.observe({"q": metric}, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    target = compared.select({"change": compared["base.absolute.q"]})
    changes = target.attribute(by=(channel,)).compute().to_pandas()
    assert changes["contribution.change"].tolist() == pytest.approx([1 / 9, -1 / 9])


def _contribution(
    current: AnalysisFrame,
    baseline: AnalysisFrame,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
) -> AnalysisFrame:
    compared = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    selected = compared.select(
        {name: compared[f"base.absolute.{name}"] for name in current.output_names}
    )
    return selected.attribute(by=by)


def _memory(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def _schema(frame: AnalysisFrame) -> DataSchema:
    assert isinstance(frame._schema, DataSchema)
    return frame._schema


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "contribution.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, phase VARCHAR, region VARCHAR, channel VARCHAR, amount BIGINT, weight BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,'base','east','A',100,1),(2,'base','east','B',100,1),(3,'current','east','A',160,1),(4,'current','east','B',80,1)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
weight = ms.measure_column(name='weight', entity=facts, column='weight', unit='1', additivity=ms.additive_all())
bare = ms.measure_column(name='bare', entity=facts, column='amount', unit=None, additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("contribution", timezone="UTC")


def test_additive_exact_values_and_independent_role_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    reads: list[CompiledRead] = []
    execute_original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute_original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    pending = _contribution(
        session.observe({"q": amount.sum()}, where=phase == "current"),
        session.observe({"q": amount.sum()}, where=phase == "base"),
        by=(channel,),
    )
    pending.show(n=0)
    assert reads == []
    from unittest.mock import patch

    from marivo.analysis.core.attribute import AttributeContributionProgram, AttributeMethodPayload
    from marivo.analysis.materialization.graph_exchange import StageOutput
    from marivo.analysis.methods import frame_attribute_contribution

    contribute = frame_attribute_contribution.attribute_contribution_data

    def preserve_order(
        program: AttributeContributionProgram, inputs: tuple[StageOutput, ...]
    ) -> tuple[pd.DataFrame, AttributeMethodPayload]:
        with patch.object(
            pd.DataFrame, "sort_values", side_effect=AssertionError("contribution sorted")
        ):
            return contribute(program, inputs)

    monkeypatch.setattr(frame_attribute_contribution, "attribute_contribution_data", preserve_order)
    result = pending.compute().to_pandas()
    assert len(reads) == 6
    assert result["contribution.q"].tolist() == [60, -20]
    assert result["contribution_share.q"].tolist() == [1.5, -0.5]
    assert result["detail.absolute.q"].tolist() == [60, -20]


def test_native_co_valid_mask_and_local_zero_ratio(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 5 WHEN 3 THEN 10 ELSE NULL END,weight=CASE WHEN channel='A' THEN 1 ELSE 9 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        _contribution(
            session.observe({"q": amount.weighted_mean(weight=weight)}, where=phase == "current"),
            session.observe({"q": amount.weighted_mean(weight=weight)}, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["detail.current.q"].iloc[0] == 10
    assert pd.isna(rows["detail.current.q"].iloc[1])
    assert rows["contribution.q"].tolist() == [5, 0]
    assert rows["component.current.denominator.q"].tolist() == [1, 0]


def test_known_empty_fine_side_preserves_original_na(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id IN (1,4)")
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        _contribution(
            session.observe({"q": amount.sum()}, where=phase == "current"),
            session.observe({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == [160, -100]
    assert rows["detail.absolute.q"].isna().all()
    assert rows["detail.current.present.q"].tolist() == [True, False]
    assert rows["detail.baseline.present.q"].tolist() == [False, True]


def test_zero_change_keeps_signed_contributions(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN phase='base' THEN 100 WHEN channel='A' THEN 200 ELSE 0 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        _contribution(
            session.observe({"q": amount.sum()}, where=phase == "current"),
            session.observe({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == [100, -100]
    assert rows["contribution_share.q"].isna().all()


def test_original_formula_error_keeps_owner_context_and_atomicity(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.attribute import AttributeFineFiber
    from marivo.analysis.core.data_types import ScalarDataType
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DOUBLE")
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 3 THEN -5e307 WHEN 4 THEN 1e308 ELSE amount END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    pending = _contribution(
        session.observe(
            {
                "safe": amount.sum(),
                "bad": amount.sum() * 2,
            },
            where=phase == "current",
        ),
        session.observe(
            {
                "safe": amount.sum(),
                "bad": amount.sum() * 2,
            },
            where=phase == "base",
        ),
        by=(channel,),
    )
    pending = pending.where(pending.coord(pending.axes[0]) != "B")
    pending.show(n=0)
    assert reads == []
    assert terminal_runs(session) == ()
    with pytest.raises(MaterializationError) as failure:
        pending.compute()
    error = failure.value
    assert error.stage == "numeric_value"
    assert error.location == "dataset.numeric_value"
    assert error.attribute_context is not None
    assert {use.metric for use in error.attribute_context.uses} == {"bad"}
    assert {use.endpoint for use in error.attribute_context.uses} == {"left"}
    fiber = error.attribute_context.fiber
    assert isinstance(fiber, AttributeFineFiber)
    assert fiber.parent == ()
    assert fiber.fine == ((ScalarDataType("string"), "B"),)
    assert len(fiber.fine_columns) == 1
    assert error.repair is not None and "numeric" in error.repair.action


@pytest.mark.parametrize("shape", ("direct", "derived", "shared", "distinct", "source"))
def test_real_producer_association_and_inert_parent_payload(
    session: Session, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.materialization import frame_evaluation

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    left = session.observe({"q": amount.sum()}, where=phase == "current")
    right = session.observe({"q": amount.sum()}, where=phase == "base")
    first = _contribution(left, right, by=(channel,))
    result = first
    expected_inputs = [first._input_id]
    if shape == "derived":
        result = first.select({"kept": first["contribution.q"]})
    elif shape in ("shared", "distinct"):
        second = first if shape == "shared" else _contribution(left, right, by=(channel,))
        result = first.align(second, on=((first.axes[0], second.axes[0]),))
        if shape == "distinct":
            expected_inputs.append(second._input_id)
    elif shape == "source":
        selected = first.where(first["contribution.q"] > 0)
        domain = session.population_from(selected, on=((selected.axes[0], channel),))
        result = session.observe({"q": amount.sum()}, by=domain, where=channel == "A")
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def productions(
        associations: tuple[tuple[str, str, int, bool], ...],
        reachable: frozenset[str],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(associations, reachable, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    fixed = result.compute()
    assert [production.input_id for production in captured] == expected_inputs
    assert len({production.stage_id for production in captured}) == len(expected_inputs)
    assert [production.is_final for production in captured] == [shape == "direct"] * len(
        expected_inputs
    )
    for production in captured:
        payload = production.payload
        assert payload.parent_axis_count == 0
        assert payload.coordinates == ()
        assert len(payload.parents) == 1
        metric = payload.parents[0].metrics[0]
        assert metric.method.left.value == 240
        assert metric.method.right.value == 200
        assert metric.method.change.value == 40
        assert metric.share_state == "defined"
        if shape == "direct":
            from marivo.analysis.core.frame_method_facts import FrameMethodFacts

            FrameMethodFacts(
                kind="attribute",
                origin_result_id=_memory(fixed).metadata.result_id,
                output_schema=payload.output_schema,
                coordinates=payload.coordinates,
                parent_axis_count=payload.parent_axis_count,
                parents=payload.parents,
                targets=payload.targets,
                parent_schema=payload.parent_schema,
            )


def test_decimal_contribution_finishes_bound_scale(session: Session, tmp_path: Path) -> None:
    from decimal import Decimal

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(18,2)")
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 100.01 WHEN 2 THEN 100.02 WHEN 3 THEN 160.03 ELSE 80.01 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        _contribution(
            session.observe({"q": amount.sum()}, where=phase == "current"),
            session.observe({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == [Decimal("60.0"), Decimal("-20.0")]
    assert all(type(value) is Decimal for value in rows["contribution.q"])


def test_duration_preserves_exact_ticks_and_half_even(session: Session, tmp_path: Path) -> None:
    import pyarrow as pa

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "ALTER TABLE facts ALTER amount TYPE INTERVAL USING CASE id "
            "WHEN 1 THEN INTERVAL '1 microsecond' WHEN 2 THEN INTERVAL '3 microsecond' "
            "WHEN 3 THEN INTERVAL '4 microsecond' ELSE INTERVAL '2 microsecond' END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = {"sum": amount.sum(), "mean": amount.mean()}
    rows = (
        _contribution(
            session.observe(metrics, where=phase == "current"),
            session.observe(metrics, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    for name, expected_ticks, expected_shares in (
        ("sum", [3, -1], [1.5, -0.5]),
        ("mean", [2, -1], [2.0, -1.0]),
    ):
        ticks = pa.array(rows[f"contribution.{name}"]).cast(pa.int64()).to_pylist()
        assert ticks == expected_ticks
        assert rows[f"contribution_share.{name}"].tolist() == expected_shares


def test_parent_difference_overflow_has_exact_context_even_under_top0(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id IN (2,4)")
        connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(38,0)")
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN phase='current' THEN CAST(? AS DECIMAL(38,0)) ELSE -1 END",
            ["10"],
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = session.observe({"q": amount.sum()}, where=phase == "base")
    compared = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    selected = compared.select({"q": compared["base.absolute.q"]}).compute()
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CAST(? AS DECIMAL(38,0)) WHERE phase='current'", ["9" * 38]
        )
    pending = selected.attribute(by=(channel,))
    with pytest.raises(MaterializationError) as caught:
        pending.top(0, by=pending["detail.current.q"]).compute()
    assert caught.value.stage == "numeric_value"
    assert caught.value.attribute_context is not None
    assert isinstance(
        caught.value.attribute_context.fiber, (AttributeParentFiber, AttributeFineFiber)
    )
    assert caught.value.attribute_context.fiber.parent == ()
    assert {use.endpoint for use in caught.value.attribute_context.uses} == {"left", "right"}


def test_static_typed_coefficient_mismatch_is_first_path(session: Session) -> None:
    from marivo.analysis.binding.attribute_rules import AttributeRuleError

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AttributeRuleError) as caught:
        _contribution(
            session.observe({"q": amount.sum() - 2 * amount.sum()}),
            session.observe({"q": amount.sum() - 3 * amount.sum()}),
            by=(channel,),
        )
    mismatch = caught.value.mismatch
    assert mismatch is not None and mismatch.path == (1,)
    assert mismatch.left_coefficient is not None and mismatch.left_coefficient.value == 2
    assert mismatch.right_coefficient is not None and mismatch.right_coefficient.value == 3


def test_zero_root_denominator_keeps_specific_reason(session: Session, tmp_path: Path) -> None:
    from marivo.analysis.core.attribute import AttributeInputError

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("UPDATE facts SET weight=CASE WHEN channel='A' THEN 1 ELSE -1 END")
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum() / weight.sum()
    with pytest.raises(AttributeInputError) as caught:
        _contribution(
            session.observe({"q": metric}, where=phase == "current"),
            session.observe({"q": metric}, where=phase == "base"),
            by=(channel,),
        ).compute()
    assert caught.value.reason == "root_denominator_zero"
    assert caught.value.attribute_context is not None
    assert isinstance(caught.value.attribute_context.witness, AttributeNumericWitness)
    assert caught.value.attribute_context.witness.values[0].value == 0


@pytest.mark.parametrize("selected_empty", (False, True))
def test_empty_parent_domain_and_no_fine_rows_keep_complete_payload(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected_empty: bool
) -> None:
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.materialization import frame_evaluation

    amount = session.catalog.fields.get("sales.facts.amount")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
    parents = mv.coordinates(by=(region,), keys=()) if selected_empty else None
    endpoint = session.observe({"q": amount.sum()}, by=parents if parents is not None else ())
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def productions(
        associations: tuple[tuple[str, str, int, bool], ...],
        reachable: frozenset[str],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(associations, reachable, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    result = _contribution(endpoint, endpoint, by=(channel,)).compute()
    assert result.to_pandas().empty
    assert len(captured) == 1
    assert len(captured[0].payload.parents) == (0 if selected_empty else 1)
    if not selected_empty:
        assert captured[0].payload.parents[0].metrics[0].method.left.value == 0
        assert captured[0].payload.parents[0].metrics[0].share_state == "zero_total"


def test_exact_large_value_beside_known_empty_parent(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN channel='A' THEN 9007199254740993 ELSE 0 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parents = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    # Both phases have identical values; one endpoint retains the exact/empty-parent boundary.
    endpoint = session.observe({"q": amount.sum()}, by=parents, where=phase == "current")
    rows = _contribution(endpoint, endpoint, by=(channel,)).compute().to_pandas()
    assert rows["detail.current.q"].tolist() == [9007199254740993, 0]
    assert rows["contribution.q"].tolist() == [0, 0]


@pytest.mark.release
def test_linear_ratio_children_and_original_scalar_order(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    ratio = amount.sum() / weight.sum()
    metric = ratio - 2 * ratio
    rows = (
        _contribution(
            session.observe({"q": metric}, where=phase == "current"),
            session.observe({"q": metric}, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == [-30, 10]
    assert rows["contribution_share.q"].tolist() == [1.5, -0.5]


def test_null_original_parent_is_unknown_not_additive_zero(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.core.attribute import AttributeInputError

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=NULL WHERE phase='current'")
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AttributeInputError) as caught:
        _contribution(
            session.observe({"q": amount.sum()}, where=phase == "current"),
            session.observe({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
        ).compute()
    assert caught.value.reason == "undefined_input"
    assert caught.value.attribute_context is not None


def test_mean_uses_full_parent_count_in_unequal_groups(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 0 WHEN 3 THEN 10 ELSE 100 END"
        )
        connection.execute(
            "INSERT INTO facts VALUES (5,'base','east','B',100,1),(6,'current','east','B',100,1)"
        )
        connection.execute("UPDATE facts SET weight=3 WHERE id IN (5,6)")
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = {
        "mean": amount.mean(),
        "ratio": amount.sum() / weight.sum(),
        "linear": amount.sum() - 2 * amount.sum(),
    }
    rows = (
        _contribution(
            session.observe(metrics, where=phase == "current"),
            session.observe(metrics, where=phase == "base"),
            by=(channel,),
        )
        .compute()
        .to_pandas()
    )
    assert rows["contribution.mean"].tolist() == pytest.approx([10 / 3, 0])
    assert rows["detail.absolute.mean"].tolist() == [10, 0]
    assert rows["component.current.denominator.mean"].tolist() == [1, 2]
    assert rows["contribution.ratio"].tolist() == pytest.approx([2, 0])
    assert rows["detail.absolute.ratio"].tolist() == [10, 0]
    assert rows["component.current.numerator.ratio"].tolist() == [10, 200]
    assert rows["component.current.denominator.ratio"].tolist() == [1, 4]
    assert rows["contribution.linear"].tolist() == [-10, 0]
    assert rows["contribution_share.linear"].tolist() == [1, 0]


@pytest.mark.parametrize("kind", ("additive", "ratio_component", "linear"))
@pytest.mark.release
def test_payload_satisfies_existing_fact_model(
    session: Session, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts
    from marivo.analysis.materialization import frame_evaluation

    bare = session.catalog.fields.get("sales.facts.bare")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    ratio = bare.sum() / weight.sum()
    metric = bare.sum() if kind == "additive" else ratio
    if kind == "linear":
        metric = ratio - 2 * ratio
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def productions(
        associations: tuple[tuple[str, str, int, bool], ...],
        reachable: frozenset[str],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(associations, reachable, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    fixed = _contribution(
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
        by=(channel,),
    ).compute()
    assert len(captured) == 1
    payload = captured[0].payload
    facts = FrameMethodFacts(
        kind="attribute",
        origin_result_id=_memory(fixed).metadata.result_id,
        output_schema=payload.output_schema,
        coordinates=payload.coordinates,
        parent_axis_count=payload.parent_axis_count,
        parents=payload.parents,
        targets=payload.targets,
        parent_schema=payload.parent_schema,
    )
    assert facts.parents[0].metrics[0].method.kind == kind


@pytest.mark.release
@pytest.mark.parametrize("shape", ("scalar", "pending", "fixed"))
def test_new_attribute_uses_only_its_own_live_components(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    from marivo.analysis.compiler.graph_plan import FrameCompute, LocalCompute
    from marivo.analysis.core.attribute import (
        AttributeContributionProgram,
        AttributeInputProgram,
        AttributeMethodPayload,
        AttributeMethodProduction,
        AttributeProjectProgram,
    )
    from marivo.analysis.materialization import frame_evaluation, graph_local_execution
    from marivo.analysis.materialization.graph_exchange import CheckReceipt, StageOutput
    from marivo.analysis.materialization.graph_local_execution import LocalKernel
    from marivo.datasource.adapters import SourceSession

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts VALUES (1,'base','E','A',10,1),(2,'base','E','B',10,1),"
            "(3,'current','E','A',30,1),(4,'current','E','B',5,1),"
            "(5,'base','W','A',20,1),(6,'current','W','A',10,1)"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = (
        {"q": amount.sum(), "n": amount.count()} if shape == "pending" else {"q": amount.sum()}
    )
    source_by = () if shape == "scalar" else mv.coordinates(by=(region,), keys=(("E",), ("W",)))
    left = session.observe(metrics, by=source_by, where=phase == "current")
    right = session.observe(metrics, by=source_by, where=phase == "base")
    first = _contribution(
        left,
        right,
        by=(region,) if shape == "scalar" else (channel,),
    )
    selected = first.where(first["contribution.q"] > 0)
    if shape == "fixed":
        selected = selected.compute()
    parents = session.population_from(selected, on=((selected.axes[0], region),))
    left = session.observe(
        metrics,
        by=parents,
        where=(phase == "current")
        & mv.in_population(parents)
        & ((channel == "A") if shape != "scalar" else (phase == "current")),
    )
    right = session.observe(
        metrics,
        by=parents,
        where=(phase == "base")
        & mv.in_population(parents)
        & ((channel == "A") if shape != "scalar" else (phase == "base")),
    )
    final = _contribution(left, right, by=(channel,))
    from marivo.analysis.core.frame_attribute_merge import AttributeMergeValues

    def producer_ids(frame: AnalysisFrame) -> set[str]:
        backing = frame._backing
        assert isinstance(backing, ExpressionFrameBacking) and isinstance(
            backing.root, OperationFrameBinding
        )
        return (
            {child._input_id for child in backing.root.inputs}
            if isinstance(backing.root.operation, AttributeMergeValues)
            else {frame._input_id}
        )

    first_ids, final_ids = producer_ids(first), producer_ids(final)
    sources: list[SourceSession] = []
    attribute_sources: list[SourceSession] = []
    captured: list[AttributeMethodProduction] = []
    expected_order: list[str] = []
    consumed: list[str] = []
    bridges: list[str] = []
    execute_original = SourceSession.execute
    productions_original = frame_evaluation._attribute_productions
    local_original = graph_local_execution.run_local

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        sources.append(self)
        if "__attribute_kind" in issued.expression.columns:
            attribute_sources.append(self)
        return execute_original(self, issued)

    def productions(
        associations: tuple[tuple[str, str, int, bool], ...],
        reachable: frozenset[str],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        assert sources and all(source._closed for source in sources)
        expected_order.extend(
            input_id
            for _, input_id, _, _ in associations
            if input_id in final_ids or (shape != "fixed" and input_id in first_ids)
        )
        values = productions_original(associations, reachable, observed)
        captured.extend(values)
        return values

    def local(
        stage: LocalCompute | FrameCompute,
        inputs: tuple[StageOutput, ...],
        kernel: LocalKernel | None = None,
    ) -> StageOutput:
        output = local_original(stage, inputs, kernel)
        if isinstance(stage, FrameCompute):
            if isinstance(stage.program, AttributeInputProgram):
                assert output.receipts == (
                    CheckReceipt("attribute.complete_key_mapping", stage.stage_id),
                )
                assert {root.stage_id for root in output.attribute_inputs} == {
                    root.stage_id
                    for binding in stage.program.reads
                    for root in inputs[binding.input_index].attribute_inputs
                }
                assert all(not source.attribute_inputs for source in (inputs[0], *inputs[3:5]))
                assert {binding.scope for binding in stage.program.reads} == {"parent", "fine"}
            else:
                assert not output.attribute_inputs
            if isinstance(stage.program, AttributeContributionProgram):
                consumed.append(stage.stage_id)
            if isinstance(stage.program, AttributeProjectProgram):
                bridges.append(stage.stage_id)
        elif stage.program is None:
            assert not output.attribute_inputs
            bridges.append(stage.stage_id)
        return output

    monkeypatch.setattr(SourceSession, "execute", execute)
    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    monkeypatch.setattr(graph_local_execution, "run_local", local)
    rows = final.compute().to_pandas()
    keys = list(rows.iloc[:, :2].itertuples(index=False, name=None))
    assert keys == ([("E", "A"), ("E", "B")] if shape == "scalar" else [("E", "A")])
    assert rows["contribution.q"].tolist() == ([20, -5] if shape == "scalar" else [20])
    assert rows["contribution_share.q"].tolist() == pytest.approx(
        [20 / 15, -5 / 15] if shape == "scalar" else [1.0]
    )
    assert len(attribute_sources) == (4 if shape == "fixed" else 8)
    assert len(consumed) == (1 if shape == "fixed" else 4 if shape == "pending" else 2)
    assert bridges
    assert [production.input_id for production in captured] == expected_order
    assert set(expected_order) == (final_ids if shape == "fixed" else first_ids | final_ids)
    assert [production.explicit_index for production in captured] == sorted(
        production.explicit_index for production in captured
    )
    assert all(
        production.is_final == (production.input_id == final._input_id) for production in captured
    )
    assert len({production.stage_id for production in captured}) == len(captured)
    if shape == "pending":
        assert rows["contribution.n"].tolist() == [0]
        assert rows["contribution_share.n"].isna().all()
    payload = next(
        p.payload
        for p in captured
        if p.input_id in final_ids and p.payload.targets[0].target == ("q",)
    )
    assert len(payload.parents) == 1
    assert payload.parents[0].key[0].value == "E"
    method = payload.parents[0].metrics[0].method
    assert (method.left.value, method.right.value, method.change.value) == (
        (35, 20, 15) if shape == "scalar" else (30, 10, 20)
    )
    if shape != "scalar":
        assert rows["detail.current.q"].tolist() == [30]
        assert rows["detail.baseline.q"].tolist() == [10]
        assert rows["detail.absolute.q"].tolist() == [20]


@pytest.mark.parametrize("mean", [True, False])
@pytest.mark.parametrize("lexical", ["0.5", "0.500"])
@pytest.mark.release
def test_fractional_decimal_scalar_keeps_actual_native_originals_and_facts(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mean: bool, lexical: str
) -> None:
    import os
    import subprocess
    import sys
    from decimal import Decimal
    from fractions import Fraction

    import pyarrow as pa

    import marivo
    from marivo.analysis.binding.attribute_rules import RuleScale
    from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
    from marivo.analysis.core.frame_method_facts import FrameLinearMethod
    from marivo.datasource.adapters import SourceSession

    if not mean:
        with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
            connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(18,2)")
            connection.execute(
                "UPDATE facts SET amount=CASE id WHEN 3 THEN 160.25 WHEN 4 THEN 80.25 ELSE amount END"
            )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    child = amount.mean() if mean else amount.sum()
    metric = (child + child) * Decimal(lexical)
    current_parent = session.observe({"q": metric}, where=phase == "current").compute()
    base_parent = session.observe({"q": metric}, where=phase == "base").compute()
    current = session.observe({"q": metric}, where=phase == "current", by=(channel,)).compute()
    base = session.observe({"q": metric}, where=phase == "base", by=(channel,)).compute()
    expected_dtype = ScalarDataType("float64") if mean else DecimalDataType(38, 1)
    assert _schema(current).values[0].dtype == _schema(base).values[0].dtype == expected_dtype
    assert (
        _schema(current_parent).values[0].dtype
        == _schema(base_parent).values[0].dtype
        == expected_dtype
    )
    endpoints = (
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
    )
    ordinary_attribute = _contribution(*endpoints, by=(channel,)).compute()
    pending = _contribution(*endpoints, by=(channel,))
    assert isinstance(pending._backing, ExpressionFrameBacking)
    binding = pending._backing.root
    assert isinstance(binding, OperationFrameBinding)
    assert isinstance(binding.operation, AttributeContributionValues)
    rule = binding.operation.rules[0][0][1]
    assert isinstance(rule.operation, RuleScale)
    captured = rule.operation.scalar
    assert type(captured.value) is Decimal
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def counted(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", counted)
    fixed = pending.compute()
    assert len(reads) == 6
    data = fixed.to_pandas()
    default = ordinary_attribute.to_pandas()
    left = current.to_pandas()["q"].tolist()
    right = base.to_pandas()["q"].tolist()
    assert left == ([160.0, 80.0] if mean else [Decimal("160.3"), Decimal("80.3")])
    assert right == (
        [100.0, 100.0] if mean else [Decimal("100.000000000"), Decimal("100.000000000")]
    )
    for role, values in (("detail.current", left), ("detail.baseline", right)):
        assert data[role + ".q"].tolist() == default[role + ".q"].tolist() == values
        column = next(column for column in _schema(fixed).values if column.name == role + ".q")
        assert column.dtype == expected_dtype and column.unit == "USD"
        assert (
            next(
                column
                for column in _schema(ordinary_attribute).values
                if column.name == role + ".q"
            ).dtype
            == expected_dtype
        )
    assert data["detail.current.present.q"].tolist() == [True, True]
    assert data["detail.baseline.present.q"].tolist() == [True, True]
    assert data["contribution.q"].tolist() == (
        [30.0, -10.0] if mean else [Decimal("60"), Decimal("-20")]
    )
    assert data["contribution_share.q"].tolist() == pytest.approx(
        [1.5, -0.5] if mean else [float(Fraction(60, 41)), float(Fraction(-20, 41))]
    )
    facts = fixed.method_facts
    method = facts[0].parents[0].metrics[0].method
    if mean:
        assert isinstance(method, FrameLinearMethod)
        coefficient = method.terms[0].coefficient
        assert isinstance(coefficient, Decimal)
        assert coefficient.as_tuple() == captured.value.as_tuple()
    else:
        from marivo.analysis.core.frame_method_facts import FrameAdditiveMethod

        assert isinstance(method, FrameAdditiveMethod)
    assert method.left.column.dtype == method.right.column.dtype == expected_dtype
    assert method.left.value == (120.0 if mean else Decimal("240.500000000"))
    assert method.right.value == (100.0 if mean else Decimal("200.000000000"))
    assert method.change.value == (20.0 if mean else Decimal("41"))
    saved = fixed.persist()
    assert saved.method_facts == facts and saved.compute().method_facts == facts
    assert len(reads) == 6
    if not mean and lexical == "0.500":
        checkout = Path(marivo.__file__).resolve().parents[1]
        code = """
import sys
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
import marivo
import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.analysis.core.data_types import DecimalDataType
assert Path(marivo.__file__).resolve().parents[1] == Path(sys.argv[3])
def forbidden(*args, **kwargs):
    raise AssertionError('cold native facts attempted source/model rebuild')
with patch.object(Session,'catalog',property(forbidden)), patch.object(SourceSession,'__init__',forbidden):
    session=mv.session.resume(sys.argv[1])
    saved=session.persisted_frame(sys.argv[2])
    method=saved.method_facts[0].parents[0].metrics[0].method
    assert method.left.column.dtype==DecimalDataType(38,1)
    assert method.left.value==Decimal('240.500000000')
    assert method.change.value==Decimal('41')
    assert saved.compute().method_facts==saved.method_facts
    checks=[dict(item.values) for item in saved.artifact.evidence_digest().items if item.kind=='check']
    ids={item.get('check') for item in checks}
    assert {'attribute.complete_key_mapping','attribute_closure'} <= ids
    assert not {'attribute.scoped_identity_coverage','attribute.disjoint_joint_partition'} & ids
"""
        cold = subprocess.run(
            [sys.executable, "-c", code, session.id, saved.artifact.id, str(checkout)],
            cwd=checkout,
            env={**os.environ, "PYTHONPATH": str(checkout), "MARIVO_PROJECT_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
        )
        assert cold.returncode == 0, cold.stdout + cold.stderr
    assert pa.array(data["detail.current.q"]).type == (
        pa.float64() if mean else pa.decimal128(38, 1)
    )


def test_captured_frame_attribute_absolute(session: Session) -> None:
    from marivo.analysis.binding.frame_attribute import attribute

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = session.observe({"q": amount.sum()}, where=phase == "base")
    comparison = current.compare({"base": mv.baseline(baseline)})
    selected = comparison.select({"change": comparison["base.absolute.q"]}).compute()
    result = attribute(selected, by=(channel,)).compute().to_pandas()
    assert result["contribution.change"].tolist() == [60, -20]


def test_captured_frame_attribute_level(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.analysis.binding.frame_attribute import attribute
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe({"q": amount.sum()}, where=phase == "current").compute()
    reads: list[CompiledRead] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    result = attribute(selected, by=(channel,)).compute().to_pandas()
    assert len(reads) == 2
    assert result["contribution.q"].tolist() == [160, 80]
    assert result["total.q"].tolist() == [240, 240]
    assert result["observed_total.q"].tolist() == [240, 240]


def test_captured_frame_attribute_preserves_observed_total_on_new_read(session: Session) -> None:
    from marivo.analysis.binding.frame_attribute import attribute

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    database = Path.cwd() / "contribution.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE facts SET amount=140 WHERE id=3")
    current = session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = session.observe({"q": amount.sum()}, where=phase == "base")
    compared = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    selected = compared.select({"change": compared["base.absolute.q"]}).compute()
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE facts SET amount=160 WHERE id=3")
    result = attribute(selected, by=(channel,)).compute().to_pandas()
    assert result["contribution.change"].tolist() == [60, -20]
    assert result["total.change"].tolist() == [40, 40]
    assert result["observed_total.change"].tolist() == [20, 20]
    assert result["contribution_share.change"].tolist() == [1.5, -0.5]


def test_captured_rule_families(session: Session) -> None:
    from marivo.analysis.binding.frame_attribute import attribute

    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = {
        "ratio": amount.sum() / weight.sum(),
        "mean": amount.mean(),
        "weighted": amount.weighted_mean(weight=weight),
        "linear": amount.sum() - 2 * amount.sum(),
        "count": amount.count(),
    }
    current_frame = session.observe(metrics, where=phase == "current").compute()
    baseline = session.observe(metrics, where=phase == "base").compute()
    comparison = current_frame.compare(
        {
            "base": mv.baseline(
                baseline, on=tuple(zip(current_frame.axes, baseline.axes, strict=True))
            )
        }
    )
    # Captured level and comparison modes share immutable endpoints and one attribution pass.
    selected_outputs = {f"level_{name}": comparison[name] for name in metrics}
    selected_outputs.update(
        {f"absolute_{name}": comparison[f"base.absolute.{name}"] for name in metrics}
    )
    selected_outputs.update(
        {f"relative_{name}": comparison[f"base.relative.{name}"] for name in metrics}
    )
    selected = comparison.select(selected_outputs).compute()
    rows = attribute(selected, by=(channel,)).compute().to_pandas()
    for name in ("ratio", "mean", "weighted"):
        assert rows[f"contribution.level_{name}"].tolist() == [80, 40]
        assert rows[f"detail.value.level_{name}"].tolist() == [160, 80]
        assert rows[f"total.level_{name}"].tolist() == [120, 120]
    assert rows["contribution.level_linear"].tolist() == [-160, -80]
    assert rows["contribution.level_count"].tolist() == [1, 1]

    expected_absolute = {
        "ratio": [30, -10],
        "mean": [30, -10],
        "weighted": [30, -10],
        "linear": [-60, 20],
        "count": [0, 0],
    }
    relative_denominator = {
        "ratio": 100,
        "mean": 100,
        "weighted": 100,
        "linear": 200,
        "count": 2,
    }
    for name, expected in expected_absolute.items():
        assert rows[f"contribution.absolute_{name}"].tolist() == expected
        assert rows[f"contribution.relative_{name}"].tolist() == [
            value / relative_denominator[name] for value in expected
        ]


def test_captured_original_fine_formula_checks_survive_pruning(session: Session) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    with duckdb.connect(str(Path.cwd() / "contribution.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DOUBLE")
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 3 THEN -5e307 WHEN 4 THEN 1e308 ELSE amount END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    source = session.observe({"q": amount.sum() * 2}, where=phase == "current").compute()
    for prune in ("direct", "select", "where", "top0"):
        report = source.attribute(by=(channel,))
        if prune == "select":
            report = report.select({"kept": report["contribution.q"]})
        elif prune == "where":
            report = report.where(report.coord(report.axes[0]) != "B")
        elif prune == "top0":
            report = report.top(0, by=report["contribution.q"])
        with pytest.raises(MaterializationError) as caught:
            report.compute()
        assert caught.value.stage == "numeric_value"
        assert caught.value.attribute_context is not None
        assert isinstance(caught.value.attribute_context.fiber, AttributeFineFiber)
        assert caught.value.attribute_context.fiber.fine[0][1] == "B"


@pytest.mark.parametrize("kind", ("mean", "weighted", "ratio"))
def test_captured_known_empty_sample_retains_original_na(session: Session, kind: str) -> None:
    with duckdb.connect(str(Path.cwd() / "contribution.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=NULL WHERE channel='B'")
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = (
        amount.mean()
        if kind == "mean"
        else amount.weighted_mean(weight=weight)
        if kind == "weighted"
        else amount.sum() / weight.sum()
    )
    source = session.observe({"q": metric}, where=phase == "current").compute()
    if kind == "ratio":
        from marivo.analysis.core.attribute import AttributeInputError

        with pytest.raises(AttributeInputError) as caught:
            source.attribute(by=(channel,)).compute()
        assert caught.value.reason == "undefined_input"
        return
    rows = source.attribute(by=(channel,)).compute().to_pandas()
    assert rows["contribution.q"].tolist() == [160, 0]
    assert pd.isna(rows["detail.value.q"].iloc[1])
    assert rows["detail.value.present.q"].tolist() == [True, True]
    if kind == "weighted":
        assert rows["component.value.valid_count.q"].tolist() == [1, 0]


def test_unsupported_output_records_exact_node_after_compute_and_rename(session: Session) -> None:
    from marivo.analysis.errors import AnalysisError

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    source = session.observe({"q": amount.sum()})
    mixed = source.select({"good": source["q"], "bad": source["q"] + source["q"]}).compute()
    with pytest.raises(AnalysisError) as caught:
        mixed.attribute(by=(channel,))
    assert "bad" in str(caught.value.received)
    assert "ValueBinary" in str(caught.value.received)
    assert "good" not in str(caught.value.received)
    renamed = mixed.select({"renamed": mixed["bad"]}).compute()
    with pytest.raises(AnalysisError) as renamed_error:
        renamed.attribute(by=(channel,))
    assert "renamed" in str(renamed_error.value.received)
    assert "ValueBinary" in str(renamed_error.value.received)
    assert (
        mixed.select({"good": mixed["good"]})
        .compute()
        .attribute(by=(channel,))
        .compute()
        .output_names[0]
        == "contribution.good"
    )


@pytest.mark.parametrize("mode", ("absolute", "level"))
def test_independent_endpoint_drift_checks_only_requested_final_target(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from marivo.analysis.core.attribute import AttributeClosureWitness, AttributeInputError
    from marivo.datasource.adapters import SourceSession

    database = tmp_path / "contribution.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 50 WHEN 2 THEN 40 WHEN 3 THEN 60 ELSE 40 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = session.observe({"q": amount.sum()}, where=phase == "base")
    comparison = current.compare({"base": mv.baseline(baseline)})
    selected = (
        comparison.select({"q": comparison["base.absolute.q"]}) if mode == "absolute" else current
    ).compute()
    execute = SourceSession.execute
    roles: list[str] = []

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" not in rows:
            return rows
        parent = "attribute-parent:" in issued.purpose
        roles.append("parent" if parent else "fine")
        if not parent:
            endpoint = "current" if roles.count("parent") == 1 else "base"
            fine = rows["__attribute_kind"] == 0
            selected = fine & (rows["key.0"] == ("B" if endpoint == "current" else "A"))
            for name in rows:
                if name.endswith("/value") or name.endswith(".additive_value"):
                    rows.loc[selected, name] += 10
            for name in rows:
                if name.startswith("__original_id."):
                    rows[name] += 100
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    if mode == "level":
        with pytest.raises(AttributeInputError) as error:
            selected.attribute(by=(channel,)).compute()
        assert error.value.reason == "closure_mismatch"
        context = error.value.attribute_context
        assert context is not None and isinstance(context.witness, AttributeClosureWitness)
        assert context.witness.total == 100
        assert context.witness.contribution_sum == 110
        assert context.witness.policy == "exact_integer"
        assert roles == ["parent", "fine"]
    else:
        report = selected.attribute(by=(channel,)).compute()
        rows = report.to_pandas()
        assert roles == ["parent", "fine", "parent", "fine"]
        assert rows["contribution.q"].tolist() == [0, 10]
        assert rows["total.q"].tolist() == [10, 10]
        assert rows["observed_total.q"].tolist() == [10, 10]
        audit = _memory(report).metadata.audit
        read_facts = [dict(fact.values) for fact in audit.source_facts if fact.kind == "read"]
        assert {
            fact["attribute_read_role"] for fact in read_facts if "attribute_read_role" in fact
        } == {"parent", "fine"}
        closures = [
            dict(check.values)
            for check in audit.evaluation.checks
            if dict(check.values).get("check") == "attribute_closure"
        ]
        assert len(closures) == 1
        assert {
            key: closures[0][key] for key in ("policy", "target", "mode", "parent_count", "result")
        } == {
            "policy": "attribute_closure/v2",
            "target": "q",
            "mode": "absolute",
            "parent_count": 1,
            "result": "passed",
        }
        semantic = [
            dict(fact.values)
            for fact in audit.source_facts
            if fact.kind == "check"
            and str(dict(fact.values).get("check", "")).startswith("attribute.")
        ]
        assert semantic == []
        mappings = [
            dict(check.values)
            for check in audit.evaluation.checks
            if dict(check.values).get("check") == "attribute.complete_key_mapping"
        ]
        assert len(mappings) == 1 and mappings[0]["result"] == "passed"
        saved = report.persist()
        reopened = session.persisted_frame(saved.artifact.id)
        assert reopened._backing.metadata.audit == audit
        before = roles.copy()
        assert reopened.compute().to_pandas().equals(rows)
        assert roles == before


def test_integer_sum_decimal_carrier_rejects_one_unit_independent_drift(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.attribute import AttributeClosureWitness, AttributeInputError
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE channel WHEN 'A' THEN 60 ELSE 40 END WHERE phase='current'"
        )
    selected = session.observe({"q": amount.sum()}, where=phase == "current").compute()
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" in rows and "attribute-parent:" not in issued.purpose:
            selected = (rows["__attribute_kind"] == 0) & (rows["key.0"] == "B")
            for name in rows:
                if name.endswith("/value") or name.endswith(".additive_value"):
                    rows.loc[selected, name] += 1
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    with pytest.raises(AttributeInputError) as error:
        selected.attribute(by=(channel,)).compute()
    context = error.value.attribute_context
    assert context is not None and isinstance(context.witness, AttributeClosureWitness)
    assert (context.witness.total, context.witness.contribution_sum) == (100, 101)
    assert context.witness.tolerance == 0
    assert context.witness.policy == "exact_integer"


def test_incompatible_same_root_scopes_are_rejected_before_attribute_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.attribute import AttributeInputError
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe(
        {"q": amount.sum(where=channel == "A") + amount.sum(where=channel == "B")}
    ).compute()
    execute = SourceSession.execute
    attribute_reads: list[str] = []

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        if "__attribute_kind" in issued.schema.names:
            attribute_reads.append(issued.purpose)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    with pytest.raises(AttributeInputError, match="incompatible non-temporal source scopes"):
        selected.attribute(by=(channel,)).compute()
    assert attribute_reads == []


@pytest.mark.release
def test_shared_frozen_key_dependency_can_be_reread_for_both_endpoints(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parents = session.observe({"q": amount.sum()}, by=(region,)).compute()
    keys = session.population_from(parents, on=((parents.axes[0], region),))
    current = session.observe(
        {"q": amount.sum()}, by=(region,), where=mv.in_population(keys) & (phase == "current")
    )
    baseline = session.observe(
        {"q": amount.sum()}, by=(region,), where=mv.in_population(keys) & (phase == "base")
    )
    compared = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    target = compared.select({"q": compared["base.absolute.q"]}).compute()
    rows = target.attribute(by=(channel,)).compute().to_pandas()
    assert rows["contribution.q"].tolist() == [60, -20]
    assert rows["total.q"].tolist() == [40, 40]


def test_fixed_top_parent_and_observed_value_survive_new_source_ranking(
    session: Session, tmp_path: Path
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    database = tmp_path / "contribution.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("INSERT INTO facts VALUES (5,'current','west','A',10,1)")
    observed = session.observe({"q": amount.sum()}, by=(region,), where=phase == "current")
    filtered = observed.where(observed["q"] > 100)
    selected = filtered.top(1, by=filtered["q"]).compute()
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE channel WHEN 'A' THEN 60 ELSE 40 END WHERE phase='current' AND region='east'"
        )
        connection.execute("UPDATE facts SET amount=10000 WHERE region='west'")
        connection.execute("INSERT INTO facts VALUES (6,'current','north','A',20000,1)")
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert list(rows.iloc[:, :2].itertuples(index=False, name=None)) == [
        ("east", "A"),
        ("east", "B"),
    ]
    assert rows["contribution.q"].tolist() == [60, 40]
    assert rows["total.q"].tolist() == [100, 100]
    assert rows["observed_total.q"].tolist() == [240, 240]


def test_float_method_facts_capture_native_ratio_and_survive_cold_recovery(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    import sys
    from dataclasses import replace

    import marivo
    from marivo.analysis.core.frame_method_facts import FrameRatioMethod
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    pending = _contribution(
        session.observe({"q": amount.sum() / weight.sum()}, where=phase == "current"),
        session.observe({"q": amount.sum() / weight.sum()}, where=phase == "base"),
        by=(channel,),
    )
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "attribute-parent:" in issued.purpose:
            rows["attribute.native.0"] += 1e-12
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    result = pending.compute()
    facts = result.method_facts[0]
    parent = facts.parents[0]
    metric = parent.metrics[0]
    method = metric.method
    assert isinstance(method, FrameRatioMethod)
    assert method.left.value == 120.0 + 1e-12
    assert method.numerator_left.value == 240 and method.denominator_left.value == 2
    saved_native = result.persist()
    saved_native.show()
    saved_native.method_facts[0].show()
    # A finite discrepancy well above 1% is still a captured fact, not a new
    # closure calculation. The actual report was checked by its producer above.
    captured = replace(method, left=replace(method.left, value=999.0))
    changed = replace(
        facts, parents=(replace(parent, metrics=(replace(metric, method=captured),)),)
    )
    memory = _memory(result)
    frame = AnalysisFrame._from_memory(
        session._runtime.local, replace(memory.result, method_facts=(changed,))
    )
    saved = frame.compute().persist()
    saved.method_facts[0].show()
    checkout = Path(marivo.__file__).resolve().parents[1]
    code = """
import sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
def forbidden(*args, **kwargs):
    raise AssertionError('cold facts attempted a source/model read')
with patch.object(Session,'catalog',property(forbidden)), patch.object(SourceSession,'__init__',forbidden):
    session=mv.session.resume(sys.argv[1])
    for artifact, expected in ((sys.argv[2], 120.0+1e-12), (sys.argv[3], 999.0)):
        saved=session.persisted_frame(artifact)
        assert saved.compute().method_facts[0].parents[0].metrics[0].method.left.value==expected
        saved.show()
        saved.method_facts[0].show()
"""
    cold = subprocess.run(
        [sys.executable, "-c", code, session.id, saved_native.artifact.id, saved.artifact.id],
        cwd=checkout,
        env={**os.environ, "PYTHONPATH": str(checkout), "MARIVO_PROJECT_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


@pytest.mark.parametrize("complete", [False, True])
def test_fix_keeps_distinct_source_endpoints_for_attribute(
    session: Session, complete: bool
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    frame = session.observe({"q": amount.mean()}, by=(phase,))
    if complete:
        frame = frame.compute()
    current = frame.fix(frame.axes[0], "current")
    baseline = frame.fix(frame.axes[0], "base")
    result = current.compare({"base": mv.baseline(baseline)}).attribute(by=(channel,)).compute()
    rows = result.to_pandas()
    assert rows["contribution.q"].tolist() == pytest.approx([80, 40])
    assert rows["contribution.base.absolute.q"].tolist() == pytest.approx([30, -10])
    assert rows["contribution.base.relative.q"].tolist() == pytest.approx([0.3, -0.1])


@pytest.mark.parametrize("stage", ["before", "after", "persist"])
def test_fix_attribute_composition_and_absent_members(session: Session, stage: str) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    frame = session.observe({"q": amount.sum()}, by=(phase, region))
    a = frame.fix(frame.axes[0], "current")
    b = frame.fix(frame.axes[0], "base")
    if stage == "before":
        a = a.fix(a.axes[0], "east")
        b = b.fix(b.axes[0], "east")
        compared = a.compare({"base": mv.baseline(b)})
    else:
        compared = a.compare({"base": mv.baseline(b, on=((a.axes[0], b.axes[0]),))})
        compared = compared.fix(compared.axes[0], "east")
    if stage == "persist":
        compared = compared.persist()
    rows = compared.attribute(by=(channel,)).compute().to_pandas()
    assert rows["contribution.base.absolute.q"].tolist() == [60, -20]
    missing = frame.fix(frame.axes[0], "absent")
    assert len(missing.attribute(by=(channel,)).compute().to_pandas()) == 0


def test_fix_missing_comparison_endpoint_is_not_recreated_by_attribute(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    frame = session.observe({"q": amount.sum()}, by=(phase,))
    current = frame.fix(frame.axes[0], "current")
    missing = frame.fix(frame.axes[0], "absent")
    compared = current.compare({"base": mv.baseline(missing)})
    selected = compared.select({"delta": compared["base.absolute.q"]})
    from marivo.analysis.core.attribute import AttributeInputError

    with pytest.raises(AttributeInputError) as caught:
        selected.attribute(by=(channel,)).compute()
    assert caught.value.reason == "undefined_input"
    assert "no structural endpoint scope" in caught.value.received


@pytest.mark.parametrize(
    ("mode", "complete"),
    [("levels", False), ("difference", True), ("missing_left", False), ("missing_right", True)],
)
def test_fix_after_align_preserves_attribute_endpoint_presence(
    session: Session, mode: str, complete: bool
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    left = session.observe(
        {"q": amount.sum()},
        by=(region,),
        where=phase == ("absent" if mode == "missing_left" else "current"),
    )
    right = session.observe(
        {"q": amount.sum()},
        by=(region,),
        where=phase == ("absent" if mode == "missing_right" else "base"),
    )
    aligned = left.align(right, on=((left.axes[0], right.axes[0]),))
    if complete:
        aligned = aligned.compute()
    fixed = aligned.fix(aligned.axes[0], "east")
    if mode == "levels":
        selected = fixed.select({"current": fixed["left.q"], "baseline": fixed["right.q"]})
        rows = selected.attribute(by=(channel,)).compute().to_pandas()
        assert rows["contribution.current"].tolist() == [160, 80]
        assert rows["contribution.baseline"].tolist() == [100, 100]
    else:
        selected = fixed.select({"delta": fixed["left.q"] - fixed["right.q"]})
        if mode == "difference":
            rows = selected.attribute(by=(channel,)).compute().to_pandas()
            assert rows["contribution.delta"].tolist() == [60, -20]
        else:
            with pytest.raises(AttributeInputError) as caught:
                selected.attribute(by=(channel,)).compute()
            assert caught.value.reason == "undefined_input"
            assert "no structural endpoint scope" in caught.value.received
