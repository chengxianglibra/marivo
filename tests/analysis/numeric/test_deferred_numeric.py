"""Native Deferred numeric DAGs share source and local demand semantics."""

import math
from decimal import Decimal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pandas as pd
import pyarrow as pa
import pytest
from ibis.common.deferred import Deferred, Variable, resolver

from marivo._definition.expressions import (
    MetricExpression,
    PredicateExpression,
    RowExpression,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import ExactRef, SumAll
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram, formula_columns
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.local import query_combine
from marivo.refs import ref
from marivo.semantic._numeric_lowering import NumericCheck, NumericValue, lower_source_reduction
from marivo.semantic._numeric_recipe import lower_recipe
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory


def test_nonnullable_source_inputs_keep_null_branches_physically_nullable() -> None:
    table = ibis.table({"x": dt.Int64(nullable=False)}, name="nonnull_values")
    field = RowField(
        ExactRef(ref.measure("sales.facts.x"), "v1"),
        ExactRef(ref.entity("sales.facts"), "v1"),
        None,
        SumAll(),
    )
    for node in (
        SourceAggregate(field, "min", None),
        SourceAggregate(field, "max", None),
        SourceAggregate(field, "sum", None),
        SourceWeightedMean(field, field, None),
    ):
        lowered = lower_source_reduction(node, table, {"sales.facts.x": table.x})
        null_casts = [
            cast
            for cast in lowered.value.op().find(ops.Cast)
            if isinstance(cast.arg, ops.Literal) and cast.arg.value is None
        ]
        assert null_casts
        assert all(cast.to.nullable for cast in null_casts)
        # ClickHouse validates every CAST, including an unselected conditional branch.
        assert "Nullable(" in ibis.to_sql(
            table.aggregate(value=lowered.value), dialect="clickhouse"
        )


def test_native_branches_mask_source_checks_and_invalid_function_inputs() -> None:
    x, y = Deferred(Variable("slot_0")), Deferred(Variable("slot_1"))
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table(
            "values",
            pd.DataFrame(
                {"x": [1.0, 0.0, 0.0], "y": [4.0, -1.0, -2.0], "bad": [False, True, True]}
            ),
        )
        inputs = (
            NumericValue(table.x),
            NumericValue(table.y, (NumericCheck(table.bad, "non-NA source values", "strict sum"),)),
        )
        recipe = resolver(ibis.ifelse(x > 0, y.sqrt(), 0))
        lowered = lower_recipe(recipe, inputs)
        expression = table.select(
            value=lowered.value,
            **{f"check_{i}": check.violation for i, check in enumerate(lowered.checks)},
        )
        source = connection.execute(expression)
        assert source.value.tolist() == [2.0, 0.0, 0.0]
        assert not source.filter(like="check_").any().any()
        physical = {name: connection.execute(table)[name].astype(object) for name in table.columns}
        local = evaluate_numeric(lowered.value.op(), physical, source.index)
        assert local.tolist() == source.value.tolist()
        assert not any(
            evaluate_numeric(check.violation.op(), physical, source.index)
            .astype("boolean")
            .fillna(False)
            .any()
            for check in lowered.checks
        )
    finally:
        connection.disconnect()


def test_combine_checks_only_demanded_completed_slots() -> None:
    a, b = Deferred(Variable("slot_0")), Deferred(Variable("slot_1"))
    placeholder = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    metric = MetricExpression(resolver(ibis.ifelse(a > 0, b, 0)), (placeholder, placeholder))
    values = (
        FormulaInput((0, 0), "a", DataColumn("a", "a", ScalarDataType("int64"), True), True),
        FormulaInput(
            (0, 1),
            "b",
            DataColumn("b", "b", ScalarDataType("int64"), True),
            True,
            (("bad", "non-NA source values", "sum(nulls='error')"),),
        ),
    )
    program = FormulaProgram((("value", metric),), (), values)
    key = DataColumn("key.0", "key", ScalarDataType("string"), False)
    stage = LocalCompute(
        "combine", ("a", "b"), (key, *formula_columns(program)), ("key.0",), program
    )
    left = pd.DataFrame({"key.0": ["yes", "no"], "a": [1, 0]})
    right = pd.DataFrame({"key.0": ["yes", "no"], "b": [10, None], "bad": [False, True]})
    assert query_combine(stage, (left, right))["value.0"].tolist() == [10, 0]
    left.loc[1, "a"] = 1
    with pytest.raises(SemanticExpressionError, match="non-NA source values"):
        query_combine(stage, (left, right))


def test_coalesce_and_cases_preserve_nullable_integer_and_decimal_values() -> None:
    x = Deferred(Variable("slot_0"))
    table = ibis.table({"x": "decimal(18,2)"})
    values = {"x": pd.Series([None, Decimal("2.50"), Decimal("-1.00")], dtype=object)}
    recipe = resolver(ibis.cases((x.isnull(), 7), (x > 0, x * 2), else_=x.abs()))
    expression = lower_recipe(recipe, (NumericValue(table.x),))
    assert evaluate_numeric(expression.value.op(), values, values["x"].index).tolist() == [
        7,
        Decimal("5.00"),
        Decimal("1.00"),
    ]
    strict = resolver(ibis.coalesce(x, (ibis.coalesce(x, 1) - 2).sqrt()))
    expression = lower_recipe(strict, (NumericValue(table.x),))
    violations = [
        evaluate_numeric(check.violation.op(), values, values["x"].index)
        .astype("boolean")
        .fillna(False)
        for check in expression.checks
    ]
    assert any(mask.tolist() == [True, False, False] for mask in violations)


def test_completed_native_nan_remains_valid_during_formula_assembly() -> None:
    x = Deferred(Variable("slot_0"))
    table = ibis.table({"x": "float64"})
    values = {
        "x": pd.Series(
            pd.arrays.ArrowExtensionArray(pa.array([float("nan"), None, 2.0], from_pandas=False))
        ).astype(object)
    }
    expression = lower_recipe(resolver(ibis.coalesce(x + 1, 7)), (NumericValue(table.x),))
    actual = evaluate_numeric(expression.value.op(), values, values["x"].index)
    assert math.isnan(actual.iloc[0])
    assert actual.iloc[1:].tolist() == [7, 3]
    missing = lower_recipe(resolver(x.isnull()), (NumericValue(table.x),))
    assert evaluate_numeric(missing.value.op(), values, values["x"].index).tolist() == [
        False,
        True,
        False,
    ]


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], 0.0),
        ([None, None], None),
        ([None, 2.0, 3.0], 5.0),
        ([-10.0, 10.0], 0.0),
        ([2.0, 3.0], 5.0),
    ],
)
def test_nullable_source_sum(values: list[float | None], expected: float | None) -> None:
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table(
            "empty_values", pd.DataFrame({"x": values}), schema={"x": "float64"}
        )
        literal = RowField(
            ExactRef(ref.measure("sales.facts.x"), "v1"),
            ExactRef(ref.entity("sales.facts"), "v1"),
            None,
            SumAll(),
        )
        null = lower_source_reduction(
            SourceAggregate(literal, "sum", None), table, {"sales.facts.x": table.x}
        )
        actual = connection.execute(table.aggregate(value=null.value)).value.iloc[0]
        assert pd.isna(actual) if expected is None else actual == expected
        count = lower_source_reduction(
            SourceAggregate(literal, "count", None), table, {"sales.facts.x": table.x}
        )
        assert connection.execute(table.aggregate(value=count.value)).value.iloc[0] == sum(
            value is not None for value in values
        )
        strict = lower_source_reduction(
            SourceAggregate(literal, "sum", None, nulls="error"),
            table,
            {"sales.facts.x": table.x},
        )
        assert any(bool(connection.execute(check.violation)) for check in strict.checks) == any(
            value is None for value in values
        )
    finally:
        connection.disconnect()


