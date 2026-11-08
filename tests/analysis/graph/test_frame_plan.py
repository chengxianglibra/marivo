"""Closed Frame plans keep inferred schemas, literal results, and fixed-frame boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._definition.query import (
    ReduceValues,
    SelectValues,
    Statistic,
    ValueSlot,
)
from marivo.analysis.compiler.frame_plan import CompiledSourceFrame, plan_frame
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    plan_query,
)
from marivo.analysis.compiler.optimization import optimize_query
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
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    open_query_sources,
    qualify_query_sources,
    run_frame_plan,
)
from marivo.analysis.materialization.query_key_fusion import fuse_query_keys, fuse_query_terminal
from tests.shared_fixtures import DslCaseFactory, catalog_value


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
    binary = compiled.lowered.plan.stages[-1]
    assert isinstance(binary, FrameCompute) and isinstance(binary.program, FrameCombineProgram)
    assert binary.program.output_schema == result
    assert binary.program.operation.match == CalendarCorrespondence("ordinal")


def test_literal_graph_is_prefixed_and_uses_same_schedule_without_source_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("literal-compiler", timezone="UTC")
    query = session.values(by=(), columns={"amount": "CNY"}, rows=(((), {"amount": 9}),))
    child = plan_query(_captured_graph(query), ())
    final = child.stages[-1]
    schema = DataSchema((), final.columns, value_paths=(("amount",),))
    root = FrameOperation(
        (SourceFrameInput("literal", _captured_graph(query)),),
        SelectValues((("copied", ValueSlot(0)),)),
    )
    compiled = plan_frame(root, {"literal": CompiledSourceFrame(lower_query(child, {}), schema)})
    adapted: list[str] = []

    def adapt(input_id: str, output: StageOutput) -> StageOutput:
        adapted.append(input_id)
        return StageOutput.from_frame(output.table, schema.columns, schema.key_fields)

    def no_fixed(stage: FrameInput) -> CompletedFrameData:
        raise AssertionError("literal graph requested fixed data")

    result = run_frame_plan(compiled, source_prepared={}, read_fixed=no_fixed, adapt_source=adapt)
    assert result.table.iloc[0, 0] == 9 and adapted == ["literal"]


@pytest.mark.parametrize("invalid", [float("inf"), -float("inf")])
def test_source_adapter_complete_boundary_preserves_unused_native_infinity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: float
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("source-complete", timezone="UTC")
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
        # Backend floating values remain valid even when a later select omits them.
        return StageOutput.from_frame(rows, schema.columns, schema.key_fields)

    def no_fixed(stage: FrameInput) -> CompletedFrameData:
        raise AssertionError("source-free graph requested a fixed input")

    result = run_frame_plan(compiled, source_prepared={}, read_fixed=no_fixed, adapt_source=adapt)
    assert result.table["value.0"].tolist() == [1.0]


def test_native_source_child_keeps_one_prefixed_schedule(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="measure")
    query = case.session.observe({"amount": amount.sum()})
    with (
        case.session._runtime.connection_service().operation() as connections,
        open_query_sources(
            connections,
            _captured_graph(query),
            case.session.catalog._state,
        ) as prepared,
    ):
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
        )
        fixed = plan_frame(
            FrameOperation(
                (FixedFrameInput("fixed", _schema()),), SelectValues((("copy", ValueSlot(0)),))
            ),
            {},
        )
        for fuse in (fuse_query_keys, fuse_query_terminal):
            refused = fuse(prepared, fixed.lowered)
            assert refused.reads == ()
    assert output.table.iloc[0, 0] == 1


def test_binary_and_unary_arities_are_closed() -> None:
    from marivo.analysis.core.frame_expression import AlignFrameValues

    leaf = FixedFrameInput("one", _schema())
    for expression in (
        FrameOperation((leaf,), AlignFrameValues(((0, 0),))),
        FrameOperation((leaf, leaf), SelectValues((("copy", ValueSlot(0)),))),
    ):
        with pytest.raises(AnalysisError, match=r"explicit.*input"):
            plan_frame(expression, {})


def test_native_division_inference_uses_typed_nonnull_proxy() -> None:
    from marivo._definition.query import ValueBinary

    schema = _schema()
    selected = infer_frame_schema(
        schema, SelectValues((("ratio", ValueBinary("div", ValueSlot(0), ValueSlot(0))),))
    )
    assert selected.values[0].dtype == ScalarDataType("float64")
    assert selected.values[0].nullable is True
    assert selected.values[0].unit == "1"
