"""Original Metric consumers preserve the actual inputs and semantic boundaries."""

from __future__ import annotations

import operator
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, datetime, timezone
from itertools import pairwise

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.common.deferred import BinaryOperator, Just, Variable

from marivo._definition.expressions import (
    EntityCount,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.relations import LiteralPopulation
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    SumAll,
    SumAxes,
)
from marivo._definition.windows import AllHistory
from marivo.analysis.compiler.graph_plan import SourceRead
from marivo.analysis.compiler.observation import (
    FormulaInput,
    FormulaProgram,
    MemberInput,
    ReductionInput,
    data_type,
    formula_columns,
    lower_observation,
)
from marivo.analysis.compiler.original import (
    component_layout,
    lower_components,
    require_rollup,
    share_additive,
)
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.query_components import (
    ComponentFrame,
    complete_components,
)
from marivo.analysis.materialization.query_drill import drill_entities, prepare_drill
from marivo.analysis.materialization.query_parameters import instantiate
from marivo.analysis.methods.local import _combine_program
from marivo.analysis.methods.query_original import drill_components, rollup_components, share_frame
from marivo.datasource.adapters import _execute_duckdb
from marivo.refs import ref
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject

ROOT = ExactRef(ref.entity("sales.orders"), "original-test")
AMOUNT = RowField(
    ExactRef(ref.measure("sales.orders.amount"), "original-test"), ROOT, "CNY", SumAll()
)
WEIGHT = RowField(
    ExactRef(ref.measure("sales.orders.weight"), "original-test"), ROOT, None, SumAll()
)
CUSTOMER = RowField(
    ExactRef(ref.dimension("sales.orders.customer"), "original-test"), ROOT, None, SumAll()
)
CUSTOMER_AXIS = AxisBinding(ROOT, CUSTOMER.ref, (), "field")


@pytest.fixture
def original_table() -> Iterator[ir.Table]:
    connection = ibis.duckdb.connect(":memory:")
    table = connection.create_table(
        "original_inputs",
        pd.DataFrame(
            {
                "id": [1, 2, 3],
                "region": ["East", "East", "West"],
                "customer": ["A", "A", "B"],
                "amount": [10, 30, 300],
                "weight": [1, 3, 6],
            }
        ),
    )
    try:
        yield table
    finally:
        connection.disconnect()


def _program(
    table: ir.Table,
    node: MetricNode,
    leaves: tuple[tuple[tuple[int, ...], SourceAggregate | SourceWeightedMean | EntityCount], ...],
    *,
    samples: bool = False,
    identity_columns: tuple[str, ...] = ("id",),
) -> tuple[FormulaProgram, tuple[ComponentFrame, ...], pd.DataFrame]:
    bindings: list[FormulaInput] = []
    components: list[ComponentFrame] = []
    frames: list[pd.DataFrame] = []
    axes = (("key.0", "region"), ("key.1", "customer")) if samples else (("key.0", "region"),)
    fields = {
        "sales.orders.amount": "amount",
        "sales.orders.weight": "weight",
        "sales.orders.customer": "customer",
        "region": "region",
        "customer": "customer",
    }
    for index, (occurrence, leaf) in enumerate(leaves):
        reduction = ReductionInput(leaf, axes, f"source.{index}")
        summary, checks = lower_observation(reduction, table, fields, ())
        columns = tuple(
            DataColumn(name, name, data_type(summary[name].type()), True)
            for name in summary.columns
        )
        layout = component_layout(
            reduction,
            columns,
            tuple(
                (
                    DataColumn(
                        f"__original_id.{index}", name, data_type(table[name].type()), False
                    ),
                    name,
                )
                for index, name in enumerate(identity_columns)
            ),
            table,
            fields,
        )
        expression, lowered_checks = lower_components(layout, table, fields, ())
        assert tuple(name for name, _ in lowered_checks) == tuple(name for name, _ in checks)
        backend = table._find_backend()
        batch = _execute_duckdb(backend, expression, backend.compile(expression))
        assert isinstance(batch, pd.DataFrame)
        fine, component = complete_components(layout, batch)
        for name, _ in checks:
            assert not fine[name].fillna(False).any()
        value = next(column for column in columns if column.slot_id == reduction.value_slot)
        bindings.append(
            FormulaInput(
                occurrence,
                f"source:{index}",
                value,
                isinstance(leaf, EntityCount)
                or (
                    isinstance(leaf, SourceAggregate)
                    and leaf.reducer in ("sum", "count", "count_distinct")
                ),
                tuple((name, check.expected, check.received) for name, check in checks),
                keys=layout.keys,
                key_groups=tuple((key,) for key in layout.keys),
            )
        )
        frames.append(fine)
        components.append(component)
    program = FormulaProgram((("result", node),), (), tuple(bindings))
    completed, checks = _combine_program(
        program, tuple(frames), ("key.0",), formula_columns(program)
    )
    for name, _, _ in checks:
        assert not completed[name].fillna(False).any()
    return program, tuple(components), completed.loc[:, ["key.0", "value.0"]]