@pytest.mark.parametrize("values", ([], [None, None], [None, 2.0, 4.0]))
def test_native_avg_owns_null_and_empty_results_even_with_strict_policy(
    values: list[float | None],
) -> None:
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table(
            "average_values", pd.DataFrame({"x": values}), schema={"x": "float64"}
        )
        field = RowField(
            ExactRef(ref.measure("sales.facts.x"), "v1"),
            ExactRef(ref.entity("sales.facts"), "v1"),
            None,
            SumAll(),
        )
        lowered = lower_source_reduction(
            SourceAggregate(field, "mean", None, nulls="error"),
            table,
            {"sales.facts.x": table.x},
        )
        assert lowered.checks == ()
        assert lowered.value.op().find(ops.Mean)
        assert not lowered.value.op().find(ops.IfElse)
        actual = connection.execute(table.aggregate(value=lowered.value)).value.iloc[0]
        assert pd.isna(actual) if not any(value is not None for value in values) else actual == 3.0
    finally:
        connection.disconnect()


@pytest.mark.parametrize("operation", ("add", "multiply", "exp"))
def test_native_float_overflow_is_not_replaced_with_numeric_checks(operation: str) -> None:
    x = Deferred(Variable("slot_0"))
    expression = x + x if operation == "add" else x * x if operation == "multiply" else x.exp()
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.sql("SELECT 1e308::DOUBLE AS x")
        lowered = lower_recipe(resolver(expression), (NumericValue(table.x),))
        assert lowered.checks == ()
        assert math.isinf(connection.execute(table.select(value=lowered.value)).value.iloc[0])
    finally:
        connection.disconnect()


