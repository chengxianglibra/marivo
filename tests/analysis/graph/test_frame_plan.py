"""Closed Frame plans share the existing schedule without source fusion."""

from __future__ import annotations

import gc
import weakref
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import PairStatistic, ReduceValues, SelectValues, Statistic, ValueSlot
from marivo.analysis.compiler.frame_plan import CompiledSourceFrame, plan_frame
from marivo.analysis.compiler.graph_lowering import LoweredQuery, lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    PlannedCheck,
    QueryPlan,
    SourceRead,
    plan_query,
)
from marivo.analysis.compiler.native_query import NativeQueryFinish
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_program import FrameProgram, SourceParameter
from marivo.analysis.core.artifact_data import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_expression import (
    FixedFrameInput,
    FrameOperation,
    SourceFrameInput,
    infer_frame_schema,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import Binding, Fact, Obligation
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_execution import run_query_plan
from marivo.analysis.materialization.graph_local_execution import run_local
from marivo.analysis.materialization.graph_source_execution import (
    open_query_sources,
    qualify_query_sources,
    run_frame_plan,
)
from marivo.analysis.materialization.query_key_fusion import fuse_query_keys, fuse_query_terminal
from tests.shared_fixtures import DslCaseFactory


def _captured_graph(frame: AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, (SourceFrameBinding, LiteralFrameBinding))
    return frame._backing.root.graph


def _schema() -> DataSchema:
    key = DataColumn("key", "key", ScalarDataType("int64"), False)
    value = DataColumn("amount", "amount", ScalarDataType("int64"), True, "CNY")
    return DataSchema((DataAxis("key", (key,), name="key"),), (value,), value_paths=(("amount",),))


def test_concrete_and_pending_inference_share_directories_and_units() -> None:
    schema = _schema()
    pending = PendingDataSchema(
        (
            PendingDataAxis(
                "key", "key", "field", (PendingDataColumn("key", "key", "numeric", False),)
            ),
        ),
        (PendingDataColumn("amount", "amount", "numeric", True, "CNY"),),
        (("amount",),),
    )
    operation = SelectValues((("copied", ValueSlot(0)),))
    concrete = infer_frame_schema(schema, operation)
    captured = infer_frame_schema(pending, operation)
    assert concrete.values[0].unit == captured.values[0].unit == "CNY"
    assert concrete.value_paths == captured.value_paths == (("copied",),)
    assert captured.complete(concrete) == concrete


def test_calendar_binary_pending_and_concrete_share_one_closed_program() -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.compiler.query_program import FrameCombineProgram
    from marivo.analysis.core.data_schema import (
        BuiltinCalendarData,
        CalendarDataAxis,
        FrozenCalendarData,
        PairedDataAxis,
    )
    from marivo.analysis.core.frame_expression import AlignFrameValues, infer_frame_combine_schema

    ordinary = _schema()
    axis = CalendarDataAxis(
        "key",
        ordinary.axes[0].columns,
        "UTC",
        name="key",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "calendar.key"),
    )
    concrete = replace(ordinary, axes=(axis,))
    pending = PendingDataSchema(
        (axis,),
        (PendingDataColumn("amount", "amount", "numeric", True, "CNY"),),
        (("amount",),),
    )
    operation = AlignFrameValues(((0, 0),), match=CalendarCorrespondence("ordinal"))
    captured = infer_frame_combine_schema(pending, concrete, operation)
    result = infer_frame_combine_schema(concrete, concrete, operation)
    assert isinstance(captured, PendingDataSchema)
    assert isinstance(result, DataSchema)
    assert captured.complete(result) == result
    assert isinstance(result.axes[0], PairedDataAxis)
    pair = result.axes[0]
    assert len({pair.token, pair.left.token, pair.right.token}) == 3
    compiled = plan_frame(
        FrameOperation(
            (FixedFrameInput("left", concrete), FixedFrameInput("right", concrete)), operation
        ),
        {},
    )
    assert tuple(type(stage) for stage in compiled.lowered.plan.stages) == (
        FrameInput,
        FrameInput,
        FrameCompute,
    )
    binary = compiled.lowered.plan.stages[-1]
    assert isinstance(binary, FrameCompute) and isinstance(binary.program, FrameCombineProgram)
    assert binary.program.output_schema == result
    assert binary.program.operation.match == CalendarCorrespondence("ordinal")