def _total(
    program: FormulaProgram, components: tuple[ComponentFrame, ...], frame: pd.DataFrame
) -> pd.DataFrame:
    result, _ = rollup_components(
        program, components, ("key.0",), (), frame.loc[:, ["key.0"]], formula_columns(program)
    )
    return result


def test_raw_mean_and_weighted_pairs_finish_after_regrouping(original_table: ir.Table) -> None:
    mean = SourceAggregate(AMOUNT, "mean", None)
    program, components, fine = _program(original_table, mean, (((0,), mean),))
    assert fine["value.0"].tolist() == [20.0, 300.0]
    assert _total(program, components, fine)["value.0"].tolist() == pytest.approx([340 / 3])
    weighted = SourceWeightedMean(AMOUNT, WEIGHT, None)
    program, components, fine = _program(original_table, weighted, (((0,), weighted),))
    assert _total(program, components, fine)["value.0"].tolist() == [190.0]
    paired = original_table.filter(original_table.id != 3).mutate(
        amount=ibis.ifelse(original_table.id == 1, 10, ibis.null().cast("int64")),
        weight=ibis.ifelse(original_table.id == 1, 1, 9),
    )
    program, components, fine = _program(paired, weighted, (((0,), weighted),))
    assert _total(program, components, fine)["value.0"].tolist() == [10.0]


def test_original_constant_and_ratio_recompute_the_formula(original_table: ir.Table) -> None:
    total = SourceAggregate(AMOUNT, "sum", None)
    shifted = MetricExpression(BinaryOperator(operator.add, Variable("slot_0"), Just(10)), (total,))
    table = original_table.filter(original_table.id != 2).mutate(
        amount=ibis.ifelse(original_table.id == 1, 100, 300)
    )
    program, components, fine = _program(table, shifted, (((0, 0), total),))
    assert fine["value.0"].tolist() == [110, 310]
    assert fine["value.0"].sum() == 420
    assert _total(program, components, fine)["value.0"].tolist() == [410]
    with pytest.raises(SemanticExpressionError, match="contribution-additive"):
        share_frame(program, components, fine, ("key.0",), (), 0, "share")
    count = EntityCount(ROOT, None)
    ratio = MetricExpression(
        BinaryOperator(operator.truediv, Variable("slot_0"), Variable("slot_1")), (total, count)
    )
    program, components, fine = _program(original_table, ratio, (((0, 0), total), ((0, 1), count)))
    assert _total(program, components, fine)["value.0"].tolist() == pytest.approx([340 / 3])


