"""Check admitted compiler rewrites using emitted plans and real execution."""

import operator
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from itertools import product
from pathlib import Path
from typing import Literal

import duckdb
import ibis
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PresenceSlot,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
from marivo.analysis.compiler.frame_filter_fusion import frame_filter_steps, fuse_frame_filters
from marivo.analysis.compiler.frame_plan import plan_frame
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    ProjectionRead,
    QueryPlan,
    QueryStage,
    SourceReadStage,
    checked_output,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    SourceFrameBinding,
)
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.graph_source_execution import (
    execute_numeric_query,
    run_frame_plan,
)
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_sources import open_query_sources
from marivo.analysis.materialization.query_terminal_fusion import fuse_query_terminal
from marivo.analysis.methods import query_values
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_evaluation import FrameEvaluation
from marivo.analysis.methods.query_values import evaluate_result
from marivo.analysis.model.data_schema import DataAxis, DataSchema
from marivo.analysis.model.data_types import (
    DataColumn,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import frame_backing
from marivo.analysis.model.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.spi import (
    SourceSession,
    TableSourceIR,
    default_datasource,
    require_profile_for_backend_type,
)
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.formal._smt import prove_unsat
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import SourceTrace

_INT = ScalarDataType("int64")
_COLUMNS = (
    DataColumn("key", "key", _INT, False),
    DataColumn("x", "x", _INT, True),
    DataColumn("y", "y", _INT, True),
)
_KEY = ("key",)
_X, _Y = ValueSlot(0), ValueSlot(1)
_ZERO = ValueLiteral(0)


def _selection_plan(first: ValueExpression, second: ValueExpression) -> QueryPlan:
    # Verify the compiler's admitted same-layout plan slice. Public Frame
    # composition has its own completed-input boundaries.
    stages = tuple(
        LocalCompute(
            name,
            (upstream,),
            _COLUMNS,
            _KEY,
            ResultProgram(FilterValues(predicate), _COLUMNS, _KEY, (), (_KEY,), (_KEY,)),
        )
        for name, upstream, predicate in (("first", "input", first), ("second", "first", second))
    )
    return QueryPlan(
        (checked_output("input", _COLUMNS, _KEY), *stages),
        (("input", ("first",)), ("first", ("second",)), ("second", ())),
        "second",
        (_KEY,),
    )


def _integer(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


def _operand(expression: ValueExpression) -> tuple[str, str]:
    if isinstance(expression, ValueSlot) and expression.index in (0, 1):
        name = ("x", "y")[expression.index]
        return name, f"{name}_null"
    if isinstance(expression, ValueLiteral) and type(expression.value) is int:
        return _integer(expression.value), "false"
    raise AssertionError(f"Unmodeled selection operand: {expression!r}")


def _kleene(
    expression: ValueExpression,
    operand: Callable[[ValueExpression], tuple[str, str]] = _operand,
    *,
    floating: frozenset[str] = frozenset(),
) -> str:
    """Translate actual plan nodes. -1/0/1 are FALSE/NULL/TRUE."""
    if isinstance(expression, ValueUnary):
        if expression.operation == "not":
            return f"(- {_kleene(expression.operand, operand, floating=floating)})"
        assert expression.operation == "is_null", expression
        if isinstance(expression.operand, (ValueUnary, ValueBinary)):
            missing = f"(= {_kleene(expression.operand, operand, floating=floating)} 0)"
        else:
            _, missing = operand(expression.operand)
        return f"(ite {missing} 1 (- 1))"
    if isinstance(expression, PresenceSlot):
        value, missing = operand(expression)
        return f"(ite {missing} 0 {value})"
    assert isinstance(expression, ValueBinary), expression
    if expression.operation in ("and", "or"):
        left = _kleene(expression.left, operand, floating=floating)
        right = _kleene(expression.right, operand, floating=floating)
        relation = "<=" if expression.operation == "and" else ">="
        return f"(ite ({relation} {left} {right}) {left} {right})"
    comparisons = {"eq": "=", "ne": "distinct", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
    assert expression.operation in comparisons, expression
    lhs, lhs_null = operand(expression.left)
    rhs, rhs_null = operand(expression.right)
    comparison = f"({comparisons[expression.operation]} {lhs} {rhs})"
    nan_flags = tuple(f"{term}_nan" for term in (lhs, rhs) if term in floating)
    if nan_flags:
        # Only inequality is true when a valid NaN participates in comparison.
        comparison = f"(ite (or {' '.join(nan_flags)}) {str(expression.operation == 'ne').lower()} {comparison})"
    return f"(ite (or {lhs_null} {rhs_null}) 0 (ite {comparison} 1 (- 1)))"


def _filters(plan: QueryPlan) -> tuple[LocalCompute, ...]:
    # Follow the executable predecessor chain rather than picking an unused node.
    stages = {stage.stage_id: stage for stage in plan.stages}
    stage = stages[plan.publish]
    filters = []
    while isinstance(stage, LocalCompute):
        (parent,) = stage.inputs
        filters.append(stage)
        stage = stages[parent]
    assert stage.stage_id == "input"
    return tuple(reversed(filters))


def _predicate(stage: LocalCompute) -> ValueExpression:
    assert isinstance(stage.program, ResultProgram)
    operation = stage.program.operation
    assert isinstance(operation, FilterValues) and isinstance(operation.predicate, ValueExpression)
    return operation.predicate


@pytest.mark.parametrize(
    "comparison,constant",
    (("ne", True), *((name, False) for name in ("eq", "ne", "lt", "le", "gt", "ge"))),
)
def test_l1_actual_rewrite_retains_the_same_rows_with_nullable_comparisons(
    comparison: Literal["eq", "ne", "lt", "le", "gt", "ge"],
    constant: bool,
) -> None:
    # Independent Python guard below also exercises the real pandas kernel.
    first = ValueBinary("or", ValueBinary("lt", _X, _ZERO), ValueBinary("eq", _Y, _ZERO))
    second = ValueBinary(comparison, _Y, ValueLiteral(-1) if constant else _X)
    original = _selection_plan(first, second)
    optimized = optimize_query(original)
    assert any(group.law == "L1" for group in optimized.fusion_groups)
    original_chain = _filters(original)
    (rewritten,) = _filters(optimized)
    sequential = " ".join(f"(= {_kleene(_predicate(stage))} 1)" for stage in original_chain)
    emitted = _kleene(_predicate(rewritten))
    prove_unsat(
        f"""(set-logic QF_LIA)
(declare-const x Int)
(declare-const y Int)
(declare-const x_null Bool)
(declare-const y_null Bool)
(define-fun sequential () Bool (and {sequential}))
(define-fun fused () Bool (= {emitted} 1))
(assert (distinct sequential fused))""",
        claim=f"L1 emitted filter ({comparison}, constant={constant})",
        witness=("x", "y", "x_null", "y_null", "sequential", "fused"),
    )
    points = (None, -(2**63), -1, 0, 1, 2**63 - 1)
    rows = list(product(points, repeat=2))
    table = pd.DataFrame(
        {
            "key": range(len(rows)),
            "x": pd.Series([x for x, _ in rows], dtype="int64[pyarrow]"),
            "y": pd.Series([y for _, y in rows], dtype="int64[pyarrow]"),
        }
    )
    compare = {
        "eq": operator.eq,
        "ne": operator.ne,
        "lt": operator.lt,
        "le": operator.le,
        "gt": operator.gt,
        "ge": operator.ge,
    }[comparison]
    expected = [
        idx
        for idx, (x, y) in enumerate(rows)
        if ((x is not None and x < 0) or y == 0)
        and y is not None
        and (compare(y, -1) if constant else x is not None and compare(y, x))
    ]
    plain = table
    for stage in original_chain:
        plain = evaluate_result(stage, (plain,))
    fused = evaluate_result(rewritten, (table,))
    assert plain["key"].tolist() == expected
    pd.testing.assert_frame_equal(fused, plain)


def test_l1_totality_rechecks_drift_and_preserves_early_demand() -> None:
    first = ValueBinary("lt", _X, _ZERO)
    guarded = ValueBinary("gt", _X, _Y)
    (fused,) = _filters(optimize_query(_selection_plan(first, guarded)))
    assert isinstance(fused.program, ResultProgram)
    assert len(fused.program.selection_steps) == 2

    # The planner saw integral slots. Native materialization can supply one
    # effective Decimal and one float. Ordering Decimal against valid NaN
    # fails on the first row, which the first predicate excludes.
    columns = (
        _COLUMNS[0],
        replace(_COLUMNS[1], dtype=DecimalDataType(12, 2)),
        replace(_COLUMNS[2], dtype=ScalarDataType("float64")),
    )
    source = pd.DataFrame(
        {
            "key": pd.Series([1, 2], dtype="int64[pyarrow]"),
            "x": pd.Series(
                [Decimal("1.00"), Decimal("-1.00")], dtype=pd.ArrowDtype(pa.decimal128(12, 2))
            ),
            "y": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([float("nan"), -2.0], type=pa.float64(), from_pandas=False)
                ),
            ),
        }
    )
    actual = replace(fused, program=replace(fused.program, columns=columns), columns=columns)
    assert evaluate_result(actual, (source,))["key"].tolist() == [2]
    with pytest.raises(InvalidOperation):
        evaluate_result(
            replace(
                actual,
                program=replace(
                    fused.program,
                    operation=FilterValues(guarded),
                    columns=columns,
                    selection_steps=(),
                ),
            ),
            (source,),
        )


def test_l1_keeps_unqualified_arithmetic_on_its_own_boundary() -> None:
    first = ValueBinary("lt", _X, _ZERO)
    unsafe = ValueBinary("gt", ValueBinary("add", _X, _Y), _ZERO)
    optimized = optimize_query(_selection_plan(first, unsafe))
    assert len(_filters(optimized)) == 2
    assert any(
        decision.rule == "L1" and decision.status == "refused"
        for decision in optimized.optimization_decisions
    )


_I64_MIN, _I64_MAX = -(2**63), 2**63 - 1
_DECIMAL_MAX = 10**38 - 1
_EXACT = DecimalDataType(38, 0)
_FRAME_KEYS = (_I64_MAX, 3, _I64_MIN, 2, -1, 0, 1)
_BOOL = ScalarDataType("boolean")
_CARRIER_LIMITS = {
    _INT: (_I64_MIN, _I64_MAX),
    _EXACT: (-_DECIMAL_MAX, _DECIMAL_MAX),
    _BOOL: (-1, 1),
}


@pytest.fixture
def filter_frame(tmp_path: Path) -> mv.AnalysisFrame:
    """Seed a completed Frame with the exact native carriers the pass consumes."""
    values = (
        DataColumn("x", "x", _INT, True),
        DataColumn("y", "y", _INT, True),
        DataColumn("exact", "exact", _EXACT, True),
        DataColumn("floating", "floating", ScalarDataType("float64"), True),
    )
    schema = DataSchema(
        (DataAxis("key", (_COLUMNS[0],), name="key"),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(_FRAME_KEYS, dtype="int64[pyarrow]"),
            "x": pd.Series([_I64_MAX, None, _I64_MIN, 0, 2, -1, 1], dtype="int64[pyarrow]"),
            "y": pd.Series([1, _I64_MAX, None, _I64_MIN, 0, -1, 1], dtype="int64[pyarrow]"),
            "exact": pd.Series(
                [
                    Decimal(_DECIMAL_MAX),
                    None,
                    Decimal(-_DECIMAL_MAX),
                    Decimal(_I64_MAX + 1),
                    Decimal(_I64_MIN),
                    Decimal(0),
                    Decimal(1),
                ],
                dtype=pd.ArrowDtype(pa.decimal128(38, 0)),
            ),
            "floating": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [float("nan"), None, -1.0, 1.0, -1.0, -1.0, -1.0],
                        type=pa.float64(),
                        from_pandas=False,
                    )
                )
            ),
        }
    )
    runtime = DatasetRuntime.create(tmp_path, "formal-l1", timezone="UTC")
    metadata = FrameResultMetadata(
        "formal-l1", EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ())
    )
    return mv.AnalysisFrame._from_memory(
        runtime.local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata)
    )