def test_predicate_checks_follow_coordinate_demand_without_creating_output_keys() -> None:
    from marivo.analysis.compiler.observation import ReductionInput, lower_observation

    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table(
            "predicate_values", pd.DataFrame({"category": ["kept", "filtered"], "x": [2.0, 0.0]})
        )
        field = RowField(
            ExactRef(ref.measure("sales.facts.x"), "v1"),
            ExactRef(ref.entity("sales.facts"), "v1"),
            None,
            SumAll(),
        )
        x = Deferred(Variable("slot_0"))
        predicate = RowExpression(resolver(x.log() > 0), (field,))
        reduction = SourceAggregate(field, "sum", None)
        lowered, checks = lower_observation(
            ReductionInput(reduction, (("key.0", "category"),), "value"),
            table,
            {"sales.facts.x": "x", "category": "category"},
            (PredicateExpression(Variable("slot_0"), (predicate,)),),
        )
        frame = connection.execute(lowered).set_index("key.0")
        assert frame.loc["kept", "value"] == 2
        assert bool(frame.loc["kept", "__numeric_present"])
        assert pd.isna(frame.loc["filtered", "__numeric_present"])
        predicate_check = next(
            name
            for name, check in checks
            if "valid finite numeric function inputs" in check.expected
        )
        assert not bool(frame.loc["kept", predicate_check])
        assert bool(frame.loc["filtered", predicate_check])
    finally:
        connection.disconnect()


def test_per_reduces_completed_ratios_and_retains_literal_zero_activity_samples() -> None:
    from marivo._definition.expressions import PerReduce
    from marivo._definition.relations import LiteralPopulation
    from marivo._definition.types import AxisBinding

    a, b = Deferred(Variable("slot_0")), Deferred(Variable("slot_1"))
    placeholder = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.dimension("sales.facts.customer"), "v1"),
        (),
        "field",
    )
    ratio = MetricExpression(resolver(a / b), (placeholder, placeholder))
    metric = PerReduce(ratio, (axis,), "mean")
    values = (
        FormulaInput(
            (0, 0, 0),
            "a",
            DataColumn("a", "a", ScalarDataType("int64"), True),
            True,
            keys=("key.0",),
        ),
        FormulaInput(
            (0, 0, 1),
            "b",
            DataColumn("b", "b", ScalarDataType("int64"), True),
            True,
            keys=("key.0",),
        ),
    )
    program = FormulaProgram((("mean", metric),), (), values)
    stage = LocalCompute("combine", ("a", "b"), formula_columns(program), (), program)
    left = pd.DataFrame({"key.0": ["alice", "bob"], "a": [10, 90]})
    right = pd.DataFrame({"key.0": ["alice", "bob"], "b": [2, 3]})
    assert query_combine(stage, (left, right))["value.0"].tolist() == [17.5]
    fixed = LiteralPopulation((axis,), (("alice",), ("bob",), ("empty",)), "v1")
    metric = PerReduce(placeholder, fixed, "mean")
    program = FormulaProgram(
        (("mean", metric),),
        (),
        (FormulaInput((0, 0), "a", values[0].column, True, keys=("key.0",)),),
    )
    stage = LocalCompute("combine", ("a",), formula_columns(program), (), program)
    assert query_combine(stage, (left,))["value.0"].tolist() == pytest.approx([100 / 3])


def test_native_function_chain_and_rounding_match_source_route() -> None:
    x = Deferred(Variable("slot_0"))
    connection = ibis.duckdb.connect(threads=1)
    try:
        table = connection.create_table("functions", pd.DataFrame({"x": [2.5, -2.5, 0.0]}))
        physical = {"x": pd.Series([2.5, -2.5, 0.0], dtype=object)}
        for recipe in (
            x.round(),
            x.cast("int64"),
            ibis.greatest(ibis.least((x.abs() + 1).log().exp().round(2), 3), 1).clip(1, 3),
        ):
            item = lower_recipe(resolver(recipe), (NumericValue(table.x),))
            source = connection.execute(table.select(value=item.value)).value.tolist()
            local = evaluate_numeric(item.value.op(), physical, physical["x"].index).tolist()
            assert local == pytest.approx(source)
    finally:
        connection.disconnect()