@pytest.mark.parametrize(("customer", "expected"), [("A", 400), ("B", 200)])
def test_per_keeps_full_internal_identity(
    original_table: ir.Table, customer: str, expected: int
) -> None:
    table = original_table.filter(original_table.id != 2).mutate(
        amount=ibis.ifelse(original_table.id == 1, 100, 300),
        customer=ibis.ifelse(original_table.id == 1, "A", customer),
    )
    total = SourceAggregate(AMOUNT, "sum", None)
    per = PerReduce(total, (CUSTOMER_AXIS,), "mean")
    program, components, fine = _program(table, per, (((0, 0), total),), samples=True)
    assert fine["value.0"].tolist() == [100.0, 300.0]
    assert _total(program, components, fine)["value.0"].tolist() == [expected]


def test_exact_quantile_and_distinct_use_complete_values(original_table: ir.Table) -> None:
    quantile = SourceAggregate(AMOUNT, "quantile", None, q=0.95)
    program, components, fine = _program(original_table, quantile, (((0,), quantile),))
    assert _total(program, components, fine)["value.0"].tolist() == pytest.approx([273.0])
    distinct = SourceAggregate(CUSTOMER, "count_distinct", None)
    program, components, fine = _program(original_table, distinct, (((0,), distinct),))
    assert _total(program, components, fine)["value.0"].tolist() == [2]


def test_share_uses_selected_actual_inputs_and_preserves_originals(
    original_table: ir.Table,
) -> None:
    total = SourceAggregate(AMOUNT, "sum", None)
    program, components, fine = _program(original_table, total, (((0,), total),))
    share = share_frame(program, components, fine, ("key.0",), (), 0, "share")
    pd.testing.assert_frame_equal(share.loc[:, fine.columns], fine)
    assert share["share"].tolist() == pytest.approx([40 / 340, 300 / 340])
    selected = fine.loc[fine["key.0"] == "East"]
    assert share.loc[share["key.0"] == "East", "share"].tolist() == pytest.approx([40 / 340])
    assert share_frame(program, components, selected, ("key.0",), (), 0, "share")[
        "share"
    ].tolist() == [1]


def test_share_zero_policy_only_applies_to_added_column(original_table: ir.Table) -> None:
    table = original_table.filter(original_table.id != 2).mutate(
        amount=ibis.ifelse(original_table.id == 1, 100, -100)
    )
    total = SourceAggregate(AMOUNT, "sum", None)
    program, components, fine = _program(table, total, (((0,), total),))
    shared = share_frame(program, components, fine, ("key.0",), (), 0, "share")
    assert shared["share"].isna().all()
    assert shared["value.0"].tolist() == [100, -100]
    with pytest.raises(SemanticExpressionError, match="nonzero denominator"):
        share_frame(program, components, fine, ("key.0",), (), 0, "share", "error")


def test_approximation_requires_its_own_qualification(original_table: ir.Table) -> None:
    approximate = SourceAggregate(AMOUNT, "approx_median", None)
    reduction = ReductionInput(approximate, (("key.0", "region"),), "value")
    with pytest.raises(SemanticExpressionError, match="qualified"):
        component_layout(
            reduction,
            (),
            ((DataColumn("__original_id.0", "id", ScalarDataType("int64"), False), "id"),),
            original_table,
            {"sales.orders.amount": "amount", "region": "region"},
        )


def test_second_rollup_retains_original_input(original_table: ir.Table) -> None:
    mean = SourceAggregate(AMOUNT, "mean", None)
    program, components, fine = _program(original_table, mean, (((0,), mean),))
    mapping = fine.loc[:, ["key.0"]].assign(**{"__target.0": "All"})
    first, retained = rollup_components(
        program, components, ("key.0",), ("key.0",), mapping, formula_columns(program)
    )
    second = _total(program, retained, first)
    assert second["value.0"].tolist() == pytest.approx([340 / 3])