def _compile_fixed_frame(query: mv.AnalysisFrame) -> CompiledFramePlan[StageOutput]:
    captured = capture_frame_inputs(query)
    return plan_frame(captured.root, {}, frame_inputs=captured.expressions)


def _published_compute(plan: QueryPlan) -> FrameCompute:
    stage = next(stage for stage in plan.stages if stage.stage_id == plan.publish)
    assert isinstance(stage, FrameCompute)
    return stage


def _frame_filter(stage: FrameCompute) -> tuple[FrameProgram, ValueExpression]:
    program = stage.program
    assert isinstance(program, FrameProgram) and isinstance(program.operation, FilterValues)
    return program, program.operation.predicate


def _frame_operand(schema: DataSchema, expression: ValueExpression) -> tuple[str, str]:
    if isinstance(expression, ValueSlot):
        column = schema.values[expression.index]
    elif isinstance(expression, CoordinateSlot):
        (column,) = schema.axes[expression.index].columns
    elif isinstance(expression, PresenceSlot):
        column = schema.presence[schema.presence_paths.index(expression.path)]
    elif isinstance(expression, ValueLiteral) and type(expression.value) is int:
        assert _I64_MIN <= expression.value <= _I64_MAX
        return _integer(expression.value), "false"
    else:
        raise AssertionError(f"Unmodeled Frame selection operand: {expression!r}")
    assert column.dtype in _CARRIER_LIMITS or column.dtype == ScalarDataType("float64"), column
    return column.slot_id, f"{column.slot_id}_null"