def test_optimizer_keeps_fixed_and_frame_stages_as_hard_boundaries() -> None:
    schema = _schema()
    expression = FrameOperation(
        (FixedFrameInput("one", schema),), SelectValues((("copy", ValueSlot(0)),))
    )
    compiled = plan_frame(expression, {})
    optimized = optimize_query(compiled.lowered.plan)
    assert tuple(type(stage) for stage in optimized.stages) == (FrameInput, FrameCompute)
    assert optimized.fusion_groups == ()
    assert optimized.required_slots[0].columns == tuple(c.slot_id for c in schema.columns)
    assert lower_query(optimized, {}).reads == ()


def test_shared_fixed_stage_is_read_once_and_released_after_last_consumer() -> None:
    schema = _schema()
    leaf = FrameInput("input", "exact", schema)
    operation = SelectValues((("copy", ValueSlot(0)),))
    program = FrameProgram(operation, schema, infer_frame_schema(schema, operation))
    left, right = (
        FrameCompute("left", ("input",), program),
        FrameCompute("right", ("input",), program),
    )
    plan = QueryPlan(
        (leaf, left, right),
        (),
        (("input", ("left", "right")), ("left", ()), ("right", ())),
        "right",
    )
    reads = 0
    # StageOutput is slotted, so observe its table lifetime instead.
    table_reference: weakref.ReferenceType[pd.DataFrame] | None = None

    def fixed(stage: FrameInput) -> StageOutput:
        nonlocal reads, table_reference
        reads += 1
        output = StageOutput.from_frame(
            pd.DataFrame({"key": [1], "amount": [7]}), schema.columns, schema.key_fields
        )
        table_reference = weakref.ref(output.table)
        return output

    def frame(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        assert table_reference is not None and table_reference() is not None
        return run_local(stage, inputs)

    def forbidden(*args: object) -> StageOutput:
        raise AssertionError("fixed plan called a source reader")

    result = run_query_plan(
        plan, forbidden, forbidden, lambda *args: None, fixed=fixed, frame=frame
    )
    gc.collect()
    assert reads == 1 and table_reference is not None and table_reference() is None
    assert result.table.iloc[0, 1] == 7


def test_stage_checks_precede_source_state_stripping_and_transport() -> None:
    schema = _schema()
    leaf = FrameInput("input", "exact", schema)
    obligation = Obligation(
        Fact("finite_numeric", Binding("session", "store", "input", "scope"), "value", "v1"),
        "source.finite_numeric@v1",
        "consume",
    )
    plan = QueryPlan((leaf,), (PlannedCheck(obligation, "input"),), (("input", ()),), "input")
    order: list[str] = []

    def fixed(stage: FrameInput) -> StageOutput:
        order.append("full-validation")
        return StageOutput.from_frame(
            pd.DataFrame({"key": [1], "amount": [7]}), schema.columns, schema.key_fields
        )

    def check(requirement: PlannedCheck, output: StageOutput) -> None:
        assert tuple(output.table.columns) == ("key", "amount")
        order.append("check")

    def finish(stage: object, output: StageOutput) -> StageOutput:
        order.append("adapt-and-transport")
        return output

    def forbidden(*args: object) -> StageOutput:
        raise AssertionError("fixed schedule reached legacy source/local callback")

    run_query_plan(plan, forbidden, forbidden, check, fixed=fixed, finish=finish)
    assert order == ["full-validation", "check", "adapt-and-transport"]


def test_literal_graph_is_prefixed_and_uses_same_schedule_without_source_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("literal-compiler", timezone="UTC")
    query = session.values(by=(), columns={"amount": "CNY"}, rows=(((), {"amount": 9}),))
    child = plan_query(_captured_graph(query), ())
    final = child.stages[-1]
    schema = DataSchema((), final.columns, value_paths=(("amount",),))
    root = FrameOperation(
        (SourceFrameInput("literal", _captured_graph(query)),),
        SelectValues((("copied", ValueSlot(0)),)),
    )
    compiled = plan_frame(root, {"literal": CompiledSourceFrame(lower_query(child, {}), schema)})
    assert all(
        stage.stage_id.startswith("source:0/") for stage in compiled.lowered.plan.stages[:-1]
    )
    adapted: list[str] = []

    def adapt(input_id: str, output: StageOutput) -> StageOutput:
        adapted.append(input_id)
        return StageOutput.from_frame(output.table, schema.columns, schema.key_fields)

    def no_fixed(stage: FrameInput) -> CompletedFrameData:
        raise AssertionError("literal graph requested fixed data")

    result = run_frame_plan(compiled, source_prepared={}, read_fixed=no_fixed, adapt_source=adapt)
    assert result.table.iloc[0, 0] == 9 and adapted == ["literal"]


@pytest.mark.parametrize("invalid", [float("inf"), -float("inf")])
def test_source_adapter_complete_boundary_rejects_unused_infinity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: float
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("source-complete", timezone="UTC")
    query = session.values(
        by=(), columns={"a": None, "b": None}, rows=(((), {"a": 1.0, "b": 0.0}),)
    )
    child = plan_query(_captured_graph(query), ())
    schema = DataSchema((), child.stages[-1].columns, value_paths=(("a",), ("b",)))
    root = FrameOperation(
        (SourceFrameInput("literal", _captured_graph(query)),),
        SelectValues((("only_a", ValueSlot(0)),)),
    )
    compiled = plan_frame(root, {"literal": CompiledSourceFrame(lower_query(child, {}), schema)})

    def adapt(input_id: str, output: StageOutput) -> StageOutput:
        rows = output.table.copy()
        rows[schema.values[1].slot_id] = [invalid]
        # Transient stages retain their old contract. The Frame completion
        # boundary, before the select consumer, must reject this data.
        return StageOutput.from_frame(rows, schema.columns, schema.key_fields)

    def no_fixed(stage: FrameInput) -> CompletedFrameData:
        raise AssertionError("source-free graph requested a fixed input")

    with pytest.raises(AnalysisError, match="finite"):
        run_frame_plan(compiled, source_prepared={}, read_fixed=no_fixed, adapt_source=adapt)


def test_source_prefix_covers_native_fused_parameter_and_check_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("prefix", timezone="UTC")
    query = session.values(by=(), columns={"amount": None}, rows=(((), {"amount": 7}),))
    schema = _schema()
    formula = FormulaProgram((), (), (FormulaInput((), "removed-read", schema.values[0], False),))
    producer = SourceRead(
        "producer", "source", (), schema.columns, schema.key_fields, (), (), (), None
    )
    hidden = replace(producer, stage_id="removed-read")
    consumer = replace(
        producer,
        stage_id="consumer",
        inputs=("producer",),
        parameters=(SourceParameter("producer", "parameter", schema.columns),),
        native_reads=(hidden,),
        common_reads=(hidden,),
        native_query_finish=NativeQueryFinish(schema.columns, schema.key_fields, None),
    )
    removed = LocalCompute(
        "removed-local",
        ("consumer",),
        "query.combine@v1",
        schema.columns,
        schema.key_fields,
        program=formula,
    )
    final = replace(removed, stage_id="final", fused_steps=(removed,))
    obligation = Obligation(
        Fact("finite_numeric", Binding("session", "store", "input", "scope"), "value", "v1"),
        "source.finite_numeric@v1",
        "publish",
    )
    child = QueryPlan(
        (producer, consumer, final),
        (PlannedCheck(obligation, "consumer"),),
        (("producer", ("consumer",)), ("consumer", ("final",)), ("final", ())),
        "final",
    )
    compiled = plan_frame(
        SourceFrameInput("exact", _captured_graph(query)),
        {"exact": CompiledSourceFrame(LoweredQuery(child, ()), schema)},
    )
    stages = compiled.lowered.plan.stages
    read = stages[1]
    assert isinstance(read, SourceRead)
    assert read.parameters[0].stage_id == "source:0/producer"
    assert read.native_reads[0].stage_id == read.common_reads[0].stage_id == "source:0/removed-read"
    assert isinstance(read.native_query_finish, NativeQueryFinish)
    assert read.native_query_finish.columns == schema.columns
    assert read.native_query_finish.order == schema.key_fields
    local = stages[2]
    assert isinstance(local, LocalCompute) and local.program is not None
    assert local.fused_steps[0].stage_id == "source:0/removed-local"
    assert local.fused_steps[0].inputs == ("source:0/consumer",)
    assert local.program.values[0].stage_id == "source:0/removed-read"
    assert compiled.lowered.plan.checks[0].stage_id == "source:0/consumer"
    assert compiled.lowered.plan.checks[0].deadline == "publish"


def test_native_source_child_keeps_one_prefixed_schedule(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    query = case.session.observe({"amount": amount.sum()})
    with open_query_sources(
        case.session._runtime.connection_service(),
        _captured_graph(query),
        case.session.catalog._state,
    ) as prepared:
        child = optimize_query(plan_query(_captured_graph(query), prepared.schemas))
        lowered = lower_query(child, prepared.relations)
        qualify_query_sources(prepared, lowered)
        final = next(stage for stage in child.stages if stage.stage_id == child.publish)
        schema = DataSchema((), final.columns, value_paths=(("amount",),))
        root = FrameOperation(
            (SourceFrameInput("source", _captured_graph(query)),),
            ReduceValues((("rows", Statistic("row_count")),), ()),
        )
        compiled = plan_frame(root, {"source": CompiledSourceFrame(lowered, schema)})
        seen: list[str] = []

        def adapt(input_id: str, output: StageOutput) -> StageOutput:
            assert output.components or output.table.shape == (1, 1)
            return StageOutput.from_frame(output.table, schema.columns, schema.key_fields)

        def no_fixed(stage: FrameInput) -> CompletedFrameData:
            raise AssertionError("source child requested fixed data")

        output = run_frame_plan(
            compiled,
            source_prepared={"source": prepared},
            read_fixed=no_fixed,
            adapt_source=adapt,
            observe_read=lambda read, value: seen.append(read.stage.stage_id),
        )
        fixed = plan_frame(
            FrameOperation(
                (FixedFrameInput("fixed", _schema()),), SelectValues((("copy", ValueSlot(0)),))
            ),
            {},
        )
        for fuse in (fuse_query_keys, fuse_query_terminal):
            refused = fuse(prepared, fixed.lowered)
            assert tuple(type(stage) for stage in refused.plan.stages) == (FrameInput, FrameCompute)
            assert refused.reads == ()
    assert output.table.iloc[0, 0] == 1
    assert seen and all(identifier.startswith("source:0/") for identifier in seen)


def test_paired_reductions_stay_local_and_keep_both_endpoints(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = case.session.catalog.fields.get("sales.order.amount")
    query = case.session.observe({"amount": amount.sum(), "other": amount.mean()})
    graph = _captured_graph(query)
    with open_query_sources(
        case.session._runtime.connection_service(), graph, case.session.catalog._state
    ) as prepared:
        child = optimize_query(plan_query(graph, prepared.schemas))
        final = next(stage for stage in child.stages if stage.stage_id == child.publish)
        schema = DataSchema((), final.columns, value_paths=(("amount",), ("other",)))
        paired = FrameOperation(
            (SourceFrameInput("source", graph),),
            ReduceValues(
                (
                    (
                        "coefficient",
                        PairStatistic("pearson", ValueSlot(0), ValueSlot(1), "Column.corr"),
                    ),
                ),
                (),
            ),
        )
        expression = FrameOperation(
            (paired,), ReduceValues((("mean", Statistic("mean", ValueSlot(0))),), ())
        )
        compiled = plan_frame(
            expression,
            {"source": CompiledSourceFrame(lower_query(child, prepared.relations), schema)},
        )
        optimized = optimize_query(compiled.lowered.plan)
        frames = tuple(stage for stage in optimized.stages if isinstance(stage, FrameCompute))
        assert len(frames) == 2
        assert isinstance(frames[0].program, FrameProgram)
        assert frames[0].program.statistic_types == (
            (schema.values[0].dtype, schema.values[1].dtype),
        )
        source_boundary = next(
            stage for stage in optimized.stages if stage.stage_id == frames[0].inputs[0]
        )
        assert next(
            required
            for required in optimized.required_slots
            if required.stage_id == source_boundary.stage_id
        ).columns == tuple(column.slot_id for column in schema.columns)
        for fuse in (fuse_query_keys, fuse_query_terminal):
            fused = fuse(prepared, replace(compiled.lowered, plan=optimized))
            assert (
                tuple(stage for stage in fused.plan.stages if isinstance(stage, FrameCompute))
                == frames
            )
            assert not any(
                {frame.stage_id for frame in frames}.intersection(group.members)
                for group in fused.plan.fusion_groups
            )


@pytest.mark.parametrize("compare", [False, True])
def test_binary_self_input_has_one_read_and_two_consumer_occurrences(compare: bool) -> None:
    from marivo.analysis.compiler.query_program import FrameCombineProgram, FrameCompareProgram
    from marivo.analysis.core.frame_expression import (
        AlignFrameValues,
        CompareFrameValues,
        NamedFrameComparison,
    )

    schema = _schema()
    leaf = FixedFrameInput("shared", schema)
    operation = (
        CompareFrameValues((NamedFrameComparison("reference", ((0, 0),)),))
        if compare
        else AlignFrameValues(((0, 0),))
    )
    expression = FrameOperation((leaf, leaf), operation)
    compiled = plan_frame(expression, {})
    plan = optimize_query(compiled.lowered.plan)
    assert tuple(type(stage) for stage in plan.stages) == (FrameInput, FrameCompute)
    combine = plan.stages[-1]
    assert isinstance(combine, FrameCompute) and isinstance(
        combine.program, FrameCompareProgram if compare else FrameCombineProgram
    )
    assert combine.inputs == ("fixed:0", "fixed:0")
    assert dict(plan.consumers)["fixed:0"] == (combine.stage_id, combine.stage_id)
    reads = 0
    captured: list[weakref.ReferenceType[pd.DataFrame]] = []

    def fixed(stage: FrameInput) -> StageOutput:
        nonlocal reads
        reads += 1
        output = StageOutput.from_frame(
            pd.DataFrame({"key": [2, 1], "amount": [8, 3]}), schema.columns, schema.key_fields
        )
        captured.append(weakref.ref(output.table))
        return output

    def binary(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        assert inputs[0] is inputs[1]
        return run_local(stage, inputs)

    def forbidden(*args: object) -> StageOutput:
        raise AssertionError("fixed schedule reached a source or local reader")

    output = run_query_plan(
        plan, forbidden, forbidden, lambda c, o: None, fixed=fixed, frame=binary
    )
    assert output is not None and len(output.table) == 2 and reads == 1
    gc.collect()
    assert captured[0]() is None
    assert plan.fusion_groups == ()


def test_binary_and_unary_arities_are_closed() -> None:
    from marivo.analysis.core.frame_expression import AlignFrameValues

    leaf = FixedFrameInput("one", _schema())
    for expression in (
        FrameOperation((leaf,), AlignFrameValues(((0, 0),))),
        FrameOperation((leaf, leaf), SelectValues((("copy", ValueSlot(0)),))),
    ):
        with pytest.raises(AnalysisError, match=r"explicit.*input"):
            plan_frame(expression, {})


def test_self_combination_reuses_the_exact_derived_expression_stage() -> None:
    from marivo.analysis.core.frame_expression import CompareFrameValues, NamedFrameComparison

    leaf = FixedFrameInput("shared", _schema())
    selected = FrameOperation((leaf,), SelectValues((("amount", ValueSlot(0)),)))
    compiled = plan_frame(
        FrameOperation(
            (selected, selected),
            CompareFrameValues((NamedFrameComparison("reference", ((0, 0),)),)),
        ),
        {},
    )
    stages = compiled.lowered.plan.stages
    assert tuple(type(stage) for stage in stages) == (FrameInput, FrameCompute, FrameCompute)
    assert stages[-1].inputs == (stages[1].stage_id, stages[1].stage_id)
    assert dict(compiled.lowered.plan.consumers)[stages[1].stage_id] == (
        stages[-1].stage_id,
        stages[-1].stage_id,
    )


def test_native_division_inference_uses_typed_nonnull_proxy() -> None:
    from marivo._definition.query import ValueBinary

    schema = _schema()
    selected = infer_frame_schema(
        schema, SelectValues((("ratio", ValueBinary("div", ValueSlot(0), ValueSlot(0))),))
    )
    assert selected.values[0].dtype == ScalarDataType("float64")
    assert selected.values[0].nullable is True
    assert selected.values[0].unit == "1"


def test_named_compare_has_one_stage_and_one_read_for_three_shared_occurrences() -> None:
    from marivo.analysis.compiler.query_program import FrameCompareProgram
    from marivo.analysis.core.frame_expression import CompareFrameValues, NamedFrameComparison

    schema = _schema()
    leaf = FixedFrameInput("shared", schema)
    operation = CompareFrameValues(
        (NamedFrameComparison("one", ((0, 0),)), NamedFrameComparison("two", ((0, 0),)))
    )
    plan = optimize_query(
        plan_frame(FrameOperation((leaf, leaf, leaf), operation), {}).lowered.plan
    )
    assert tuple(type(stage) for stage in plan.stages) == (FrameInput, FrameCompute)
    compare = plan.stages[-1]
    assert isinstance(compare, FrameCompute) and isinstance(compare.program, FrameCompareProgram)
    assert compare.inputs == ("fixed:0",) * 3
    assert dict(plan.consumers)["fixed:0"] == (compare.stage_id,) * 3
    reads = 0

    def fixed(stage: FrameInput) -> StageOutput:
        nonlocal reads
        reads += 1
        return StageOutput.from_frame(
            pd.DataFrame({"key": [2, 1], "amount": [8, 3]}), schema.columns, schema.key_fields
        )

    def forbidden(*args: object) -> StageOutput:
        raise AssertionError("fixed named compare reached a source reader")

    def compare_inputs(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        assert inputs[0] is inputs[1] is inputs[2]
        return run_local(stage, inputs)

    output = run_query_plan(
        plan, forbidden, forbidden, lambda c, o: None, fixed=fixed, frame=compare_inputs
    )
    assert output is not None and len(output.table) == 2 and reads == 1
    assert plan.required_slots[0].columns == tuple(column.slot_id for column in schema.columns)