def test_member_count_uses_complete_identities_and_selected_fine_keys(
    original_table: ir.Table,
) -> None:
    region = AxisBinding(
        ROOT, ExactRef(ref.dimension("sales.orders.region"), "original-test"), (), "field"
    )
    members = LiteralPopulation(
        (AxisBinding(ROOT, None, (), "identity"), region),
        (((1,), "East"), ((2,), "East"), ((3,), "West")),
        "member-test",
    )
    member_input = MemberInput(members, ("member.0", "member.1"), (("key.0", "member.1"),))
    count = MemberCount()
    program = FormulaProgram((("members", count),), (), (), members=member_input)
    fine = pd.DataFrame({"key.0": ["East", "West"], "value.0": [2, 1]})
    assert _total(program, (), fine)["value.0"].tolist() == [3]
    assert _total(program, (), fine.loc[fine["key.0"] == "East"])["value.0"].tolist() == [2]
    assert share_frame(program, (), fine, ("key.0",), (), 0, "share")[
        "share"
    ].tolist() == pytest.approx([2 / 3, 1 / 3])
    mapping = fine.loc[:, ["key.0"]].assign(**{"__target.0": "All"})
    grouped, retained = rollup_components(
        program, (), ("key.0",), ("key.0",), mapping, formula_columns(program)
    )
    assert _total(program, retained, grouped)["value.0"].tolist() == [3]
    total = SourceAggregate(AMOUNT, "sum", None)
    ratio = MetricExpression(
        BinaryOperator(operator.truediv, Variable("slot_0"), Variable("slot_1")), (total, count)
    )
    program, components, _ = _program(original_table, total, (((0,), total),))
    program = replace(
        program,
        outputs=(("per_member", ratio),),
        values=tuple(replace(binding, occurrence=(0, 0)) for binding in program.values),
        members=member_input,
    )
    assert _total(program, components, fine)["value.0"].tolist() == pytest.approx([340 / 3])


def test_member_count_drill_reuses_selected_complete_member_identities() -> None:
    region = AxisBinding(
        ROOT, ExactRef(ref.dimension("sales.orders.region"), "original-test"), (), "field"
    )
    members = LiteralPopulation(
        (AxisBinding(ROOT, None, (), "identity"), region),
        (((1,), "East"), ((2,), "East"), ((3,), "West")),
        "member-drill",
    )
    program = FormulaProgram(
        (("members", MemberCount()),),
        (),
        (),
        members=MemberInput(members, ("member.0", "member.1"), (("key.0", "member.1"),)),
    )
    selected = pd.DataFrame({"key.0": ["East"], "value.0": [2]})
    mapping = pd.DataFrame(
        {
            "key.0": ["East", "East"],
            "__original_member.0": [1, 2],
            "__target.0": ["East", "East"],
            "__target.1": ["Web", "Store"],
        }
    )
    columns = (
        DataColumn("key.0", "region", ScalarDataType("string"), True),
        DataColumn("key.1", "channel", ScalarDataType("string"), True),
    )
    result, _ = drill_components(
        program,
        (),
        selected,
        ("key.0",),
        ("key.0", "key.1"),
        (mapping,),
        formula_columns(program),
        source_columns=columns[:1],
        target_columns=columns,
    )
    assert result["value.0"].tolist() == [1, 1]
    overlapping = LiteralPopulation(
        (AxisBinding(ROOT, None, (), "identity"), region),
        (((1,), "East"), ((1,), "West")),
        "overlapping-members",
    )
    program = replace(program, members=replace(program.members, population=overlapping))
    with pytest.raises(SemanticExpressionError, match="disjoint fine contribution"):
        share_frame(
            program,
            (),
            pd.DataFrame({"key.0": ["East", "West"], "value.0": [1, 1]}),
            ("key.0",),
            (),
            0,
            "share",
        )


def test_version_coordinate_is_part_of_the_complete_contribution_identity(
    original_table: ir.Table,
) -> None:
    table = original_table.mutate(version=original_table.id, id=ibis.literal(1))
    total = SourceAggregate(AMOUNT, "sum", None)
    program, components, fine = _program(
        table, total, (((0,), total),), identity_columns=("id", "version")
    )
    assert _total(program, components, fine)["value.0"].tolist() == [340]