def _symbolic_frame_row(schema: DataSchema) -> tuple[str, tuple[str, ...]]:
    constraints: list[str] = []
    witness: list[str] = []
    for column in schema.columns:
        if column.dtype == ScalarDataType("float64"):
            # A real super-domain retains fractional comparisons. Extreme reals
            # can represent the ordered infinities in these comparison formulas.
            # NaN and missing cells have disjoint state flags.
            symbol = column.slot_id
            constraints.extend(
                (
                    f"(declare-const {symbol} Real)",
                    f"(declare-const {symbol}_null Bool)",
                    f"(declare-const {symbol}_nan Bool)",
                    f"(assert (not (and {symbol}_null {symbol}_nan)))",
                )
            )
            if not column.nullable:
                constraints.append(f"(assert (not {symbol}_null))")
            witness.extend((symbol, f"{symbol}_null", f"{symbol}_nan"))
            continue
        if column.dtype not in _CARRIER_LIMITS:
            continue
        symbol = column.slot_id
        nullable = f"{symbol}_null"
        lower, upper = _CARRIER_LIMITS[column.dtype]
        constraints.extend(
            (
                f"(declare-const {symbol} Int)",
                f"(declare-const {nullable} Bool)",
                f"(assert (<= {_integer(lower)} {symbol} {_integer(upper)}))",
            )
        )
        if not column.nullable:
            constraints.append(f"(assert (not {nullable}))")
        if column.dtype == _BOOL:
            constraints.append(f"(assert (distinct {symbol} 0))")
        witness.extend((symbol, nullable))
    return "\n".join(constraints), tuple(witness)


def _selected_operand(frame: mv.AnalysisFrame, carrier: str) -> mv.Column:
    return frame.coord(frame.axes[0]) if carrier == "coordinate" else frame[carrier]


@pytest.mark.parametrize("carrier", ("x", "exact", "coordinate"), ids=("int64", "decimal38", "key"))
def test_public_l1_emitted_filter_preserves_selection_and_second_mask_demand(
    filter_frame: mv.AnalysisFrame, carrier: str
) -> None:
    operand = _selected_operand(filter_frame, carrier)
    first = filter_frame.where((operand < 0) | (operand >= _I64_MAX))
    query = first.where((_selected_operand(first, carrier) != -1) & (first["y"] >= -1))
    original = _compile_fixed_frame(query)
    stage = _published_compute(fuse_frame_filters(original).lowered.plan)
    fusion = stage.filter_fusion
    assert fusion is not None, carrier
    left, right = fusion.fallback
    first_program, first_predicate = _frame_filter(left)
    _, second_predicate = _frame_filter(right)
    program, _ = _frame_filter(stage)
    emitted_left, emitted_right = (step.program.operation for step in fusion.steps)
    assert isinstance(emitted_left, FilterValues) and isinstance(emitted_right, FilterValues)
    emitted = ValueBinary("and", emitted_left.predicate, emitted_right.predicate)
    assert fusion.fallback == tuple(
        step for step in original.lowered.plan.stages if isinstance(step, FrameCompute)
    )
    assert stage.inputs == left.inputs and right.inputs == (left.stage_id,)
    assert program.input_schema == first_program.input_schema
    encode = partial(_frame_operand, program.input_schema)
    row, bindings = _symbolic_frame_row(program.input_schema)
    # The admitted predicates are total on these carriers. Kleene-true gives
    # the same retention mask as short-circuit conjunction. The left subtree
    # also has to preserve exactly where the second mask is demanded.
    prove_unsat(
        f"""(set-logic QF_LIRA)
{row}
(define-fun demand () Bool (= {_kleene(first_predicate, encode)} 1))
(define-fun sequential () Bool (ite demand (= {_kleene(second_predicate, encode)} 1) false))
(define-fun fused_demand () Bool (= {_kleene(emitted.left, encode)} 1))
(define-fun fused () Bool (= {_kleene(emitted, encode)} 1))
(assert (or (distinct sequential fused) (distinct demand fused_demand)))""",
        claim=f"Public L1 emitted filter ({carrier})",
        witness=(*bindings, "demand", "sequential", "fused_demand", "fused"),
    )


