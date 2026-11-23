"""Adjacent public projections compose at the original checked carrier boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
from marivo.analysis.compiler.frame_plan import plan_frame
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.query_stages import FrameCompute, FrameInput, QueryStage
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.graph_source_execution import run_frame_plan
from marivo.analysis.materialization.optimization import optimize_compiled_frame
from marivo.analysis.methods import query_values
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from marivo.analysis.model.data_schema import DataAxis, DataSchema
from marivo.analysis.model.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from tests.support.optimization import compute_with_policy

_ON = OptimizationPolicy.default()
_OFF = _ON.with_rule("L5_select_fusion", enabled=False)
_INT = ScalarDataType("int64")
_FLOAT = ScalarDataType("float64")
_I64_MAX = 2**63 - 1


@pytest.fixture
def projection_input(tmp_path: Path) -> tuple[mv.AnalysisFrame, CompletedFrameData]:
    """An exact fixed domain with one native NaN and one Arrow null."""
    values = (
        DataColumn("exact", "exact", DecimalDataType(38, 0), True, "1"),
        DataColumn("divisor", "divisor", _INT, True, "1"),
        DataColumn("integer", "integer", _INT, True, "1"),
        DataColumn("floating", "floating", _FLOAT, True, "1"),
    )
    schema = DataSchema(
        (DataAxis("key", (DataColumn("key", "key", _INT, False),), name="key"),),
        values,
        presence=(DataColumn("presence.seen", "seen", ScalarDataType("boolean"), False),),
        value_paths=tuple((column.name,) for column in values),
        presence_paths=(("seen",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series([3, 1, 2], dtype="int64[pyarrow]"),
            "exact": pd.Series(
                [
                    Decimal("9007199254740993"),
                    Decimal("99999999999999999999999999999999999999"),
                    None,
                ],
                dtype=pd.ArrowDtype(pa.decimal128(38, 0)),
            ),
            "divisor": pd.Series([1, 0, 1], dtype="int64[pyarrow]"),
            "integer": pd.Series([_I64_MAX, -_I64_MAX - 1, 3], dtype="int64[pyarrow]"),
            "floating": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([float("nan"), None, 2.0], type=pa.float64())
                )
            ),
            "presence.seen": pd.Series([True, False, True], dtype="bool[pyarrow]"),
        }
    )
    runtime = DatasetRuntime.create(tmp_path, "select-fusion", timezone="UTC")
    metadata = FrameResultMetadata(
        "select-fusion",
        EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ()),
    )
    data = CompletedFrameData(schema, rows)
    return mv.AnalysisFrame._from_memory(runtime.local, CompletedFrameResult(data, metadata)), data


def _plan(
    query: mv.AnalysisFrame, policy: OptimizationPolicy = _ON
) -> CompiledFramePlan[StageOutput]:
    captured = capture_frame_inputs(query)
    return plan_frame(captured.root, {}, frame_inputs=captured.expressions, policy=policy)


def _run(
    compiled: CompiledFramePlan[StageOutput],
    data: CompletedFrameData,
    *,
    checked: dict[str, StageOutput] | None = None,
) -> tuple[StageOutput, tuple[str, ...]]:
    materialized: list[str] = []

    def observe(stage: QueryStage, output: StageOutput) -> None:
        if isinstance(stage, FrameCompute):
            assert output.frame_schema is not None
            materialized.append(stage.stage_id)

    output = run_frame_plan(
        compiled,
        source_prepared={},
        read_fixed=lambda _: data,
        adapt_source=lambda _, output: output,
        observe_local=observe,
        completed_frames=checked,
    )
    return output, tuple(materialized)


@pytest.mark.parametrize("length", (2, 3))
def test_projection_chain_materializes_once_and_reuses_finished_native_values(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
    monkeypatch: pytest.MonkeyPatch,
    length: int,
) -> None:
    source, data = projection_input
    first = source.select(
        {
            "ratio": source["exact"] / source["divisor"],
            "unused": source["integer"],
            "carried": source["floating"],
            "was_seen": source["seen"],
        }
    )
    second = first.select(
        {
            "doubled": first["ratio"] + first["ratio"],
            "missing": first["carried"].is_null(),
            "was_seen": first["was_seen"],
        }
    )
    query = (
        second
        if length == 2
        else second.select(
            {
                "doubled": second["doubled"],
                "missing": second["missing"],
                "was_seen": second["was_seen"],
                "key_value": second.coord(second.axes[0]),
            }
        )
    )
    baseline, original_events = _run(_plan(query, _OFF), data)
    compiled = _plan(query)
    optimized = optimize_compiled_frame(compiled, {}, frozenset(), _ON)
    plan = optimized.lowered.plan
    (group,) = (group for group in plan.fusion_groups if group.law == "L5")
    assert group.members == original_events and len(group.members) == length
    assert any(
        decision.rule == "L5_select_fusion" and decision.status == "adopted"
        for decision in plan.optimization_decisions
    )
    unoptimized = optimize_compiled_frame(_plan(query, _OFF), {}, frozenset(), _OFF)
    assert not any(group.law == "L5" for group in unoptimized.lowered.plan.fusion_groups)

    divisions = 0

    def count_divisions(operation: str, left: pd.Series, right: pd.Series) -> pd.Series:
        nonlocal divisions
        divisions += operation == "divide"
        return nullable_arithmetic(operation, left, right)

    with monkeypatch.context() as instrument:
        instrument.setattr(query_values, "nullable_arithmetic", count_divisions)
        result, events = _run(optimized, data)
    assert events == (plan.publish,)
    assert divisions == 1
    assert result.frame_schema == baseline.frame_schema
    assert result.unique_keys == baseline.unique_keys
    pd.testing.assert_frame_equal(result.table, baseline.table)

    published = query.compute().to_pandas()
    assert published["key"].tolist() == [3, 1, 2]
    assert pa.array(published["doubled"]).to_pylist() == [18014398509481984.0, None, None]
    assert str(published["doubled"].dtype) == "double[pyarrow]"
    assert published["missing"].tolist() == [False, True, False]
    assert published["was_seen"].tolist() == [True, False, True]
    if length == 3:
        assert published["key_value"].tolist() == [3, 1, 2]


@pytest.mark.parametrize("present", (False, True), ids=("empty", "unit"))
def test_projection_chain_preserves_zero_axis_row_count(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
    present: bool,
) -> None:
    source, data = projection_input
    scalar = source.fix(source.axes[0], 3 if present else 999)
    intermediate = scalar.select({"remaining": scalar["divisor"]})
    query = intermediate.select({})
    compiled = optimize_compiled_frame(_plan(query), {}, frozenset(), _ON)
    result, events = _run(compiled, data)
    assert len(events) == 2  # Fix, then the fused projection.
    assert not result.keys
    assert result.frame_schema is not None and not result.frame_schema.values
    assert len(result.table) == int(present)


def test_dropped_projection_keeps_its_original_early_arithmetic_failure(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
) -> None:
    source, _ = projection_input
    first = source.select({"bad": source["integer"] + 1, "ok": source["integer"]})
    second = first.select({"ok": first["ok"]})
    # The last stage also fails, with a different operation, if reached.
    query = second.select({"later": second["ok"] * 2})
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert caught.value.stage == "numeric_value"
        assert caught.value.expected == "native numeric arithmetic"
        assert (caught.value.received or "").startswith("add:")


@pytest.mark.parametrize("later_failure", (False, True))
def test_dropped_projection_keeps_normalization_and_evaluation_in_original_order(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
    later_failure: bool,
) -> None:
    source, _ = projection_input
    fields = {"too_wide": source["exact"] + 1, "ok": source["integer"]}
    if later_failure:
        fields["later"] = source["integer"] * 2
    first = source.select(fields)
    query = first.select({"kept": first["ok"]})
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        if later_failure:
            assert caught.value.stage == "numeric_value"
            assert (caught.value.received or "").startswith("multiply:")
        else:
            assert caught.value.stage == "stage_output"
            assert (caught.value.expected or "").startswith("value.0: ")


@pytest.mark.parametrize("share", (False, True), ids=("partition", "share"))
def test_cross_row_projection_refuses_fusion(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
    share: bool,
) -> None:
    source, data = projection_input
    cross_row = (
        source["divisor"].share(along=source.axes)
        if share
        else source.over(source["divisor"].sum())
    )
    first = source.select({"denominator": cross_row})
    query = first.select({"kept": first["denominator"]})
    compiled = _plan(query)
    schedule = optimize_compiled_frame(compiled, {}, frozenset(), _ON)
    plan = schedule.lowered.plan
    assert any(
        d.rule == "L5_select_fusion" and d.status == "refused" and "partition or share" in d.reason
        for d in plan.optimization_decisions
    )
    output, events = _run(schedule, data)
    assert len(events) == 2
    assert output.table["value.0"].tolist() == ([0.5, 0.0, 0.5] if share else [2, 2, 2])


def test_shared_projection_stays_available_to_both_consumers(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
) -> None:
    source, data = projection_input
    first = source.select({"n": source["divisor"]})
    left = first.select({"n": first["n"] + 1})
    right = first.select({"n": first["n"] + 2})
    query = left.align(right, on=((left.axes[0], right.axes[0]),))
    optimized = optimize_compiled_frame(_plan(query), {}, frozenset(), _ON)
    plan = optimized.lowered.plan
    assert not any(group.law == "L5" for group in plan.fusion_groups)
    assert any(
        decision.rule == "L5_select_fusion"
        and decision.status == "refused"
        and "multiple consumers" in decision.reason
        for decision in plan.optimization_decisions
    )
    completed, events = _run(optimized, data)
    assert len(events) == 4
    assert set(
        zip(
            completed.table["key"],
            completed.table["value.0"],
            completed.table["value.1"],
            strict=True,
        )
    ) == {
        (3, 2, 3),
        (1, 1, 2),
        (2, 2, 3),
    }


def test_checkpoint_projection_remains_a_separately_observable_stage(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
) -> None:
    source, data = projection_input
    first = source.select({"denominator": source["divisor"]})
    query = first.select({"kept": first["denominator"]})
    compiled = _plan(query)
    inner_id, expression = compiled.frame_operations[0]
    optimized = optimize_compiled_frame(compiled, {}, frozenset((id(expression),)), _ON)
    assert optimized.frame_operations == compiled.frame_operations
    assert any(
        d.rule == "L5_select_fusion" and d.status == "refused" and "checkpoint" in d.reason
        for d in optimized.lowered.plan.optimization_decisions
    )
    _, events = _run(optimized, data)
    assert events == (inner_id, optimized.lowered.plan.publish)


def test_changed_input_carrier_replays_the_original_projection_stages(
    projection_input: tuple[mv.AnalysisFrame, CompletedFrameData],
) -> None:
    source, data = projection_input
    first = source.select({"n": source["divisor"]})
    second = first.select({"n": first["n"]})
    query = second.select({"kept": second["n"]})
    compiled = _plan(query)
    schedule = optimize_compiled_frame(compiled, {}, frozenset(), _ON)
    (inner,) = (item for item in schedule.lowered.plan.stages if isinstance(item, FrameInput))
    (published,) = (item for item in schedule.lowered.plan.stages if isinstance(item, FrameCompute))
    assert published.select_fusion is not None and isinstance(published.program, FrameProgram)
    schema = data.schema
    carrier = DecimalDataType(16, 2)
    actual = replace(
        schema,
        values=tuple(
            replace(column, dtype=carrier) if column.slot_id == "divisor" else column
            for column in schema.values
        ),
    )
    values = [Decimal("1.25"), Decimal("0.25"), Decimal("1.25")]
    rows = data.copy_rows().assign(
        divisor=pd.arrays.ArrowExtensionArray(pa.array(values, type=pa.decimal128(16, 2))),
    )
    checked = {inner.input_id: StageOutput.from_completed(CompletedFrameData(actual, rows))}
    before, before_events = _run(compiled, data, checked=checked)
    after, after_events = _run(schedule, data, checked=checked)
    assert after_events == before_events and len(after_events) == 3
    assert after.frame_schema == before.frame_schema
    assert after.table["value.0"].tolist() == values
    assert str(after.table["value.0"].dtype) == "decimal128(16, 2)[pyarrow]"
    pd.testing.assert_frame_equal(after.table, before.table)