def test_state_sample_coverage_is_checked_only_when_per_value_is_demanded() -> None:
    from datetime import datetime, timezone

    from marivo._definition.expressions import PerReduce
    from marivo._definition.types import AxisBinding
    from marivo.analysis.binding.sampling import SampleCoverage

    days = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc))
    placeholder = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.time_dimension("sales.facts.day"), "v1"),
        (),
        "field",
    )
    per = PerReduce(placeholder, (axis,), "mean")
    column = DataColumn("a", "a", ScalarDataType("int64"), True)
    binding = FormulaInput(
        (0, 0),
        "a",
        column,
        True,
        keys=("key.0",),
        coverage=(SampleCoverage("key.0", days),),
        key_groups=(("key.0",),),
    )
    frame = pd.DataFrame(
        {"key.0": [days[0]], "a": [10], "__numeric_present": [True], "__sample_present": [True]}
    )
    program = FormulaProgram((("mean", per),), (), (binding,))
    stage = LocalCompute("combine", ("a",), formula_columns(program), (), program)
    with pytest.raises(SemanticExpressionError, match="complete fixed-cadence state samples"):
        query_combine(stage, (frame,))
    value = Deferred(Variable("slot_0"))
    guarded = MetricExpression(resolver(ibis.ifelse(False, value, 0)), (per,))
    from dataclasses import replace

    program = FormulaProgram((("mean", guarded),), (), (replace(binding, occurrence=(0, 0, 0)),))
    stage = LocalCompute("combine", ("a",), formula_columns(program), (), program)
    assert query_combine(stage, (frame,))["value.0"].tolist() == [0]


def test_local_sample_accumulation_uses_fsum_and_one_decimal_finish() -> None:
    from marivo._definition.expressions import PerReduce, PerWeightedMean
    from marivo._definition.types import AxisBinding
    from marivo.analysis.core.data_types import DecimalDataType
    from marivo.analysis.methods._sample_numeric import reduce_samples

    placeholder = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.dimension("sales.facts.customer"), "v1"),
        (),
        "field",
    )
    domain = pd.DataFrame(index=pd.RangeIndex(1))
    result, _ = reduce_samples(
        PerReduce(placeholder, (axis,), "sum"),
        pd.DataFrame({"value": [1e16, 1.0, -1e16]}),
        (),
        domain,
        ("value",),
        (),
        output_type=ScalarDataType("float64"),
    )
    assert result.value.tolist() == [1.0]
    result, _ = reduce_samples(
        PerReduce(placeholder, (axis,), "mean"),
        pd.DataFrame({"value": [Decimal("1.01"), Decimal("1.02")]}),
        (),
        domain,
        ("value",),
        (),
        output_type=DecimalDataType(18, 2),
    )
    assert result.value.tolist() == [Decimal("1.02")]

    result, _ = reduce_samples(
        PerWeightedMean(placeholder, placeholder, (axis,)),
        pd.DataFrame({"value": [Decimal("1.01"), Decimal("1.02")], "weight": [1.0, 2.0]}),
        (),
        domain,
        ("value", "weight"),
        (),
        output_type=ScalarDataType("float64"),
    )
    assert result.value.tolist() == pytest.approx([(1.01 + 2 * 1.02) / 3])


@pytest.mark.parametrize("calendar_slot,customer_slot", (("key.0", "key.1"), ("key.1", "key.0")))
def test_sample_coverage_respects_calendar_axis_position(
    calendar_slot: str, customer_slot: str
) -> None:
    from datetime import datetime, timezone

    from marivo._definition.observations import SampleCoverage

    days = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc))
    node = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    keys = ("key.0", "key.1", "key.2")
    binding = FormulaInput(
        (0,),
        "read",
        DataColumn("a", "a", ScalarDataType("int64"), True),
        True,
        keys=keys,
        coverage=(SampleCoverage("key.2", days, 0, calendar_slot),),
    )
    program = FormulaProgram((("value", node),), (), (binding,))
    columns = tuple(DataColumn(key, key, ScalarDataType("string"), False) for key in keys)
    stage = LocalCompute("combine", ("read",), (*columns, *formula_columns(program)), keys, program)
    frame = pd.DataFrame(
        {
            customer_slot: ["alice"],
            calendar_slot: [0],
            "key.2": [days[0]],
            "a": [10],
            "__numeric_present": [True],
            "__sample_present": [True],
        }
    )
    with pytest.raises(SemanticExpressionError, match="complete fixed-cadence"):
        query_combine(stage, (frame,))