@pytest.mark.parametrize(
    "mode,expected_positions,expected_kept",
    (
        ("all", tuple(range(len(_FRAME_KEYS))), (0, 1, 3, 6)),
        ("none", (), ()),
        ("mixed", (0, 3, 4, 6), (0, 3, 6)),
    ),
)
def test_public_l1_only_evaluates_the_second_mask_on_demanded_rows(
    filter_frame: mv.AnalysisFrame,
    mode: str,
    expected_positions: tuple[int, ...],
    expected_kept: tuple[int, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mode == "all":
        condition = filter_frame.coord(filter_frame.axes[0]) >= _I64_MIN
    elif mode == "none":
        condition = filter_frame["x"] > _I64_MAX
    else:
        condition = filter_frame["x"] >= 0
    first = filter_frame.where(condition)
    query = first.where((first["exact"] >= _I64_MAX) | (first["y"] > 0))
    original = _compile_fixed_frame(query)
    second_predicate = _frame_filter(_published_compute(original.lowered.plan))[1]
    policy = OptimizationPolicy.default().with_rule("L1", enabled=False)
    sequential = compute_with_policy(query, policy).to_pandas()

    calls: list[pd.DataFrame] = []
    evaluate = query_values.expression_series

    def observe(
        expression: ValueExpression,
        rows: pd.DataFrame,
        program: ResultProgram | FrameProgram,
        *,
        evaluation: FrameEvaluation | None = None,
    ) -> pd.Series:
        if expression == second_predicate:
            calls.append(rows.reset_index(drop=True))
        return evaluate(expression, rows, program, evaluation=evaluation)

    monkeypatch.setattr(query_values, "expression_series", observe)
    fused = query.compute().to_pandas()
    pd.testing.assert_frame_equal(fused, sequential)
    assert fused["key"].tolist() == [_FRAME_KEYS[index] for index in expected_kept]
    if not expected_positions:
        assert calls == []
        return
    (operands,) = calls
    if mode == "mixed":
        assert set(operands.columns) == {"exact", "y"}
    columns = ["exact", "y"]
    expected = filter_frame.to_pandas().iloc[list(expected_positions)].reset_index(drop=True)
    pd.testing.assert_frame_equal(operands.loc[:, columns], expected.loc[:, columns])


def test_public_l1_schema_change_executes_every_fallback_filter(
    filter_frame: mv.AnalysisFrame,
) -> None:
    first = filter_frame.where(filter_frame["x"] >= 0)
    second = first.where(first["x"] < 1)
    third = second.where(~second["x"].is_null())
    original = _compile_fixed_frame(third.where(third["y"] <= 0))
    compiled = fuse_frame_filters(original)
    stage = _published_compute(compiled.lowered.plan)
    fusion = stage.filter_fusion
    assert fusion is not None
    program, _ = _frame_filter(stage)

    # Model a completed producer yielding an effective Decimal carrier after
    # the consumer was planned as int64. Keep the real scheduler and kernels.
    backing = frame_backing(filter_frame)
    assert isinstance(backing, MemoryFrameBacking)
    rows = backing.data.copy_rows()
    rows["x"] = pd.Series(
        [
            Decimal("1.5"),
            None,
            Decimal("-0.5"),
            Decimal("0.5"),
            Decimal("2.0"),
            None,
            Decimal("1.0"),
        ],
        dtype=pd.ArrowDtype(pa.decimal128(38, 1)),
    )
    schema = replace(
        program.input_schema,
        values=(
            replace(program.input_schema.values[0], dtype=DecimalDataType(38, 1)),
            *program.input_schema.values[1:],
        ),
    )
    actual = StageOutput.from_completed(CompletedFrameData(schema, rows))
    assert frame_filter_steps(stage, program.input_schema) == (stage,)
    assert frame_filter_steps(stage, actual.frame_schema) == fusion.fallback, (
        "The input changed from int64 to Decimal(38,1)"
    )
    (fixed,) = [item for item in compiled.lowered.plan.stages if isinstance(item, FrameInput)]
    executed: list[str] = []
    completed = run_frame_plan(
        compiled,
        source_prepared={},
        read_fixed=lambda _: pytest.fail("The completed input must be reused"),
        adapt_source=lambda _, output: output,
        observe_local=lambda step, _: executed.append(step.stage_id),
        completed_frames={fixed.input_id: actual},
    )
    assert len(executed) == len(fusion.fallback) == 4
    assert tuple(executed) == tuple(step.stage_id for step in fusion.fallback)
    assert completed.frame_schema == schema
    pd.testing.assert_frame_equal(completed.table, rows.iloc[[3]].reset_index(drop=True))


def test_four_stage_where_fix_chain_short_circuits_and_takes_survivors_once(
    filter_frame: mv.AnalysisFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = filter_frame.where(filter_frame["x"] >= 0)
    second = first.where(~(first["floating"] < 0))
    fixed = second.fix(second.axes[0], _FRAME_KEYS[0])
    query = fixed.where(~fixed["y"].is_null())
    planned = fuse_frame_filters(_compile_fixed_frame(query)).lowered.plan
    stage = _published_compute(planned)
    fusion = stage.filter_fusion
    assert fusion is not None
    assert len(fusion.steps) == len(fusion.fallback) == 4
    assert tuple(item for item in planned.stages if isinstance(item, FrameCompute)) == (stage,)
    control = compute_with_policy(
        query, OptimizationPolicy.default().with_rule("L1", enabled=False)
    ).to_pandas()

    takes: list[tuple[tuple[str, ...], tuple[int, ...]]] = []
    actual = pd.DataFrame.take

    def audit(
        frame: pd.DataFrame,
        indices: list[int],
        axis: Literal["index", "columns", 0, 1] = 0,
        **kwargs: object,
    ) -> pd.DataFrame:
        takes.append((tuple(frame.columns), tuple(indices)))
        return actual(frame, indices, axis=axis)

    monkeypatch.setattr(pd.DataFrame, "take", audit)
    result = query.compute().to_pandas()
    pd.testing.assert_frame_equal(result, control)
    assert result["y"].tolist() == [1]
    expected = [
        (("x",), tuple(range(len(_FRAME_KEYS)))),
        (("floating",), (0, 3, 4, 6)),
        (("key",), (0, 3)),
        (("y",), (0,)),
        (("key", "x", "y", "exact", "floating"), (0,)),
    ]
    assert any(takes[index : index + len(expected)] == expected for index in range(len(takes))), (
        takes
    )


def test_three_stage_where_preserves_presence_and_valid_nan(filter_frame: mv.AnalysisFrame) -> None:
    left = filter_frame.where(filter_frame["x"] > 0).compute()
    right = filter_frame.where(filter_frame["y"] == 0).compute()
    aligned = left.align(right).compute()
    first = aligned.where(aligned["left_present"])
    second = first.where(~first["right_present"])
    query = second.where(~(second["left.floating"] < 0))
    stage = _published_compute(fuse_frame_filters(_compile_fixed_frame(query)).lowered.plan)
    fusion = stage.filter_fusion
    assert fusion is not None and len(fusion.steps) == 3
    control = compute_with_policy(
        query, OptimizationPolicy.default().with_rule("L1", enabled=False)
    ).to_pandas()
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, control)
    assert actual["key"].tolist() == [_FRAME_KEYS[0]]
    assert actual["left_present"].tolist() == [True]
    assert actual["right_present"].tolist() == [False]


def test_total_l1_scalar_carriers_use_original_comparison_kernels(tmp_path: Path) -> None:
    stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    prior = datetime(2026, 1, 1, tzinfo=timezone.utc)
    moment = TimestampDataType("ns", "UTC")
    paths = ("text", "amount", "day", "stamp", "flag")
    columns = (
        DataColumn("text", "text", ScalarDataType("string"), True),
        DataColumn("amount", "amount", DecimalDataType(12, 2), True),
        DataColumn("day", "day", ScalarDataType("date"), True),
        DataColumn("stamp", "stamp", moment, True),
        DataColumn("flag", "flag", ScalarDataType("boolean"), True),
    )
    schema = DataSchema(
        (DataAxis("key", (_COLUMNS[0],), name="key"),),
        columns,
        value_paths=tuple((path,) for path in paths),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series([1, 2, 3, 4, 5, 6], dtype="int64[pyarrow]"),
            "text": pd.Series(["a", "a", "b", "a", "a", "a"], dtype="string[pyarrow]"),
            "amount": pd.Series(
                [
                    Decimal("1.30"),
                    Decimal("1.24"),
                    Decimal("1.40"),
                    Decimal("1.40"),
                    Decimal("1.40"),
                    Decimal("1.40"),
                ],
                dtype=pd.ArrowDtype(pa.decimal128(12, 2)),
            ),
            "day": pd.Series(
                [
                    stamp.date(),
                    stamp.date(),
                    stamp.date(),
                    prior.date(),
                    stamp.date(),
                    stamp.date(),
                ],
                dtype=pd.ArrowDtype(pa.date32()),
            ),
            "stamp": pd.Series(
                [stamp, stamp, stamp, stamp, prior, stamp],
                dtype=pd.ArrowDtype(pa.timestamp("ns", tz="UTC")),
            ),
            "flag": pd.Series([True, True, True, True, True, None], dtype="bool[pyarrow]"),
        }
    )
    runtime = DatasetRuntime.create(tmp_path, "formal-l1-scalars", timezone="UTC")
    metadata = FrameResultMetadata(
        "formal-l1-scalars", EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ())
    )
    source = mv.AnalysisFrame._from_memory(
        runtime.local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata)
    )
    first = source.where(source["text"] == "a")
    second = first.where(first["amount"] >= Decimal("1.25"))
    third = second.where(second["day"] >= date(2026, 1, 2))
    query = third.where((third["stamp"] >= stamp) & third["flag"])
    compiled = _compile_fixed_frame(query)
    stage = _published_compute(fuse_frame_filters(compiled).lowered.plan)
    fusion = stage.filter_fusion
    assert fusion is not None and len(fusion.steps) == 4
    policy = OptimizationPolicy.default().with_rule("L1", enabled=False)
    control = compute_with_policy(query, policy).to_pandas()
    selected = query.compute().to_pandas()
    pd.testing.assert_frame_equal(selected, control)
    assert selected["key"].tolist() == [1]

    # Exercise the already-emitted L1(b) conjunction through the production
    # ResultProgram kernel on exactly the same typed input.
    original_steps = tuple(
        item for item in compiled.lowered.plan.stages if isinstance(item, FrameCompute)
    )
    result_stages = tuple(
        LocalCompute(
            item.stage_id,
            ("input",) if item is original_steps[0] else item.inputs,
            schema.columns,
            schema.key_fields,
            ResultProgram(
                FilterValues(predicate),
                schema.columns,
                schema.key_fields,
                (),
                program.key_groups,
                program.key_groups,
            ),
        )
        for item in original_steps
        for program, predicate in (_frame_filter(item),)
    )
    identifier = original_steps[0].inputs[0]
    result_plan = QueryPlan(
        (checked_output("input", schema.columns, schema.key_fields), *result_stages),
        tuple(
            ("input" if name == identifier else name, consumers)
            for name, consumers in compiled.lowered.plan.consumers
        ),
        original_steps[-1].stage_id,
        (schema.key_fields,),
    )
    (terminal,) = _filters(optimize_query(result_plan))
    internal = evaluate_result(terminal, (rows,))
    assert internal["key"].tolist() == [1]


