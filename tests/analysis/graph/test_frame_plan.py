"""Closed Frame plans share the existing schedule without source fusion."""

from __future__ import annotations

import gc
import weakref
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import ReduceValues, SelectValues, Statistic, ValueSlot
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
from marivo.analysis.compiler.native_formula import NativeOriginalFinish
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_program import FrameProgram, SourceParameter
from marivo.analysis.core.artifact_data import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import CompletedFrameData
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
from marivo.analysis.core.model import Binding, Fact, Obligation
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
from marivo.analysis.materialization.source_original import fuse_source_original
from tests.shared_fixtures import DslCaseFactory


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
    session = mv.session.get_or_create("literal-compiler", report_timezone="UTC")
    query = session.values(by=(), columns={"amount": "CNY"}, rows=(((), {"amount": 9}),))
    child = plan_query(query._graph, ())
    final = child.stages[-1]
    schema = DataSchema((), final.columns, value_paths=(("amount",),))
    root = FrameOperation(
        (SourceFrameInput("literal", query._graph),), SelectValues((("copied", ValueSlot(0)),))
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
    session = mv.session.get_or_create("source-complete", report_timezone="UTC")
    query = session.values(
        by=(), columns={"a": None, "b": None}, rows=(((), {"a": 1.0, "b": 0.0}),)
    )
    child = plan_query(query._graph, ())
    schema = DataSchema((), child.stages[-1].columns, value_paths=(("a",), ("b",)))
    root = FrameOperation(
        (SourceFrameInput("literal", query._graph),), SelectValues((("only_a", ValueSlot(0)),))
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
    session = mv.session.get_or_create("prefix", report_timezone="UTC")
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
        native_original_finish=NativeOriginalFinish(formula, schema.key_fields, schema.values, ()),
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
        SourceFrameInput("exact", query._graph),
        {"exact": CompiledSourceFrame(LoweredQuery(child, ()), schema)},
    )
    stages = compiled.lowered.plan.stages
    read = stages[1]
    assert isinstance(read, SourceRead)
    assert read.parameters[0].stage_id == "source:0/producer"
    assert read.native_reads[0].stage_id == read.common_reads[0].stage_id == "source:0/removed-read"
    assert isinstance(read.native_original_finish, NativeOriginalFinish)
    assert read.native_original_finish.program.values[0].stage_id == "source:0/removed-read"
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
        case.session._runtime.connection_service(), query._graph, case.session.catalog._state
    ) as prepared:
        child = optimize_query(plan_query(query._graph, prepared.schemas, context=prepared.context))
        lowered = lower_query(child, prepared.relations)
        qualify_query_sources(prepared, lowered)
        final = next(stage for stage in child.stages if stage.stage_id == child.publish)
        schema = DataSchema((), final.columns, value_paths=(("amount",),))
        root = FrameOperation(
            (SourceFrameInput("source", query._graph),),
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
        for fuse in (fuse_query_keys, fuse_query_terminal, fuse_source_original):
            refused = fuse(prepared, fixed.lowered)
            assert tuple(type(stage) for stage in refused.plan.stages) == (FrameInput, FrameCompute)
            assert refused.reads == ()
    assert output.table.iloc[0, 0] == 1
    assert seen and all(identifier.startswith("source:0/") for identifier in seen)