@pytest.mark.parametrize(
    "values,expected",
    [([], 0), ([None, None], None), ([None, 2, 3], 5), ([-10, 10], 0), ([2, 3], 5)],
)
def test_internal_sum_empty_policy(values: list[int | None], expected: int | None) -> None:
    from marivo._definition.expressions import PerReduce
    from marivo._definition.types import AxisBinding
    from marivo.analysis.methods._sample_numeric import reduce_samples

    source = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.dimension("sales.facts.customer"), "v1"),
        (),
        "field",
    )
    result, _ = reduce_samples(
        PerReduce(source, (axis,), "sum"),
        pd.DataFrame({"value": pd.Series(values, dtype=object)}),
        (),
        pd.DataFrame(index=pd.RangeIndex(1)),
        ("value",),
        (),
        output_type=ScalarDataType("int64"),
    )
    actual = result.value.iloc[0]
    assert pd.isna(actual) if expected is None else actual == expected


def test_internal_sum_keeps_large_integer_beside_null_group() -> None:
    from marivo._definition.expressions import PerReduce
    from marivo._definition.types import AxisBinding
    from marivo.analysis.methods._sample_numeric import reduce_samples

    source = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.dimension("sales.facts.customer"), "v1"),
        (),
        "field",
    )
    result, _ = reduce_samples(
        PerReduce(source, (axis,), "sum"),
        pd.DataFrame({"key.0": ["A", "B"], "value": pd.Series([2**53 + 1, None], dtype=object)}),
        ("key.0",),
        pd.DataFrame({"key.0": ["A", "B"]}),
        ("value",),
        (),
        output_type=ScalarDataType("int64"),
    )
    assert result.value.tolist() == [2**53 + 1, None]


def test_internal_sample_groups_keep_complete_exact_keys_and_empty_domain_order() -> None:
    import pyarrow as pa

    from marivo._definition.expressions import PerReduce
    from marivo._definition.types import AxisBinding
    from marivo.analysis.methods._sample_numeric import reduce_samples

    source = SourceAggregate(RowExpression(resolver(ibis.literal(1)), ()), "sum", None)
    axis = AxisBinding(
        ExactRef(ref.entity("sales.facts"), "v1"),
        ExactRef(ref.dimension("sales.facts.customer"), "v1"),
        (),
        "field",
    )
    ticks = pa.array([-(2**63), None, -(2**63), 0], type=pa.int64()).cast(pa.timestamp("ns", "UTC"))
    frame = pd.DataFrame(
        {
            "key.0": pd.Series([2**53, 2**53 + 1, 2**53, 2**53 + 1], dtype="int64[pyarrow]"),
            "key.1": pd.Series(pd.arrays.ArrowExtensionArray(ticks)),
            "value": [1, 7, 2, 9],
            "inherited": [False, False, True, False],
        }
    )
    domain = frame.iloc[[3, 1, 0]].loc[:, ["key.0", "key.1"]].reset_index(drop=True)
    absent = domain.iloc[:1].copy()
    absent["key.0"] = pd.Series([2**53 + 2], dtype="int64[pyarrow]")
    domain = pd.concat([domain, absent], ignore_index=True)
    reduced, _ = reduce_samples(
        PerReduce(source, (axis,), "sum"),
        frame,
        ("key.0", "key.1"),
        domain,
        ("value",),
        (("inherited", "valid sample", "invalid sample"),),
        output_type=ScalarDataType("int64"),
    )
    assert reduced.value.tolist() == [9, 7, 3, 0]
    assert reduced["inherited"].tolist() == [False, False, True, False]
    assert reduced["key.0"].tolist() == [2**53 + 1, 2**53 + 1, 2**53, 2**53 + 2]


@pytest.mark.parametrize("scale", (1, 2))
def test_cancelled_sample_weights_do_not_hide_numeric_overflow(
    analysis_dsl_case_factory: DslCaseFactory, scale: int
) -> None:
    import duckdb

    from marivo.analysis.materialization.errors import MaterializationError

    case = analysis_dsl_case_factory("j4")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" VALUES '
            "('a','A','web','paid',TIMESTAMPTZ '2026-08-01',1e308),"
            "('b','B','web','paid',TIMESTAMPTZ '2026-08-01',-1e308)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    value = amount.max()
    # Weights are +/-scale. Products or their sum overflow despite zero total weight.
    metric = value.per(customer).weighted_mean(weight=value / 1e308 * scale)
    pending = session.observe({"value": metric})
    for result in (pending, pending.top(0, by=pending["value"])):
        with pytest.raises(MaterializationError, match="internal sample reduction overflow"):
            result.compute()