@pytest.mark.parametrize("boundary", ("protected", "shared", "original"))
def test_public_l1_preserves_intermediate_selection_at_a_boundary(
    filter_frame: mv.AnalysisFrame, boundary: str
) -> None:
    first = filter_frame.where(filter_frame["x"] >= 0)
    second = first.where(first["y"] > 0)
    query = second
    if boundary == "shared":
        sibling = first.where(first["y"] <= 0)
        query = second.align(sibling, on=((second.axes[0], sibling.axes[0]),))
    compiled = _compile_fixed_frame(query)
    plan = compiled.lowered.plan
    (first_stage, first_operation), *_ = compiled.frame_operations
    protected = frozenset({id(first_operation)}) if boundary == "protected" else frozenset()
    if boundary == "original":
        # The composed Frame planner has no way to attach an original formula.
        # Supply the boundary at the optimizer input, where the guard lives.
        plan = replace(plan, original=FormulaProgram((), (), ()))
        compiled = replace(compiled, lowered=replace(compiled.lowered, plan=plan))
    assert len(plan.consumers_of(first_stage)) == (2 if boundary == "shared" else 1)
    optimized = fuse_frame_filters(compiled, protected_inputs=protected).lowered.plan
    assert optimized.stages == plan.stages
    assert any(
        decision.rule == "L1" and decision.status == "refused" and first_stage in decision.members
        for decision in optimized.optimization_decisions
    )


