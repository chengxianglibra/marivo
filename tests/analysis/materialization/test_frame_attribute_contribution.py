"""Exact contribution uses the real Frame schedule and independent small oracles."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.dsl.attribute import attribute_endpoint
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


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
    return mv.session.create("contribution", timezone="UTC")


def test_additive_exact_values_and_one_read_per_endpoint(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    reads: list[object] = []
    execute_original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        reads.append(issued)
        return execute_original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    pending = session.attribute(
        attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
        attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
        by=(channel,),
        contribution=True,
    )
    pending.show(n=0)
    assert reads == []
    result = pending.compute().to_pandas()
    assert len(reads) == 2
    assert result[("value", "contribution.q")].tolist() == [60, -20]
    assert result[("value", "contribution_share.q")].tolist() == [1.5, -0.5]
    assert result[("value", "absolute.q")].tolist() == [60, -20]


@pytest.mark.parametrize("kind", ("ratio", "mean", "weighted", "linear"))
def test_rule_families(session: Session, kind: str) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    if kind == "ratio":
        metric = amount.sum().divide(weight.sum())
    elif kind == "mean":
        metric = amount.mean()
    elif kind == "weighted":
        metric = amount.weighted_mean(weight=weight)
    else:
        metric = amount.sum() - 2 * amount.sum()
    result = (
        session.attribute(
            attribute_endpoint({"q": metric}, where=phase == "current"),
            attribute_endpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "contribution.q")].tolist() == (
        [-60, 20] if kind == "linear" else [30, -10]
    )
    assert result[("value", "contribution_share.q")].tolist() == [1.5, -0.5]
    if kind != "linear":
        assert result[("value", "numerator_left.q")].tolist() == [160, 80]
        assert result[("value", "denominator_left.q")].tolist() == [1, 1]


def test_fraction_ratio_uses_parent_denominators(session: Session, tmp_path: Path) -> None:
    from fractions import Fraction

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 1 WHEN 2 THEN 9 WHEN 3 THEN 2 ELSE 8 END, weight=CASE id WHEN 1 THEN 10 WHEN 2 THEN 90 WHEN 3 THEN 10 ELSE 190 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum().divide(weight.sum())
    rows = (
        session.attribute(
            attribute_endpoint({"q": metric}, where=phase == "current"),
            attribute_endpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == pytest.approx([0, float(-Fraction(1, 20))])
    assert rows[("value", "absolute.q")].tolist() == pytest.approx(
        [0.1, float(Fraction(8, 190) - Fraction(9, 90))]
    )
    assert rows[("value", "numerator_left.q")].tolist() == [2, 8]
    assert rows[("value", "denominator_left.q")].tolist() == [10, 190]


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
        session.attribute(
            attribute_endpoint(
                {"q": amount.weighted_mean(weight=weight)}, where=phase == "current"
            ),
            attribute_endpoint({"q": amount.weighted_mean(weight=weight)}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "left.q")].iloc[0] == 10
    assert pd.isna(rows[("value", "left.q")].iloc[1])
    assert rows[("value", "contribution.q")].tolist() == [5, 0]
    assert rows[("value", "denominator_left.q")].tolist() == [1, 0]


def test_known_empty_fine_side_preserves_original_na(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id IN (1,4)")
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        session.attribute(
            attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
            attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == [160, -100]
    assert rows[("value", "absolute.q")].isna().all()
    assert rows[("presence", "left_present")].tolist() == [True, False]
    assert rows[("presence", "right_present")].tolist() == [False, True]


def test_zero_change_keeps_signed_contributions(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN phase='base' THEN 100 WHEN channel='A' THEN 200 ELSE 0 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        session.attribute(
            attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
            attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == [100, -100]
    assert rows[("value", "contribution_share.q")].isna().all()


@pytest.mark.parametrize("derive", ("direct", "top0", "select", "where"))
def test_original_formula_error_keeps_owner_context_and_atomicity(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, derive: str
) -> None:
    from marivo.analysis.core.attribute import AttributeFineFiber
    from marivo.analysis.core.data_types import ScalarDataType
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("UPDATE facts SET weight=0 WHERE id=4")
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    pending = session.attribute(
        mv.attribute_endpoint(
            {
                "safe": amount.sum(),
                "bad": amount.sum().divide(weight.sum(), zero_denominator="error"),
            },
            where=phase == "current",
        ),
        mv.attribute_endpoint(
            {
                "safe": amount.sum(),
                "bad": amount.sum().divide(weight.sum(), zero_denominator="error"),
            },
            where=phase == "base",
        ),
        by=(channel,),
        contribution=True,
    )
    if derive == "top0":
        pending = pending.top(0, by=pending["left.safe"])
    elif derive == "select":
        pending = pending.select({"only": pending["left.safe"]})
    elif derive == "where":
        pending = pending.where(pending.coord(pending.axes[0]) != "B")
        reads: list[object] = []
        execute = SourceSession.execute

        def read(self: SourceSession, issued: object) -> pd.DataFrame:
            reads.append(issued)
            return execute(self, issued)

        monkeypatch.setattr(SourceSession, "execute", read)
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
    assert error.repair is not None and "zero_denominator" in error.repair.action


@pytest.mark.parametrize("shape", ("direct", "derived", "shared", "distinct", "source"))
def test_real_producer_association_and_inert_parent_payload(
    session: Session, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.core.frame_expression import FrameExpression
    from marivo.analysis.materialization import frame_evaluation

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    left = attribute_endpoint({"q": amount.sum()}, where=phase == "current")
    right = attribute_endpoint({"q": amount.sum()}, where=phase == "base")
    first = session.attribute(left, right, by=(channel,), contribution=True)
    result = first
    expected_inputs = [first._input_id]
    if shape == "derived":
        result = first.select({"kept": first["contribution.q"]})
    elif shape in ("shared", "distinct"):
        second = (
            first
            if shape == "shared"
            else session.attribute(left, right, by=(channel,), contribution=True)
        )
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
        compiled: CompiledFramePlan,
        explicit_inputs: tuple[tuple[str, FrameExpression], ...],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(compiled, explicit_inputs, observed)
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
                origin_result_id=fixed._backing.metadata.result_id,
                output_schema=payload.output_schema,
                coordinates=payload.coordinates,
                parent_axis_count=payload.parent_axis_count,
                parents=payload.parents,
            )


def test_exact_decimal_contribution(session: Session, tmp_path: Path) -> None:
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
        session.attribute(
            attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
            attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == [Decimal("60.02"), Decimal("-20.01")]
    assert all(type(value) is Decimal for value in rows[("value", "contribution.q")])


@pytest.mark.parametrize("mean", (False, True))
def test_duration_preserves_exact_ticks_and_half_even(
    session: Session, tmp_path: Path, mean: bool
) -> None:
    import pyarrow as pa

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute(
            "ALTER TABLE facts ALTER amount TYPE INTERVAL USING amount * INTERVAL '1 microsecond'"
        )
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN INTERVAL '1 microsecond' WHEN 2 THEN INTERVAL '3 microsecond' WHEN 3 THEN INTERVAL '4 microsecond' ELSE INTERVAL '2 microsecond' END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.mean() if mean else amount.sum()
    rows = (
        session.attribute(
            attribute_endpoint({"q": metric}, where=phase == "current"),
            attribute_endpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    ticks = pa.array(rows[("value", "contribution.q")]).cast(pa.int64()).to_pylist()
    assert ticks == ([2, -1] if mean else [3, -1])
    assert rows[("value", "contribution_share.q")].tolist() == (
        [2.0, -1.0] if mean else [1.5, -0.5]
    )


def test_parent_difference_overflow_has_exact_context_even_under_top0(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id IN (2,4)")
        connection.execute(
            "UPDATE facts SET amount=CASE WHEN phase='current' THEN 9223372036854775807 ELSE -1 END"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    pending = session.attribute(
        attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
        attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
        by=(channel,),
        contribution=True,
    )
    with pytest.raises(MaterializationError) as caught:
        pending.top(0, by=pending["left.q"]).compute()
    assert caught.value.stage == "numeric_value"
    assert caught.value.attribute_context is not None
    assert caught.value.attribute_context.fiber.parent == ()
    assert {use.endpoint for use in caught.value.attribute_context.uses} == {"left", "right"}


def test_static_typed_coefficient_mismatch_is_first_path(session: Session) -> None:
    from marivo.analysis.binding.attribute_rules import AttributeRuleError

    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AttributeRuleError) as caught:
        session.attribute(
            attribute_endpoint({"q": amount.sum() - 2 * amount.sum()}),
            attribute_endpoint({"q": amount.sum() - 3 * amount.sum()}),
            by=(channel,),
            contribution=True,
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
    metric = amount.sum().divide(weight.sum())
    with pytest.raises(AttributeInputError) as caught:
        session.attribute(
            attribute_endpoint({"q": metric}, where=phase == "current"),
            attribute_endpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        ).compute()
    assert caught.value.reason == "root_denominator_zero"
    assert caught.value.attribute_context is not None
    assert caught.value.attribute_context.witness.values[0].value == 0


@pytest.mark.parametrize("selected_empty", (False, True))
def test_empty_parent_domain_and_no_fine_rows_keep_complete_payload(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected_empty: bool
) -> None:
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.core.frame_expression import FrameExpression
    from marivo.analysis.materialization import frame_evaluation

    amount = session.catalog.fields.get("sales.facts.amount")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    with duckdb.connect(str(tmp_path / "contribution.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
    parents = mv.coordinates(by=(region,), keys=()) if selected_empty else None
    endpoint = attribute_endpoint({"q": amount.sum(empty="zero")})
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def productions(
        compiled: CompiledFramePlan,
        explicit_inputs: tuple[tuple[str, FrameExpression], ...],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(compiled, explicit_inputs, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    result = session.attribute(
        endpoint, endpoint, parents=parents, by=(channel,), contribution=True
    ).compute()
    assert result.to_pandas().empty
    assert len(captured) == 1
    assert len(captured[0].payload.parents) == (0 if selected_empty else 1)
    if not selected_empty:
        assert captured[0].payload.parents[0].metrics[0].method.left.value == 0
        assert captured[0].payload.parents[0].metrics[0].share_state == "zero_total_change"


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
    rows = (
        session.attribute(
            attribute_endpoint({"q": amount.sum(empty="zero")}, where=phase == "current"),
            attribute_endpoint({"q": amount.sum(empty="zero")}, where=phase == "base"),
            parents=parents,
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "left.q")].tolist() == [9007199254740993, 0]
    assert rows[("value", "contribution.q")].tolist() == [0, 0]


def test_linear_ratio_children_and_original_scalar_order(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    ratio = amount.sum().divide(weight.sum())
    metric = ratio - 2 * ratio
    rows = (
        session.attribute(
            attribute_endpoint({"q": metric}, where=phase == "current"),
            attribute_endpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == [-30, 10]
    assert rows[("value", "contribution_share.q")].tolist() == [1.5, -0.5]


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
        session.attribute(
            attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
            attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
            by=(channel,),
            contribution=True,
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
    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    rows = (
        session.attribute(
            attribute_endpoint({"q": amount.mean()}, where=phase == "current"),
            attribute_endpoint({"q": amount.mean()}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == pytest.approx([10 / 3, 0])
    assert rows[("value", "absolute.q")].tolist() == [10, 0]
    assert rows[("value", "denominator_left.q")].tolist() == [1, 2]


@pytest.mark.parametrize("kind", ("additive", "ratio_component", "linear"))
def test_payload_satisfies_existing_fact_model(
    session: Session, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.core.frame_expression import FrameExpression
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts
    from marivo.analysis.materialization import frame_evaluation

    bare = session.catalog.fields.get("sales.facts.bare")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    ratio = bare.sum().divide(weight.sum())
    metric = bare.sum() if kind == "additive" else ratio
    if kind == "linear":
        metric = ratio - 2 * ratio
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def productions(
        compiled: CompiledFramePlan,
        explicit_inputs: tuple[tuple[str, FrameExpression], ...],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(compiled, explicit_inputs, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", productions)
    fixed = session.attribute(
        attribute_endpoint({"q": metric}, where=phase == "current"),
        attribute_endpoint({"q": metric}, where=phase == "base"),
        by=(channel,),
        contribution=True,
    ).compute()
    assert len(captured) == 1
    payload = captured[0].payload
    facts = FrameMethodFacts(
        kind="attribute",
        origin_result_id=fixed._backing.metadata.result_id,
        output_schema=payload.output_schema,
        coordinates=payload.coordinates,
        parent_axis_count=payload.parent_axis_count,
        parents=payload.parents,
    )
    assert facts.parents[0].metrics[0].method.kind == kind


@pytest.mark.parametrize("shape", ("scalar", "pending", "fixed"))
def test_new_attribute_uses_only_its_own_live_components(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.compiler.graph_plan import FrameCompute, LocalCompute
    from marivo.analysis.core.attribute import (
        AttributeContributionProgram,
        AttributeInputProgram,
        AttributeMethodPayload,
        AttributeMethodProduction,
        AttributeProjectProgram,
    )
    from marivo.analysis.core.frame_expression import FrameExpression
    from marivo.analysis.materialization import frame_evaluation, graph_local_execution
    from marivo.analysis.materialization.graph_exchange import StageOutput
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
    left = attribute_endpoint(metrics, where=phase == "current")
    right = attribute_endpoint(metrics, where=phase == "base")
    first = session.attribute(
        left,
        right,
        parents=None if shape == "scalar" else mv.coordinates(by=(region,), keys=(("E",), ("W",))),
        by=(region,) if shape == "scalar" else (channel,),
        contribution=True,
    )
    selected = first.where(first["contribution.q"] > 0)
    if shape == "fixed":
        selected = selected.compute()
    parents = session.population_from(selected, on=((selected.axes[0], region),))
    if shape != "scalar":
        left = attribute_endpoint(metrics, where=(phase == "current") & (channel == "A"))
        right = attribute_endpoint(metrics, where=(phase == "base") & (channel == "A"))
    final = session.attribute(left, right, parents=parents, by=(channel,), contribution=True)
    sources: list[SourceSession] = []
    captured: list[AttributeMethodProduction] = []
    expected_order: list[str] = []
    consumed: list[str] = []
    bridges: list[str] = []
    execute_original = SourceSession.execute
    productions_original = frame_evaluation._attribute_productions
    local_original = graph_local_execution.run_local

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        sources.append(self)
        return execute_original(self, issued)

    def productions(
        compiled: CompiledFramePlan,
        explicit_inputs: tuple[tuple[str, FrameExpression], ...],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        assert sources and all(source._closed for source in sources)
        expected_order.extend(
            input_id
            for input_id, _ in explicit_inputs
            if input_id == final._input_id or (shape != "fixed" and input_id == first._input_id)
        )
        values = productions_original(compiled, explicit_inputs, observed)
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
                assert {root.stage_id for root in output.attribute_inputs} == {
                    root.stage_id for endpoint in inputs[1:3] for root in endpoint.attribute_inputs
                }
                assert all(not source.attribute_inputs for source in (inputs[0], *inputs[3:]))
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
    assert rows[("value", "contribution.q")].tolist() == ([20, -5] if shape == "scalar" else [20])
    assert rows[("value", "contribution_share.q")].tolist() == pytest.approx(
        [20 / 15, -5 / 15] if shape == "scalar" else [1.0]
    )
    assert len(sources) == (2 if shape == "fixed" else 4)
    assert len(consumed) == (1 if shape == "fixed" else 2)
    assert bridges
    assert [production.input_id for production in captured] == expected_order
    assert set(expected_order) == (
        {final._input_id} if shape == "fixed" else {first._input_id, final._input_id}
    )
    assert [production.explicit_index for production in captured] == sorted(
        production.explicit_index for production in captured
    )
    assert all(
        production.is_final == (production.input_id == final._input_id) for production in captured
    )
    assert len({production.stage_id for production in captured}) == len(captured)
    if shape == "pending":
        assert rows[("value", "contribution.n")].tolist() == [0]
        assert rows[("value", "contribution_share.n")].isna().all()
    payload = next(p.payload for p in captured if p.input_id == final._input_id)
    assert len(payload.parents) == 1
    assert payload.parents[0].key[0].value == "E"
    method = payload.parents[0].metrics[0].method
    assert (method.left.value, method.right.value, method.change.value) == (
        (35, 20, 15) if shape == "scalar" else (30, 10, 20)
    )
    if shape != "scalar":
        assert rows[("value", "left.q")].tolist() == [30]
        assert rows[("value", "right.q")].tolist() == [10]
        assert rows[("value", "absolute.q")].tolist() == [20]


@pytest.mark.parametrize("mean", [True, False])
@pytest.mark.parametrize("lexical", ["0.5", "0.500"])
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
    expected_dtype = ScalarDataType("float64") if mean else DecimalDataType(38, 9)
    assert current._schema.values[0].dtype == base._schema.values[0].dtype == expected_dtype
    assert (
        current_parent._schema.values[0].dtype
        == base_parent._schema.values[0].dtype
        == expected_dtype
    )
    endpoints = (
        attribute_endpoint({"q": metric}, where=phase == "current"),
        attribute_endpoint({"q": metric}, where=phase == "base"),
    )
    ordinary_attribute = session.attribute(*endpoints, by=(channel,)).compute()
    pending = session.attribute(*endpoints, by=(channel,), contribution=True)
    rule = pending._backing.root.operation.rules[0][0][1]
    assert isinstance(rule.operation, RuleScale)
    captured = rule.operation.scalar
    assert type(captured.value) is Decimal
    reads = []
    execute = SourceSession.execute

    def counted(self, issued):
        reads.append(issued)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", counted)
    fixed = pending.compute()
    assert len(reads) == 2
    data = fixed.to_pandas()
    default = ordinary_attribute.to_pandas()
    left = current.to_pandas()[("value", "q")].tolist()
    right = base.to_pandas()[("value", "q")].tolist()
    assert left == ([160.0, 80.0] if mean else [Decimal("160.250000000"), Decimal("80.250000000")])
    assert right == (
        [100.0, 100.0] if mean else [Decimal("100.000000000"), Decimal("100.000000000")]
    )
    for role, values in (("left", left), ("right", right)):
        assert (
            data[("value", role + ".q")].tolist()
            == default[("value", role + ".q")].tolist()
            == values
        )
        column = next(column for column in fixed._schema.values if column.name == role + ".q")
        assert column.dtype == expected_dtype and column.unit == "USD"
        assert (
            next(
                column for column in ordinary_attribute._schema.values if column.name == role + ".q"
            ).dtype
            == expected_dtype
        )
    assert data[("presence", "left_present")].tolist() == [True, True]
    assert data[("presence", "right_present")].tolist() == [True, True]
    assert data[("value", "contribution.q")].tolist() == (
        [30.0, -10.0] if mean else [Decimal("60.250000000"), Decimal("-19.750000000")]
    )
    assert data[("value", "contribution_share.q")].tolist() == pytest.approx(
        [1.5, -0.5] if mean else [float(Fraction(241, 162)), float(Fraction(-79, 162))]
    )
    facts = fixed.method_facts
    method = facts[0].parents[0].metrics[0].method
    if mean:
        assert isinstance(method, FrameLinearMethod)
        assert method.terms[0].coefficient.as_tuple() == captured.value.as_tuple()
    else:
        from marivo.analysis.core.frame_method_facts import FrameAdditiveMethod

        assert isinstance(method, FrameAdditiveMethod)
    assert method.left.column.dtype == method.right.column.dtype == expected_dtype
    assert method.left.value == (120.0 if mean else Decimal("240.500000000"))
    assert method.right.value == (100.0 if mean else Decimal("200.000000000"))
    assert method.change.value == (20.0 if mean else Decimal("40.500000000"))
    saved = fixed.persist()
    assert saved.method_facts == facts and saved.compute().method_facts == facts
    assert len(reads) == 2
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
    assert method.left.column.dtype==DecimalDataType(38,9)
    assert method.left.value==Decimal('240.500000000')
    assert method.change.value==Decimal('40.500000000')
    assert saved.compute().method_facts==saved.method_facts
"""
        cold = subprocess.run(
            [sys.executable, "-c", code, session.id, saved.artifact.id, str(checkout)],
            cwd=checkout,
            env={**os.environ, "PYTHONPATH": str(checkout), "MARIVO_PROJECT_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
        )
        assert cold.returncode == 0, cold.stdout + cold.stderr
    assert pa.array(data[("value", "left.q")]).type == (
        pa.float64() if mean else pa.decimal128(38, 9)
    )
