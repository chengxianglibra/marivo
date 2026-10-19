"""Data kernels and fixed-input checks in the existing Query scheduler."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pandas as pd
import pytest

from marivo._definition.query import (
    FilterValues,
    PresenceSlot,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueLiteral,
    ValueSlot,
)
from marivo.analysis.compiler.frame_plan import plan_frame
from marivo.analysis.compiler.graph_plan import FrameInput
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.core.artifact_data import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    FrozenCalendarData,
    OutputAttachment,
)
from marivo.analysis.core.data_types import DecimalDataType, DurationDataType, ScalarDataType
from marivo.analysis.core.frame_data import CompletedFrameData
from marivo.analysis.core.frame_expression import (
    FixedFrameInput,
    FrameOperation,
    FrameOperationKind,
)
from marivo.analysis.core.frozen_calendar import calendar_directory_schema
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput, normalize_complete_frame
from marivo.analysis.materialization.graph_source_execution import run_frame_plan
from marivo.semantic.errors import SemanticExpressionError


def _data() -> CompletedFrameData:
    schema = DataSchema(
        (
            DataAxis(
                "place", (DataColumn("key", "place", ScalarDataType("string"), True),), name="place"
            ),
        ),
        (
            DataColumn("a", "a", ScalarDataType("int64"), True, "CNY"),
            DataColumn("b", "b", ScalarDataType("int64"), True, "CNY"),
        ),
        value_paths=(("a",), ("b",)),
    )
    return CompletedFrameData(
        schema,
        pd.DataFrame(
            {
                "key": ["C", "A", "B", None],
                "a": pd.Series([10, 10, None, 5], dtype="int64[pyarrow]"),
                "b": pd.Series([None, 20, 30, 40], dtype="int64[pyarrow]"),
            }
        ),
    )


def _keys(frame: pd.DataFrame) -> list[str | None]:
    return [None if pd.isna(value) else str(value) for value in frame["key"]]


def _run(
    data: CompletedFrameData, *operations: FrameOperationKind
) -> tuple[DataSchema, pd.DataFrame]:
    root = FixedFrameInput("fixed-exact", data.schema)
    expression = FrameOperation((root,), operations[0]) if operations else root
    for operation in operations[1:]:
        expression = FrameOperation((expression,), operation)
    compiled = plan_frame(expression, {})
    compiled = replace(
        compiled, lowered=replace(compiled.lowered, plan=optimize_query(compiled.lowered.plan))
    )

    def read(stage: FrameInput) -> CompletedFrameData:
        assert stage.input_id == root.input_id
        return data

    def forbidden(input_id: str, output: StageOutput) -> StageOutput:
        raise AssertionError("fixed inputs reached source adapter")

    result = run_frame_plan(compiled, source_prepared={}, read_fixed=read, adapt_source=forbidden)
    return compiled.schema, result.table


def test_select_where_keep_input_sequence_and_null_masks() -> None:
    data = _data()
    schema, result = _run(
        data,
        SelectValues(
            (("sum", ValueBinary("add", ValueSlot(0), ValueSlot(1))), ("a", ValueSlot(0)))
        ),
    )
    assert _keys(result) == ["C", "A", "B", None]
    assert result["value.0"].isna().tolist() == [True, False, True, False]
    assert result["value.1"].isna().tolist() == [False, False, True, False]
    assert schema.values[0].unit == "CNY"
    _, selected = _run(data, FilterValues(ValueBinary("ge", ValueSlot(1), ValueLiteral(20))))
    assert _keys(selected) == ["A", "B", None]
    assert _keys(data.copy_rows()) == ["C", "A", "B", None]


@pytest.mark.parametrize(
    "descending,expected", [(True, ["A", "C", None, "B"]), (False, [None, "A", "C", "B"])]
)
def test_global_top_uses_complete_key_ties_and_na_last(
    descending: bool, expected: list[str | None]
) -> None:
    _, result = _run(_data(), TopValues(4, ValueSlot(0), descending))
    assert _keys(result) == expected
    _, chained = _run(
        _data(),
        TopValues(3, ValueSlot(0), descending),
        SelectValues((("kept", ValueSlot(0)),)),
        FilterValues(ValueBinary("ge", ValueSlot(0), ValueLiteral(0))),
    )
    assert _keys(chained) == expected[:3]


def test_statistics_use_independent_masks_and_empty_scalar_contract() -> None:
    operation = ReduceValues(
        (
            ("a_mean", Statistic("mean", ValueSlot(0))),
            ("b_mean", Statistic("mean", ValueSlot(1))),
            ("rows", Statistic("row_count")),
        ),
        (),
    )
    _, result = _run(_data(), operation)
    assert result.iloc[0].tolist() == [25 / 3, 30, 4]
    data = _data()
    empty = CompletedFrameData(data.schema, data.copy_rows().iloc[:0].copy())
    _, result = _run(empty, operation)
    assert len(result) == 1 and result.iloc[0, :2].isna().all() and result.iloc[0, 2] == 0
    _, result = _run(empty, ReduceValues((("sum", Statistic("sum", ValueSlot(0))),), (0,)))
    assert result.empty


def test_reduce_keeps_whole_composite_axis_and_canonical_group_order() -> None:
    key_a = DataColumn("ka", "country", ScalarDataType("string"), False)
    key_b = DataColumn("kb", "city", ScalarDataType("string"), False)
    extra = DataColumn("part", "part", ScalarDataType("int64"), False)
    value = DataColumn("v", "revenue", ScalarDataType("int64"), True)
    schema = DataSchema(
        (
            DataAxis("city", (key_a, key_b), kind="member", name="city"),
            DataAxis("part", (extra,), name="part"),
        ),
        (value,),
        value_paths=(("revenue",),),
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame(
            {"ka": ["US", "CN", "US"], "kb": ["X", "Y", "X"], "part": [2, 1, 1], "v": [10, 30, 20]}
        ),
    )
    output, result = _run(data, ReduceValues((("total", Statistic("sum", ValueSlot(0))),), (0,)))
    assert tuple(c.slot_id for c in output.axes[0].columns) == ("ka", "kb")
    assert list(result.itertuples(index=False, name=None)) == [("CN", "Y", 30), ("US", "X", 30)]
    _, ranked = _run(data, TopValues(3, ValueLiteral(1), True))
    assert list(ranked.loc[:, ["ka", "kb", "part"]].itertuples(index=False, name=None)) == [
        ("CN", "Y", 1),
        ("US", "X", 1),
        ("US", "X", 2),
    ]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_duration_arithmetic_and_statistics_preserve_exact_ticks(
    unit: Literal["s", "ms", "us", "ns"],
) -> None:
    dtype = DurationDataType(unit)
    key = DataColumn("k", "k", ScalarDataType("int64"), False)
    duration = DataColumn("d", "duration", dtype, True, "elapsed")
    schema = DataSchema(
        (DataAxis("k", (key,), name="k"),), (duration,), value_paths=(("duration",),)
    )
    rows = pd.DataFrame(
        {"k": [2, 1, 3], "d": pd.Series([1, 3, None], dtype=f"duration[{unit}][pyarrow]")}
    )
    data = CompletedFrameData(schema, rows)
    output, doubled = _run(
        data, SelectValues((("double", ValueBinary("mul", ValueSlot(0), ValueLiteral(2))),))
    )
    assert output.values[0].dtype == dtype
    assert doubled["value.0"].array.__arrow_array__().cast("int64").to_pylist() == [2, 6, None]
    output, reduced = _run(
        data,
        ReduceValues(
            (("mean", Statistic("mean", ValueSlot(0))), ("sum", Statistic("sum", ValueSlot(0)))), ()
        ),
    )
    assert output.values[0].dtype == dtype
    assert reduced["value.0"].array.__arrow_array__().cast("int64").to_pylist() == [2]
    assert reduced["value.1"].array.__arrow_array__().cast("int64").to_pylist() == [4]


def test_decimal_precision_and_current_row_mean() -> None:
    key = DataColumn("k", "k", ScalarDataType("int64"), False)
    column = DataColumn("v", "aov", DecimalDataType(20, 2), True, "CNY")
    schema = DataSchema((DataAxis("k", (key,), name="k"),), (column,), value_paths=(("aov",),))
    data = CompletedFrameData(
        schema, pd.DataFrame({"k": [1, 2], "v": [Decimal("100.00"), Decimal("10.00")]})
    )
    _, result = _run(data, ReduceValues((("mean", Statistic("mean", ValueSlot(0))),), ()))
    assert result.iloc[0, 0] == Decimal("55.00")


def test_all_na_sum_and_mean_stay_null_while_count_uses_rows() -> None:
    original = _data()
    rows = original.copy_rows()
    rows["a"] = pd.Series([None] * len(rows), dtype="int64[pyarrow]")
    data = CompletedFrameData(original.schema, rows)
    _, result = _run(
        data,
        ReduceValues(
            (
                ("sum", Statistic("sum", ValueSlot(0))),
                ("mean", Statistic("mean", ValueSlot(0))),
                ("rows", Statistic("row_count")),
            ),
            (),
        ),
    )
    assert result.iloc[0, :2].isna().all() and result.iloc[0, 2] == 4


def test_duration_mixed_units_and_overflow_are_checked() -> None:
    key = DataColumn("k", "k", ScalarDataType("int64"), False)
    left = DataColumn("a", "a", DurationDataType("ns"), True)
    right = DataColumn("b", "b", DurationDataType("us"), True)
    schema = DataSchema(
        (DataAxis("k", (key,), name="k"),), (left, right), value_paths=(("a",), ("b",))
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame(
            {
                "k": [1],
                "a": pd.Series([2**63 - 1], dtype="duration[ns][pyarrow]"),
                "b": pd.Series([1], dtype="duration[us][pyarrow]"),
            }
        ),
    )
    with pytest.raises(AnalysisError, match="exact Duration"):
        _run(data, SelectValues((("mixed", ValueBinary("add", ValueSlot(0), ValueSlot(1))),)))
    with pytest.raises(SemanticExpressionError, match="Duration"):
        _run(data, SelectValues((("overflow", ValueBinary("mul", ValueSlot(0), ValueLiteral(2))),)))


@pytest.mark.parametrize(
    "operation", [TopValues(0, ValueSlot(0), True), SelectValues((("only_b", ValueSlot(1)),))]
)
def test_full_fixed_validation_cannot_be_pruned(operation: FrameOperationKind) -> None:
    data = _data()
    data._rows["a"] = ["bad", "bad", "bad", "bad"]
    with pytest.raises(AnalysisError):
        _run(data, operation)


@pytest.mark.parametrize("corruption", ["duplicate_key", "missing_key", "missing_presence"])
def test_full_key_and_nonnullable_presence_checks_precede_top_zero(corruption: str) -> None:
    original = _data()
    axis = original.schema.axes[0]
    required_key = replace(axis.columns[0], nullable=False)
    presence = DataColumn("present", "left_present", ScalarDataType("boolean"), False)
    schema = replace(
        original.schema,
        axes=(replace(axis, columns=(required_key,)),),
        presence=(presence,),
        presence_paths=(("left_present",),),
    )
    rows = original.copy_rows().iloc[:3].copy()
    rows["present"] = [True, True, False]
    data = CompletedFrameData(schema, rows)
    if corruption == "duplicate_key":
        data._rows["key"] = ["same"] * 3
    elif corruption == "missing_key":
        data._rows.loc[0, "key"] = None
    else:
        data._rows["present"] = pd.Series([pd.NA, True, False], dtype="boolean[pyarrow]")
    with pytest.raises(AnalysisError):
        _run(data, TopValues(0, ValueSlot(0), True))


def test_strict_arithmetic_is_not_erased_by_later_select_or_top_zero() -> None:
    strict = SelectValues(
        (
            ("strict", ValueBinary("div", ValueSlot(0), ValueLiteral(0), "error")),
            ("kept", ValueSlot(1)),
        )
    )
    with pytest.raises(SemanticExpressionError, match="nonzero denominator"):
        _run(_data(), strict, SelectValues((("only", ValueSlot(1)),)))
    with pytest.raises(SemanticExpressionError, match="nonzero denominator"):
        _run(_data(), strict, TopValues(0, ValueSlot(1), True))


def test_presence_paths_are_directories_and_reduce_removes_presence() -> None:
    data = _data()
    presence = DataColumn("present", "outer.left_present", ScalarDataType("boolean"), False)
    schema = replace(data.schema, presence=(presence,), presence_paths=(("outer", "left_present"),))
    rows = data.copy_rows()
    rows["present"] = [False, True, True, False]
    data = CompletedFrameData(schema, rows)
    output, selected = _run(
        data,
        FilterValues(PresenceSlot(("outer", "left_present"))),
        SelectValues((("copy", ValueSlot(1)),)),
    )
    assert _keys(selected) == ["A", "B"]
    assert output.presence_paths == (("outer", "left_present"),)
    assert selected["present"].tolist() == [True, True]
    output, _ = _run(data, ReduceValues((("rows", Statistic("row_count")),), ()))
    assert output.presence == ()
    assert output.presence_paths == ()


def _calendar_directory(axis: CalendarDataAxis) -> OutputAttachment:
    assert axis.frozen is not None
    schema = calendar_directory_schema(axis)
    return OutputAttachment(
        axis.frozen.directory_name,
        schema,
        pd.DataFrame(
            {
                axis.columns[0].slot_id: pd.Series(
                    [0],
                    dtype="float64[pyarrow]"
                    if axis.columns[0].dtype == ScalarDataType("float64")
                    else "int64[pyarrow]",
                ),
                "nominal_label": pd.Series(["2026-01-01"], dtype="string[pyarrow]"),
                "ordinal": pd.Series([0], dtype="int64[pyarrow]"),
                **{
                    name: pd.Series(
                        [datetime(2026, month, 1, tzinfo=timezone.utc)],
                        dtype="timestamp[us, tz=UTC][pyarrow]",
                    )
                    for name, month in (
                        ("nominal_start", 1),
                        ("nominal_end", 2),
                        ("actual_start", 1),
                        ("actual_end", 2),
                    )
                },
            }
        ),
    )


def test_coordinate_attachment_tables_are_fully_validated_before_top_zero() -> None:
    key = DataColumn("k", "bucket", ScalarDataType("int64"), False)
    axis = CalendarDataAxis(
        "calendar",
        (key,),
        "UTC",
        name="calendar",
        frozen=FrozenCalendarData(BuiltinCalendarData("month", 1), "directory"),
    )
    value = DataColumn("v", "value", ScalarDataType("int64"), True)
    schema = DataSchema((axis,), (value,), value_paths=(("value",),))
    attachment = _calendar_directory(axis)
    data = CompletedFrameData(schema, pd.DataFrame({"k": [0], "v": [7]}), (attachment,))
    compiled = plan_frame(
        FrameOperation(
            (FixedFrameInput("calendar", schema),), SelectValues((("copy", ValueSlot(0)),))
        ),
        {},
    )

    def no_source(input_id: str, output: StageOutput) -> StageOutput:
        raise AssertionError("fixed calendar reached source adapter")

    result = run_frame_plan(
        compiled, source_prepared={}, read_fixed=lambda stage: data, adapt_source=no_source
    )
    assert tuple(a.name for a in result.frame_attachments) == ("directory",)
    data._attachments[0].rows["nominal_label"] = [99]
    with pytest.raises(AnalysisError):
        _run(data, TopValues(0, ValueSlot(0), True))


def _float_rows() -> tuple[DataSchema, pd.DataFrame]:
    key = DataColumn("key", "key", ScalarDataType("float64"), False)
    first = DataColumn("a", "a", ScalarDataType("float64"), True)
    unused = DataColumn("b", "b", ScalarDataType("float64"), True)
    schema = DataSchema(
        (DataAxis("key", (key,), name="key"),), (first, unused), value_paths=(("a",), ("b",))
    )
    return schema, pd.DataFrame({"key": [1.0], "a": [1.0], "b": [0.0]})


@pytest.mark.parametrize("invalid", [float("inf"), -float("inf")])
@pytest.mark.parametrize(
    "operation",
    [
        SelectValues((("only_a", ValueSlot(0)),)),
        TopValues(0, ValueSlot(0), True),
        FilterValues(ValueLiteral(False)),
    ],
)
def test_nonfinite_unused_fixed_column_rejects_before_any_consumer(
    invalid: float, operation: FrameOperationKind
) -> None:
    schema, rows = _float_rows()
    data = CompletedFrameData(schema, rows)
    data._rows["b"] = [invalid]
    with pytest.raises(AnalysisError, match="finite"):
        _run(data, operation)


@pytest.mark.parametrize("invalid", [float("inf"), -float("inf")])
@pytest.mark.parametrize("column", ["key", "b"])
def test_complete_validation_rejects_nonfinite_stage_cells(invalid: float, column: str) -> None:
    schema, rows = _float_rows()
    rows[column] = [invalid]
    with pytest.raises(AnalysisError, match="finite"):
        normalize_complete_frame(rows, schema.columns, schema.key_fields)
    for constructor in (StageOutput.from_frame, StageOutput.from_source_frame):
        transient = constructor(rows, schema.columns, schema.key_fields)
        with pytest.raises(AnalysisError, match="finite"):
            normalize_complete_frame(transient.table, transient.columns, transient.keys)


@pytest.mark.parametrize("invalid", [float("inf"), -float("inf")])
def test_fixed_typed_attachment_rejects_nonfinite_float_values(invalid: float) -> None:
    key = DataColumn("k", "bucket", ScalarDataType("float64"), False)
    axis = CalendarDataAxis(
        "calendar",
        (key,),
        "UTC",
        name="calendar",
        frozen=FrozenCalendarData(BuiltinCalendarData("month", 1), "directory"),
    )
    value = DataColumn("v", "value", ScalarDataType("int64"), True)
    schema = DataSchema((axis,), (value,), value_paths=(("value",),))
    attachment = _calendar_directory(axis)
    data = CompletedFrameData(schema, pd.DataFrame({"k": [0.0], "v": [7]}), (attachment,))
    data._attachments[0].rows["k"] = [invalid]
    with pytest.raises(AnalysisError, match="finite"):
        _run(data, TopValues(0, ValueSlot(0), True))


def test_complete_float_validation_preserves_na() -> None:
    schema, _ = _float_rows()
    rows = pd.DataFrame({"key": [1.0, 2.0], "a": [1.0, None], "b": [0.0, float("nan")]})
    data = CompletedFrameData(schema, rows)
    assert data.copy_rows().iloc[[1]][["a", "b"]].isna().all().all()
    for constructor in (StageOutput.from_frame, StageOutput.from_source_frame):
        output = constructor(rows, schema.columns, schema.key_fields)
        checked = normalize_complete_frame(output.table, output.columns, output.keys)
        assert checked.iloc[[1]][["a", "b"]].isna().all().all()