def test_public_l1_keeps_nan_distinct_from_na_under_negation(
    filter_frame: mv.AnalysisFrame,
) -> None:
    projected = filter_frame.select({"keep": ~(filter_frame["floating"] < 0)})
    assert projected.compute().to_pandas()["keep"].tolist() == [
        True,
        pd.NA,
        False,
        True,
        False,
        False,
        False,
    ]
    first = filter_frame.where(filter_frame.coord(filter_frame.axes[0]) >= _I64_MIN)
    query = first.where(~(first["floating"] < 0))
    plan = fuse_frame_filters(_compile_fixed_frame(query)).lowered.plan
    assert _published_compute(plan).filter_fusion is not None
    assert query.compute().to_pandas()["key"].tolist() == [_FRAME_KEYS[0], _FRAME_KEYS[3]]


_ROWS: tuple[tuple[int, str | None, int | None, int | None], ...] = (
    (1, "east", 10, 3),
    (2, "east", 10, None),  # Equal contributions must keep their multiplicity.
    (3, None, None, 8),
    (4, "west", 0, 9),
    (5, "west", -4, 9),
)
_DECIMAL_ROWS = (
    (1, "east", 1, Decimal("9007199254740992.01"), None),
    (2, "east", None, Decimal("0.09"), Decimal("2.30")),
    (3, "west", 0, None, Decimal("-1.20")),
    (4, "west", 1, Decimal("4.50"), Decimal("1.05")),
    (5, None, None, Decimal("-0.05"), Decimal("8.00")),
    (6, None, 0, None, Decimal("2.00")),
    (7, "nulls", 1, None, None),
    (8, "other", 2, Decimal("4.00"), Decimal("3.00")),
)


@pytest.fixture
def query_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> mv.Session:
    path = tmp_path / "formal-rewrites.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, extra BIGINT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", _ROWS)
        conn.execute(
            "CREATE TABLE settlements(id BIGINT, region VARCHAR, lane BIGINT, "
            "amount DECIMAL(20,2), extra DECIMAL(20,2))"
        )
        conn.executemany("INSERT INTO settlements VALUES (?, ?, ?, ?, ?)", _DECIMAL_ROWS)
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(path)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test')",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"), primary_key=["id"])
key = ms.dimension_column(name="id", entity=orders, column="id")
region = ms.dimension_column(name="region", entity=orders, column="region")
amount = ms.measure_column(name="amount", entity=orders, column="amount", unit="USD", additivity=ms.additive_all())
extra = ms.measure_column(name="extra", entity=orders, column="extra", unit="USD", additivity=ms.additive_all())
settlements = ms.entity(name="settlements", datasource=ms.ref.datasource("warehouse"),
    source=md.table("settlements"), primary_key=["id"])