def test_sum_permissions_follow_the_axes_actually_merged(original_table: ir.Table) -> None:
    region = AxisBinding(
        ROOT, ExactRef(ref.dimension("sales.orders.region"), "original-test"), (), "field"
    )
    scoped = replace(AMOUNT, permission=SumAxes((region.field,)))
    total = SourceAggregate(scoped, "sum", None)
    program, _, _ = _program(original_table, total, (((0,), total),))
    require_rollup(program, (region,), ())
    assert share_additive(total, {}, (region,))
    with pytest.raises(SemanticExpressionError, match="sum authorization"):
        require_rollup(program, (CUSTOMER_AXIS,), ())
    assert not share_additive(total, {}, (CUSTOMER_AXIS,))


def test_window_space_rollup_preserves_both_original_endpoints(original_table: ir.Table) -> None:
    boundaries = tuple(datetime(2026, 1, day, tzinfo=timezone.utc) for day in (1, 2, 3))
    calendar = CalendarCoordinate(
        "original-calendar",
        "day",
        "UTC",
        None,
        tuple((start, end, start, end) for start, end in pairwise(boundaries)),
    )
    total = SourceAggregate(AMOUNT, "sum", None)
    fields = {"sales.orders.amount": "amount", "region": "region"}
    bindings: list[FormulaInput] = []
    components: list[ComponentFrame] = []
    fine_frames: list[pd.DataFrame] = []
    for endpoint in (0, 1):
        table = original_table.filter(original_table.id != 2) if endpoint == 0 else original_table
        reduction = ReductionInput(
            total,
            (("key.1", "region"),),
            f"window.{endpoint}",
            calendar,
            window_key=endpoint,
            calendars=(("key.0", calendar),),
        )
        summary, checks = lower_observation(reduction, table, fields, ())
        columns = tuple(
            DataColumn(name, name, data_type(summary[name].type()), True)
            for name in summary.columns
        )
        layout = component_layout(
            reduction,
            columns,
            ((DataColumn("__original_id.0", "id", ScalarDataType("int64"), False), "id"),),
            table,
            fields,
        )
        expression, _ = lower_components(layout, table, fields, ())
        fine, component = complete_components(layout, expression.execute())
        fine_frames.append(fine)
        components.append(component)
        column = next(column for column in columns if column.slot_id == reduction.value_slot)
        bindings.append(
            FormulaInput(
                (0, 0),
                f"window:{endpoint}",
                column,
                True,
                tuple((name, check.expected, check.received) for name, check in checks),
                keys=layout.keys,
                key_groups=(("key.0",), ("key.1",)),
            )
        )
    program = FormulaProgram(
        (("window", MetricWindow(total, AllHistory())),), (), tuple(bindings), calendar
    )
    fine = pd.concat([frame.loc[:, ["key.0", "key.1"]] for frame in fine_frames], ignore_index=True)
    mapping = fine.assign(**{"__target.0": fine["key.0"]})
    result, _ = rollup_components(
        program,
        tuple(components),
        ("key.0", "key.1"),
        ("key.0",),
        mapping,
        formula_columns(program),
    )
    assert result["key.0"].tolist() == [0, 1]
    assert result["value.0"].tolist() == [310, 340]