sid = ms.dimension_column(name="id", entity=settlements, column="id")
group = ms.dimension_column(name="region", entity=settlements, column="region")
lane = ms.dimension_column(name="lane", entity=settlements, column="lane")
decimal_amount = ms.measure_column(name="amount", entity=settlements, column="amount", unit="USD", additivity=ms.additive_all())
decimal_extra = ms.measure_column(name="extra", entity=settlements, column="extra", unit="USD", additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("formal-rewrites", timezone="UTC")


def _source_graph(query: mv.AnalysisFrame) -> SourceFrameBinding:
    backing = query._backing
    assert isinstance(backing, ExpressionFrameBacking) and isinstance(
        backing.root, SourceFrameBinding
    )
    return backing.root


def _bag_sum(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present or not values else None


def _verify_native_differential(
    session: mv.Session,
    query: mv.AnalysisFrame,
    trace: SourceTrace,
    *,
    reads: tuple[int, int],
    policy: OptimizationPolicy | None = None,
) -> None:
    graph = _source_graph(query).graph
    with (
        session._runtime.connection_service().operation() as connections,
        open_query_sources(connections, graph, session.catalog._state) as prepared,
    ):
        original = plan_query(graph, prepared.schemas)
        optimized = optimize_query(original, policy=policy)
        assert any(group.law == "source_block" for group in optimized.fusion_groups)
        assert (
            sum(isinstance(stage, SourceReadStage) for stage in original.stages),
            sum(isinstance(stage, SourceReadStage) for stage in optimized.stages),
        ) == reads
        start = len(trace.native_sql)
        first = execute_numeric_query(prepared, lower_query(original, prepared.relations))
        middle = len(trace.native_sql)
        second = execute_numeric_query(prepared, lower_query(optimized, prepared.relations))
    assert (middle - start, len(trace.native_sql) - middle) == reads
    assert (
        first.keys == second.keys
        and first.layout == second.layout
        and first.columns == second.columns
    )
    pd.testing.assert_frame_equal(first.table, second.table)


@pytest.mark.parametrize(
    "grouped, selected",
    ((False, (1, 2, 3, 4, 5)), (True, (1, 2, 3, 4, 5)), (False, (3,)), (False, (99,))),
)
def test_actual_common_aggregate_preserves_bag_nulls_and_empty_domain(
    query_session: mv.Session,
    source_trace: SourceTrace,
    grouped: bool,
    selected: tuple[int, ...],
) -> None:
    session = query_session
    catalog = session.catalog
    key = catalog_value(catalog, "sales.orders.id", kind="dimension")
    region = catalog_value(catalog, "sales.orders.region", kind="dimension")
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(catalog, "sales.orders.extra", kind="measure")
    query = session.observe(
        {"total": amount.sum(), "extras": extra.sum(), "sample_count": amount.count()},
        by=(region,) if grouped else (),
        where=key.isin(selected),
    )
    _verify_native_differential(session, query, source_trace, reads=(3, 1))

    groups: dict[str | None, list[tuple[int | None, int | None]]] = {} if grouped else {None: []}
    for identifier, category, q, e in _ROWS:
        if identifier in selected:
            groups.setdefault(category if grouped else None, []).append((q, e))
    expected = {
        (
            label,
            _bag_sum([q for q, _ in rows]),
            _bag_sum([e for _, e in rows]),
            sum(q is not None for q, _ in rows),
        )
        for label, rows in groups.items()
    }
    result = query.compute().to_pandas()
    actual = {
        tuple(None if pd.isna(value) else value for value in (row if grouped else (None, *row)))
        for row in result.itertuples(index=False, name=None)
    }
    assert len(result) == len(expected) and actual == expected, (
        selected,
        grouped,
        actual,
        expected,
    )


@pytest.mark.parametrize("conditional, optimized_reads", ((False, 2), (True, 1)))
def test_distinct_contribution_domains_require_conditional_aggregation(
    query_session: mv.Session, source_trace: SourceTrace, conditional: bool, optimized_reads: int
) -> None:
    catalog = query_session.catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(catalog, "sales.orders.extra", kind="measure")
    key = catalog_value(catalog, "sales.orders.id", kind="dimension")
    query = query_session.observe(
        {
            "first": amount.sum().where(key <= 2),
            "other": amount.sum().where(key >= 4),
            "first_extra": extra.sum().where(key <= 2),
        },
    )
    policy = OptimizationPolicy.default().with_rule("conditional_source_merge", enabled=conditional)
    _verify_native_differential(
        query_session, query, source_trace, reads=(3, optimized_reads), policy=policy
    )
    assert query.compute().to_pandas().iloc[0].tolist() == [20, -4, 3]


def test_projection_and_empty_top_cannot_erase_discarded_strict_check(
    query_session: mv.Session,
) -> None:
    amount = catalog_value(query_session.catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(query_session.catalog, "sales.orders.extra", kind="measure")
    source = query_session.observe({"strict": amount.sum(nulls="error"), "kept": extra.sum()})
    projected = source.select({"kept": source["kept"]})
    empty = projected.top(0, by=projected["kept"])
    for candidate in (projected, empty):
        with pytest.raises(MaterializationError) as optimized:
            candidate.compute()
        with pytest.raises(MaterializationError) as original:
            compute_with_policy(candidate, OptimizationPolicy.disabled())
        assert type(optimized.value) is type(original.value)
        assert optimized.value.expected == original.value.expected
        assert optimized.value.stage == original.value.stage == "numeric_value"
        assert "nulls='error'" in (optimized.value.received or ""), optimized.value


@pytest.mark.parametrize("loose_lane,split,alternate", product((False, True), repeat=3))
def test_full_decimal_query_with_nullable_branch_filters(
    query_session: mv.Session,
    source_trace: SourceTrace,
    loose_lane: bool,
    split: bool,
    alternate: bool,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    left_filter = ((lane == 1) | (key <= 2)) if loose_lane else ((lane == 1) & (key > 1))
    right_filter = ((lane != 1) | (key == 5)) if split else left_filter
    left = amount.sum(where=left_filter)
    side = extra.sum(where=left_filter)
    right = extra.sum(where=right_filter)
    choose_left = key.count(where=left_filter) > (1 if alternate else 0)
    observed = session.observe(
        {"left": left, "side": side, "chosen": choose_left.ifelse(left, right)},
        by=(region,),
        where=key < 8,
    )
    filtered = observed.where((observed["chosen"] > 0) | (observed["side"] > 0))
    query = filtered.select({"chosen": filtered["chosen"], "side": filtered["side"]})
    start = len(source_trace.native_sql)
    optimized = query.compute().to_pandas()
    middle = len(source_trace.native_sql)
    control = compute_with_policy(query, OptimizationPolicy.disabled()).to_pandas()
    plain_reads = len(source_trace.native_sql) - middle
    optimized_reads = middle - start
    assert 0 < optimized_reads < plain_reads, (optimized_reads, plain_reads)
    pd.testing.assert_frame_equal(optimized, control)

    def subtotal(values: list[Decimal | None]) -> Decimal | None:
        known = [value for value in values if value is not None]
        return sum(known, Decimal(0)) if known or not values else None

    expected = {}
    for label in ("east", "west", "nulls", None):
        domain = [row for row in _DECIMAL_ROWS if row[0] < 8 and row[1] == label]
        lhs = (
            [row for row in domain if row[2] == 1 or row[0] <= 2]
            if loose_lane
            else [row for row in domain if row[2] == 1 and row[0] > 1]
        )
        rhs = [row for row in domain if row[2] in (0, 2) or row[0] == 5] if split else lhs
        # Absent aggregate groups are completed as if the branch saw zero rows.
        side_value = subtotal([row[4] for row in lhs])
        chosen = (
            subtotal([row[3] for row in lhs])
            if len(lhs) > (1 if alternate else 0)
            else subtotal([row[4] for row in rhs])
        )
        if (chosen is not None and chosen > 0) or (side_value is not None and side_value > 0):
            expected[label] = (chosen, side_value)
    actual = {
        None if pd.isna(label) else label: tuple(
            None if pd.isna(value) else value for value in cells
        )
        for label, *cells in optimized.itertuples(index=False, name=None)
    }
    assert len(actual) == len(optimized) and actual == expected


@pytest.mark.parametrize(
    "threshold",
    (float(2**53), Decimal("9007199254740992.0000000000000000000001")),
    ids=("mixed-float", "mixed-scale"),
)
def test_full_decimal_query_preserves_exact_comparison_boundaries(
    query_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    threshold: float | Decimal,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    one = (lane == 1) | (key == 2)
    two = lane.isnull() | (key == 6)
    observed = session.observe(
        {
            "amount": amount.sum(where=one),
            "side": extra.sum(where=one),
            "other": extra.sum(where=two),
        },
        by=(region,),
    )
    query = observed.where(
        (observed["amount"] > threshold) | (observed["amount"].is_null() & (observed["side"] > 0))
    )
    start = len(source_trace.native_sql)
    plain = compute_with_policy(query, OptimizationPolicy.disabled()).to_pandas()
    middle = len(source_trace.native_sql)
    optimized = query.compute().to_pandas()
    assert list(plain["region"]) == ["east"]
    assert plain["amount"].tolist() == [Decimal("9007199254740992.10")]
    assert 0 < len(source_trace.native_sql) - middle < middle - start
    pd.testing.assert_frame_equal(optimized, plain)


@pytest.mark.parametrize("supported", (True, False), ids=("exact-comparison", "type-rejection"))
def test_metric_predicate_compares_decimal_values_at_different_scales(
    query_session: mv.Session,
    source_trace: SourceTrace,
    supported: bool,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    total = amount.sum(where=(lane == 1) | (key == 2))
    other = extra.sum(where=lane.isnull() | (key == 6))
    threshold = Decimal("9007199254740992" if supported else "9007199254740992.00001")
    query = session.observe({"chosen": (total > threshold).ifelse(total, other)}, by=(region,))
    if not supported:
        before = len(source_trace.native_sql)
        for policy in (OptimizationPolicy.disabled(), OptimizationPolicy.default()):
            with pytest.raises(SemanticExpressionError) as rejected:
                compute_with_policy(query, policy)
            error = rejected.value
            assert error.expected == "physical types admitted by the numeric recipe"
            assert "decimal(38, 2)" in (error.received or "")
            assert "decimal(21, 5)" in (error.received or "")
            assert error.repair is not None
        assert len(source_trace.native_sql) == before
        return
    plain = compute_with_policy(query, OptimizationPolicy.disabled()).to_pandas()
    assert plain.loc[plain["region"] == "east", "chosen"].tolist() == [
        Decimal("9007199254740992.10")
    ]
    pd.testing.assert_frame_equal(query.compute().to_pandas(), plain)


@pytest.mark.parametrize("demand", ("masked", "west-only", "demanded"))
def test_full_decimal_query_keeps_strict_checks_on_their_branch(
    query_session: mv.Session,
    source_trace: SourceTrace,
    demand: str,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    left = (lane == 1) | (key == 2)
    right = (lane == 0) | (key == 5)
    strict = extra.sum(where=left, nulls="error")
    peak = extra.max(where=left, nulls="error")
    good = amount.sum(where=left)
    fallback = extra.sum(where=right)
    keys = {"masked": (99,), "west-only": (3,), "demanded": (3, 1)}[demand]
    gate = key.count(where=key.isin(keys)) > 0
    query = session.observe(
        {"chosen": gate.ifelse(strict + peak, fallback), "good": good}, by=(region,)
    )
    if demand != "demanded":
        before = len(source_trace.native_sql)
        plain = compute_with_policy(query, OptimizationPolicy.disabled()).to_pandas()
        expected = {
            "east": Decimal(0),
            "west": Decimal("-1.20") if demand == "masked" else Decimal("2.10"),
            "nulls": Decimal(0),
            None: Decimal("10.00"),
        }
        assert {
            None if pd.isna(label) else label: value
            for label, value in plain[["region", "chosen"]].itertuples(index=False, name=None)
        } == expected
        middle = len(source_trace.native_sql)
        pd.testing.assert_frame_equal(query.compute().to_pandas(), plain)
        assert 0 < len(source_trace.native_sql) - middle < middle - before
    else:
        with pytest.raises(MaterializationError) as plain_error:
            compute_with_policy(query, OptimizationPolicy.disabled())
        with pytest.raises(MaterializationError) as optimized_error:
            query.compute()
        for error in (plain_error.value, optimized_error.value):
            assert error.stage == "numeric_value"
            assert "nulls='error'" in (error.received or "")


def test_total_l1_leaves_undemanded_non_utf8_literal_at_its_original_mask() -> None:
    columns = (*_COLUMNS[:2], replace(_COLUMNS[2], dtype=ScalarDataType("string")))
    original = _selection_plan(
        ValueBinary("lt", _X, _ZERO), ValueBinary("eq", _Y, ValueLiteral(chr(0xD800)))
    )
    planned: list[QueryStage] = []
    for stage in original.stages:
        if isinstance(stage, SourceReadStage):
            planned.append(replace(stage, columns=columns))
        else:
            assert isinstance(stage, LocalCompute) and isinstance(stage.program, ResultProgram)
            planned.append(
                replace(stage, columns=columns, program=replace(stage.program, columns=columns))
            )
    optimized = optimize_query(replace(original, stages=tuple(planned)))
    assert len(_filters(optimized)) == 2
    rows = pd.DataFrame({"key": [1], "x": [1], "y": ["ok"]})
    for stage in _filters(optimized):
        rows = evaluate_result(stage, (rows,))
    assert rows.empty


@pytest.mark.parametrize(
    "sql,second",
    (
        (
            "SELECT 1::BIGINT AS key, 3::DECIMAL(12,2) AS x, CAST('NaN' AS DOUBLE) AS y",
            ValueBinary("lt", _X, _Y),
        ),
        (
            "SELECT 1::BIGINT AS key, 3::BIGINT AS x, 9::UINTEGER AS y",
            ValueBinary("lt", _X, ValueLiteral(10)),
        ),
    ),
    ids=("mixed-effective-carriers", "unused-unsigned-carrier"),
)
def test_total_l1_requalifies_without_switching_native_terminal_paths(
    sql: str, second: ValueBinary
) -> None:
    backend = ibis.duckdb.connect()
    provider = require_profile_for_backend_type("duckdb")
    with SourceSession(provider, default_datasource(), backend) as owner:
        backend.create_table("actual_selection", backend.sql(sql))
        bound = owner.bind(TableSourceIR("actual_selection"), source_identity="selection")
        raw = bound.relation
        original = _selection_plan(ValueBinary("gt", _X, _ZERO), second)
        pairs = tuple((column.slot_id, column.slot_id) for column in _COLUMNS)
        source = SourceReadStage(
            "input", "source", (), _COLUMNS, _KEY, ProjectionRead(pairs), pairs, (), None
        )
        plan = replace(original, stages=(source, *original.stages[1:]))
        prepared = PreparedSources(owner, (), {"source": raw}, {"source": bound}, {"source": owner})
        optimized = optimize_query(plan)
        assert len(_filters(optimized)) == 1
        for route in (plan, optimized):
            merged = fuse_query_terminal(
                prepared, LoweredQuery(route, (LoweredRead("input", raw),))
            )
            stage = merged.plan.stages[-1]
            assert isinstance(stage, SourceReadStage)
            expression = merged.read(stage.stage_id).expression
            assert expression.select("key").to_pyarrow().column("key").to_pylist() == [1]