def test_drill_reads_only_selected_contributions_and_recomputes_mean(
    original_table: ir.Table,
) -> None:
    mean = SourceAggregate(AMOUNT, "mean", None)
    program, components, fine = _program(original_table, mean, (((0,), mean),))
    selected = fine.loc[fine["key.0"] == "East"]
    mapping = pd.DataFrame(
        {
            "key.0": ["East", "East"],
            "__original_id.0": [1, 2],
            "__target.0": ["East", "East"],
            "__target.1": ["Web", "Store"],
        }
    )
    keys = (
        DataColumn("key.0", "region", ScalarDataType("string"), True),
        DataColumn("key.1", "channel", ScalarDataType("string"), True),
    )
    drilled, retained = drill_components(
        program,
        components,
        selected,
        ("key.0",),
        ("key.0", "key.1"),
        (mapping,),
        formula_columns(program),
        target_columns=keys,
    )
    assert drilled[["key.0", "key.1", "value.0"]].to_dict(orient="records") == [
        {"key.0": "East", "key.1": "Web", "value.0": 10.0},
        {"key.0": "East", "key.1": "Store", "value.0": 30.0},
    ]
    drill_mapping = mapping.copy()
    mapping = drilled.loc[:, ["key.0", "key.1"]].assign(**{"__target.0": drilled["key.0"]})
    rolled, _ = rollup_components(
        program,
        retained,
        ("key.0", "key.1"),
        ("key.0",),
        mapping,
        formula_columns(program),
        target_columns=keys[:1],
    )
    assert rolled["value.0"].tolist() == [20.0]
    with pytest.raises(SemanticExpressionError, match="every selected contribution"):
        drill_components(
            program,
            components,
            selected,
            ("key.0",),
            ("key.0", "key.1"),
            (drill_mapping.iloc[:1],),
            formula_columns(program),
            target_columns=keys,
        )


def test_rollup_then_drill_keeps_per_identity_after_key_renumbering(
    original_table: ir.Table,
) -> None:
    total = SourceAggregate(AMOUNT, "sum", None)
    per = PerReduce(total, (CUSTOMER_AXIS,), "mean")
    program, components, fine = _program(original_table, per, (((0, 0), total),), samples=True)
    parent, retained = rollup_components(
        program, components, ("key.0",), (), fine.loc[:, ["key.0"]], formula_columns(program)
    )
    assert parent["value.0"].tolist() == [170]
    mapping = pd.DataFrame({"__original_id.0": [1, 2, 3], "__target.0": ["Web", "Store", "Web"]})
    result, _ = drill_components(
        program,
        retained,
        parent,
        (),
        ("key.0",),
        (mapping,),
        formula_columns(program),
        target_columns=(DataColumn("key.0", "channel", ScalarDataType("string"), True),),
    )
    assert result["key.0"].tolist() == ["Web", "Store"]
    assert result["value.0"].tolist() == [155, 30]


def test_drill_attribute_template_keeps_versions_and_selected_fiber_checks(
    original_table: ir.Table,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'], versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.orders.snapshot_day'), grain='day', timezone='UTC'))
order_id = ms.dimension_column(name='id', entity=orders, column='id')
snapshot_day = ms.time_dimension_column(name='snapshot_day', entity=orders, column='snapshot_day', granularity='day')
channel = ms.dimension_column(name='channel', entity=orders, column='channel')
profile_id = ms.dimension_column(name='profile_id', entity=orders, column='profile_id')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
profiles = ms.entity(name='profiles', datasource=ms.ref.datasource('warehouse'), source=md.table('profiles'), primary_key=['id'])
profile_key = ms.dimension_column(name='id', entity=profiles, column='id')
segment = ms.dimension_column(name='segment', entity=profiles, column='segment')
profile = ms.relationship(name='profile', from_entity=orders, to_entity=profiles, keys=[ms.join_on(profile_id, profile_key)])
""",
        }
    )
    state = project._compiled_state
    assert state is not None
    table = original_table.mutate(
        snapshot_day=ibis.ifelse(original_table.id == 2, date(2026, 1, 2), date(2026, 1, 1)),
        id=ibis.ifelse(original_table.id == 3, 2, 1),
        profile_id=ibis.ifelse(original_table.id == 3, 2, 1),
        channel=ibis.ifelse(
            original_table.id == 1, "Web", ibis.ifelse(original_table.id == 2, "Store", "Other")
        ),
    )
    backend = original_table._find_backend()
    backend.raw_sql("SET TimeZone='UTC'")
    backend._marivo_timezone_name = "UTC"
    profiles = backend.create_table(
        "original_profiles",
        pd.DataFrame({"id": [1, 2, 2], "segment": ["Good", "Bad", "Duplicate"]}),
    )
    total = SourceAggregate(AMOUNT, "sum", None)
    program, components, _ = _program(
        table, total, (((0,), total),), identity_columns=("id", "snapshot_day")
    )
    layout = components[0].layout
    stage = SourceRead(
        "source-test",
        "sales.orders",
        (0,),
        layout.columns,
        layout.keys,
        (),
        (),
        (),
        None,
        reduction=layout.reduction,
        component_layout=layout,
    )
    channel = FieldRead(ExactRef(ref.dimension("sales.orders.channel"), "test"), ROOT)
    profile_root = ExactRef(ref.entity("sales.profiles"), "test")
    segment = FieldRead(ExactRef(ref.dimension("sales.profiles.segment"), "test"), profile_root)
    entities = {"sales.orders": table, "sales.profiles": profiles}
    assert drill_entities((ROOT,), (channel, segment), state) == ("sales.orders", "sales.profiles")
    prepared = prepare_drill(
        "selected-fibers", program, (stage,), (channel, segment), entities, state
    )
    schema = prepared.schemas[0]
    assert tuple(name for _, name in schema.identity) == ("id", "snapshot_day")
    identities = tuple(column for column, _ in schema.identity)
    east = components[0].table.loc[components[0].table["key.0"] == "East", list(schema.keys)]
    parameter = StageOutput.from_frame(east, identities, schema.keys)
    selected = instantiate(
        prepared.relations[schema.source_id], schema.parameter, identities, parameter
    )
    result = selected.select(
        **{slot: selected[name] for slot, name in schema.projections},
        **{name: selected[name] for name, _, _ in schema.preparation_checks},
    ).execute()
    result = result.sort_values("__original_id.1")
    assert result["__drill_value.0"].tolist() == ["Web", "Store"]
    assert result["__drill_value.1"].tolist() == ["Good", "Good"]
    assert all(not result[name].any() for name, _, _ in schema.preparation_checks)
    west = components[0].table.loc[components[0].table["key.0"] == "West", list(schema.keys)]
    parameter = StageOutput.from_frame(west, identities, schema.keys)
    rejected = instantiate(
        prepared.relations[schema.source_id], schema.parameter, identities, parameter
    ).execute()
    assert any(rejected[name].any() for name, _, _ in schema.preparation_checks)
    from marivo._temporal import InstantAnchor

    member_axis = AxisBinding(
        ROOT, None, (), "identity", InstantAnchor.fromisoformat("2026-01-01T12:00:00+00:00")
    )
    members = LiteralPopulation((member_axis,), (((1,),),), "anchored-members")
    member_program = FormulaProgram(
        (("members", MemberCount()),), (), (), members=MemberInput(members, ("member.0",), ())
    )
    member_prepared = prepare_drill(
        "member-fibers", member_program, (), (channel, segment), entities, state
    )
    member_schema = member_prepared.schemas[0]
    member_identities = tuple(column for column, _ in member_schema.identity)
    parameter = StageOutput.from_frame(
        pd.DataFrame({"__original_member.0": [1]}), member_identities, member_schema.keys
    )
    member_table = instantiate(
        member_prepared.relations[member_schema.source_id],
        member_schema.parameter,
        member_identities,
        parameter,
    )
    result = member_table.select(
        **{slot: member_table[name] for slot, name in member_schema.projections},
        **{name: member_table[name] for name, _, _ in member_schema.preparation_checks},
    ).execute()
    assert result["__drill_value.0"].tolist() == ["Web"]
    assert result["__drill_value.1"].tolist() == ["Good"]
    assert all(not result[name].any() for name, _, _ in member_schema.preparation_checks)
