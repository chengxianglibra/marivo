"""Ordinary Frame correspondence uses complete keys and independent manual oracles."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypedDict

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import FrameCompute, FrameInput, LocalCompute, SourceRead
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    MemoryFrameBacking,
)
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl.column import row_count
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.frame_sources import values as frame_values
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources, run_frame_plan
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory, terminal_runs


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-correspondence", timezone="UTC").local


def _memory_backing(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def _complete_schema(frame: AnalysisFrame) -> DataSchema:
    assert isinstance(frame._schema, DataSchema)
    return frame._schema


def _memory(
    local: LocalFrameServices, keys: list[str | None], values: list[int | None]
) -> AnalysisFrame:
    schema = DataSchema(
        (
            DataAxis(
                "axis.0",
                (DataColumn("key", "region", ScalarDataType("string"), True),),
                name="region",
            ),
        ),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True, "USD"),),
        value_paths=(("amount",),),
    )
    data = CompletedFrameData(schema, pd.DataFrame({"key": keys, "amount": values}, dtype=object))
    metadata = FrameResultMetadata(
        "manual",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def test_outer_compare_manual_order_presence_and_negative_baseline(
    local: LocalFrameServices,
) -> None:
    left = _memory(local, ["C", "A", None], [12, None, 3])
    right = _memory(local, ["B", None, "C"], [5, 0, -4])
    combined = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}
    ).compute()
    rows = _memory_backing(combined).data.copy_rows()
    assert rows["key"].tolist() == ["C", "A", pd.NA, "B"]
    assert combined.output_names == (
        "amount",
        "reference.baseline.amount",
        "reference.absolute.amount",
        "reference.relative.amount",
    )
    assert combined.presence_names == ("current.present", "reference.present")
    assert rows[combined._schema.presence[0].slot_id].tolist() == [True, True, True, False]
    assert rows[combined._schema.presence[1].slot_id].tolist() == [True, False, True, True]
    assert rows[combined._schema.values[2].slot_id].tolist()[:1] == [16]
    assert rows[combined._schema.values[3].slot_id].tolist()[:1] == [4.0]
    assert rows[combined._schema.values[3].slot_id].isna().tolist() == [False, True, True, True]


def test_self_compare_reads_shared_input_and_records_binary_operation(
    local: LocalFrameServices,
) -> None:
    frame = _memory(local, ["B", "A"], [4, 2])
    output = frame.compare(
        {"reference": frame}, on={"reference": ((frame.axes[0], frame.axes[0]),)}
    ).compute()
    assert len(_memory_backing(output).metadata.audit.evaluation.inputs) == 1
    assert _memory_backing(output).metadata.audit.evaluation.operations == ("compare",)
    assert output.to_pandas()[("value", "reference.absolute.amount")].tolist() == [0, 0]


def test_fixed_comparison_policies_reject_retired_arguments(local: LocalFrameServices) -> None:
    left, right = _memory(local, ["A"], [1]), _memory(local, ["A"], [0])
    on = ((left.axes[0], right.axes[0]),)
    for how in ("exact", "outer"):
        with pytest.raises(TypeError, match="unexpected keyword argument 'how'"):
            left.align(right, on=on, **{"how": how})
        with pytest.raises(TypeError, match="unexpected keyword argument 'how'"):
            left.compare({"reference": right}, on={"reference": on}, **{"how": how})
    for policy in ("null", "error"):
        with pytest.raises(TypeError, match="unexpected keyword argument 'zero_denominator'"):
            left.compare({"reference": right}, on={"reference": on}, **{"zero_denominator": policy})
    assert not hasattr(left["amount"], "divide")


def _data_frame(
    local: LocalFrameServices, schema: DataSchema, columns: dict[str, object]
) -> AnalysisFrame:
    data = CompletedFrameData(
        schema, pd.DataFrame(columns).loc[:, [c.slot_id for c in schema.columns]]
    )
    metadata = FrameResultMetadata(
        "manual",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def _values(frame: AnalysisFrame, name: str) -> list[object]:
    return frame.to_pandas()[("value", name)].tolist()


def test_composite_identity_and_field_domains_are_full_tuples(local: LocalFrameServices) -> None:
    identity = DataAxis(
        "id",
        (
            DataColumn("tenant", "tenant", ScalarDataType("string"), False),
            DataColumn("id", "id", ScalarDataType("int64"), False),
        ),
        kind="member",
        name="account",
    )
    field = DataAxis(
        "region", (DataColumn("region", "region", ScalarDataType("string"), True),), name="region"
    )
    value = DataColumn("v", "amount", ScalarDataType("int64"), False, "USD")
    left_schema = DataSchema((identity, field), (value,), value_paths=(("amount",),))
    right_schema = DataSchema((field, identity), (value,), value_paths=(("amount",),))
    left = _data_frame(
        local,
        left_schema,
        {"tenant": ["t2", "t1"], "id": [1, 1], "region": [None, "A"], "v": [12, 20]},
    )
    right = _data_frame(
        local,
        right_schema,
        {"tenant": ["t1", "t2"], "id": [1, 1], "region": [None, "A"], "v": [3, 5]},
    )
    on = ((left.axes[0], right.axes[1]), (left.axes[1], right.axes[0]))
    aligned = left.align(right, on=on).compute()
    assert len(aligned.to_pandas()) == 4
    output = left.compare({"reference": right}, on={"reference": on}).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert rows["tenant"].tolist() == ["t2", "t1", "t1", "t2"]
    assert rows["region"].isna().tolist() == [True, False, True, False]
    assert _values(output, "reference.absolute.amount") == [pd.NA] * 4
    assert output.presence_names == ("current.present", "reference.present")


def test_zero_axis_empty_and_singleton_domains(local: LocalFrameServices) -> None:
    schema = DataSchema(
        (), (DataColumn("v", "amount", ScalarDataType("int64"), True),), value_paths=(("amount",),)
    )
    empty = _data_frame(local, schema, {"v": pd.Series([], dtype="int64[pyarrow]")})
    singleton = _data_frame(local, schema, {"v": [8]})

    class AlignOptions(TypedDict, total=False):
        on: tuple[tuple[FrameAxis, FrameAxis], ...]

    class CompareOptions(TypedDict, total=False):
        on: dict[str, tuple[tuple[FrameAxis, FrameAxis], ...]]

    for explicit in (False, True):
        align_options: AlignOptions = {"on": ()} if explicit else {}
        assert len(empty.align(empty, **align_options).compute().to_pandas()) == 0
        compare_options: CompareOptions = {"on": {"reference": ()}} if explicit else {}
        result = singleton.compare({"reference": empty}, **compare_options).compute()
        assert _values(result, "amount") == [8]
        assert _values(result, "reference.absolute.amount") == [pd.NA]
        assert (
            singleton.compare({"reference": singleton}, **compare_options).compute()._schema.axes
            == ()
        )


def test_no_ordinary_inference_and_complete_mapping_before_values(
    local: LocalFrameServices,
) -> None:
    left, right = _memory(local, ["A"], [1]), _memory(local, ["A"], [2])
    for on in (None, ()):
        with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
            left.compare({"reference": right}, on=None if on is None else {"reference": on})
    scalar = left.reduce({"different": row_count()})
    with pytest.raises(AnalysisError, match="equal complete axis counts"):
        left.compare({"reference": scalar}, on={"reference": ()})
    other = right.select({"different": right["amount"]})
    with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
        left.compare({"reference": other}, on={"reference": ()})
    with pytest.raises(AnalysisError, match="equal nonempty complete value path sets"):
        left.compare({"reference": other}, on={"reference": ((left.axes[0], other.axes[0]),)})
    with pytest.raises(AnalysisError, match="exact Frame input"):
        left.align(right, on=((right.axes[0], left.axes[0]),))


def test_ownership_precedes_axis_mapping(local: LocalFrameServices, tmp_path: Path) -> None:
    other_local = DatasetRuntime.create(tmp_path / "other", "different", timezone="UTC").local
    left, right = _memory(local, ["A"], [1]), _memory(other_local, ["A"], [2])
    with pytest.raises(AnalysisError, match="same Store and Session"):
        left.compare({"reference": right}, on={"reference": ()})


def test_key_role_time_and_value_compatibility(local: LocalFrameServices) -> None:
    left = _memory(local, ["A"], [1])
    schema = _complete_schema(left)
    for change in ("role", "key_type", "at", "unit", "value_type"):
        axis, value = schema.axes[0], schema.values[0]
        assert isinstance(axis, DataAxis)
        key: object = "A"
        amount: object = 2
        if change == "role":
            axis = replace(axis, kind="member", columns=(replace(axis.columns[0], nullable=False),))
        elif change == "key_type":
            axis = replace(axis, columns=(replace(axis.columns[0], dtype=ScalarDataType("int64")),))
            key = 1
        elif change == "at":
            axis = replace(axis, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        elif change == "unit":
            value = replace(value, unit="CNY")
        else:
            value = replace(value, dtype=ScalarDataType("boolean"))
            amount = True
        right = _data_frame(
            local,
            DataSchema((axis,), (value,), value_paths=(("amount",),)),
            {"key": [key], "amount": [amount]},
        )
        with pytest.raises(AnalysisError):
            left.compare({"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)})
        if change in ("unit", "value_type"):
            assert left.align(
                right, on=((left.axes[0], right.axes[0]),)
            ).compute().output_names == (
                "left.amount",
                "right.amount",
            )


def test_full_path_pairing_right_reordering_and_nested_presence(local: LocalFrameServices) -> None:
    a, b = _memory(local, ["B", "A"], [8, 2]), _memory(local, ["C", "B"], [4, 2])
    outer = a.compare({"reference": b}, on={"reference": ((a.axes[0], b.axes[0]),)}).compute()
    other = outer.where(outer["current.present"]).compute()
    # Compare all four current values; right values deliberately use a different order.
    reordered_schema = replace(
        _complete_schema(other),
        values=tuple(reversed(_complete_schema(other).values)),
        value_paths=tuple(reversed(other._schema.value_paths)),
    )
    reordered = _data_frame(
        local,
        reordered_schema,
        {
            column: _memory_backing(other).data.copy_rows()[column]
            for column in [c.slot_id for c in reordered_schema.columns]
        },
    )
    nested = outer.compare(
        {"second": reordered}, on={"second": ((outer.axes[0], reordered.axes[0]),)}
    ).compute()
    assert nested.output_names[:4] == (
        "amount",
        "reference.baseline.amount",
        "reference.absolute.amount",
        "reference.relative.amount",
    )
    assert nested.presence_names == (
        "current.present",
        "second.present",
        "current.presence.current.present",
        "current.presence.reference.present",
        "second.presence.current.present",
        "second.presence.reference.present",
    )
    table = nested.to_pandas()
    assert table[("presence", "second.presence.current.present")].tolist() == [True, True, False]
    assert table[("presence", "second.presence.reference.present")].tolist() == [True, False, False]
    selected = nested.select({"copied": nested["amount"]}).compute()
    assert selected.output_names == ("copied",) and selected.presence_names == nested.presence_names
    assert len(selected._schema.values) == 1
    assert selected.reduce({"rows": row_count()}, by=selected.axes).compute().presence_names == ()
    scalar = selected.reduce({"rows": row_count()}).compute()
    assert scalar.presence_names == () and scalar.axes == ()
    assert _values(scalar, "rows") == [3]


@pytest.mark.parametrize("prune", ["select", "where", "top"])
@pytest.mark.parametrize("invalid", ["overflow"])
def test_required_compare_checks_survive_downstream_pruning(
    local: LocalFrameServices, prune: str, invalid: str
) -> None:
    left = _memory(local, ["A"], [2**63 - 1 if invalid == "overflow" else 1])
    right = _memory(
        local, ["B" if invalid == "domain" else "A"], [-1 if invalid == "overflow" else 0]
    )
    combined = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}
    )
    tail = (
        combined.select({"constant": 1})
        if prune == "select"
        else combined.where(combined["amount"] < 0)
        if prune == "where"
        else combined.top(0, by=combined["amount"])
    )
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        tail.compute()


def test_na_precedes_zero_error(local: LocalFrameServices) -> None:
    keys: list[str | None] = ["left_null", "right_null", "both_null"]
    left = _memory(local, keys, [None, 1, None])
    right = _memory(local, keys, [0, None, None])
    output = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}
    ).compute()
    assert _values(output, "reference.absolute.amount") == [pd.NA] * 3
    assert _values(output, "reference.relative.amount") == [pd.NA] * 3
    assert output.to_pandas()[("presence", "reference.present")].tolist() == [True] * 3


@pytest.mark.parametrize(
    "dtype,left_value,right_value,absolute,relative",
    [
        (DecimalDataType(8, 2), Decimal("12.50"), Decimal("-2.50"), Decimal("15.00"), 6.0),
        (ScalarDataType("float64"), 8.0, -2.0, 10.0, 5.0),
    ],
)
def test_numeric_carriers(
    local: LocalFrameServices,
    dtype: DataType,
    left_value: object,
    right_value: object,
    absolute: object,
    relative: float,
) -> None:
    schema = DataSchema(
        (), (DataColumn("v", "amount", dtype, True, "USD"),), value_paths=(("amount",),)
    )
    left, right = (
        _data_frame(local, schema, {"v": [left_value]}),
        _data_frame(local, schema, {"v": [right_value]}),
    )
    result = left.compare({"reference": right}).compute()
    assert _values(result, "reference.absolute.amount") == [absolute]
    assert _values(result, "reference.relative.amount") == [relative]
    assert result._schema.values[-1].unit is None


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_duration_uses_exact_original_ticks(
    local: LocalFrameServices, unit: Literal["s", "ms", "us", "ns"]
) -> None:
    dtype = DurationDataType(unit)
    schema = DataSchema((), (DataColumn("v", "elapsed", dtype, True),), value_paths=(("elapsed",),))

    def ticks(value: int) -> pd.Series:
        return pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([value], type=pa.int64()).cast(pa.duration(unit))
            )
        )

    left = _data_frame(local, schema, {"v": ticks(9)})
    right = _data_frame(local, schema, {"v": ticks(-3)})
    output = left.compare({"reference": right}).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert pa.array(rows[output._schema.values[2].slot_id]).cast(pa.int64()).to_pylist() == [12]
    assert _complete_schema(output).values[2].dtype == dtype
    assert _values(output, "reference.relative.elapsed") == [4.0]


def test_pending_and_mixed_inference_completes_identical_directories(
    local: LocalFrameServices,
) -> None:
    left = frame_values(local, columns={"amount": "USD"}, rows=(((), {"amount": 8}),))
    right = frame_values(local, columns={"amount": "USD"}, rows=(((), {"amount": 2}),))
    for baseline in (right, right.compute()):
        pending = left.compare({"reference": baseline})
        assert isinstance(pending._schema, PendingDataSchema)
        result = pending.compute()
        pending._schema.complete(_complete_schema(result))
        assert _values(result, "reference.absolute.amount") == [6]
        assert _values(result, "reference.relative.amount") == [3.0]


def test_fixed_combine_never_prepares_sources_or_catalog(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_source_preparation as evaluator

    left, right = _memory(local, ["A"], [8]), _memory(local, ["A"], [2])

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed data cannot prepare sources")

    monkeypatch.setattr(evaluator, "open_query_sources", forbidden)
    monkeypatch.setattr(evaluator, "prepare_source_free_populations", forbidden)
    monkeypatch.setattr("marivo.analysis.session.core.Session.catalog", property(forbidden))
    output = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}
    ).compute()
    assert _values(output, "reference.absolute.amount") == [6]


def test_actual_source_plus_fixed_uses_one_binary_plan(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator

    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": amount.sum()},
    )
    fixed = source.compute()
    original = run_frame_plan
    schedules: list[tuple[type[object], ...]] = []

    def run(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        schedules.append(tuple(type(s) for s in compiled.lowered.plan.stages))
        return original(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observe_read,
            observe_local=observe_local,
        )

    monkeypatch.setattr(evaluator, "run_frame_plan", run)
    result = source.compare({"reference": fixed}).compute()
    assert len(schedules) == 1 and FrameInput in schedules[0] and FrameCompute in schedules[0]
    assert _values(result, "amount") == [1176]
    assert _values(result, "reference.absolute.amount") == [0]
    assert len(terminal_runs(session)) == 0


def test_paired_axis_rejection_precedes_count_and_mapping(local: LocalFrameServices) -> None:
    from marivo.analysis.core.data_schema import (
        BuiltinCalendarData,
        CalendarDataAxis,
        FrozenCalendarData,
        PairedDataAxis,
    )
    from marivo.analysis.core.frame_structure import PendingDataColumn

    frame = _memory(local, ["A"], [1])
    key = DataColumn("key", "key", ScalarDataType("int64"), False)
    left = CalendarDataAxis(
        "left",
        (key,),
        "UTC",
        name="day",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "directory"),
    )
    right = replace(
        left,
        token="reference.baseline.",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "reference.baseline.directory"),
    )
    axis = PairedDataAxis("paired", (key,), left, right, "mapping", name="paired")
    schema = PendingDataSchema(
        (axis,),
        (PendingDataColumn("v", "amount", "numeric", True, "USD"),),
        (("amount",),),
    )
    paired = AnalysisFrame._bind(local, schema, _memory_backing(frame), "paired-input")
    scalar = frame.reduce({"rows": row_count()})
    with pytest.raises(AnalysisError, match="without paired axes"):
        paired.compare({"reference": scalar}, on={"reference": ()})


@pytest.mark.parametrize("variant", ["partial", "duplicate", "empty"])
def test_multiaxis_mapping_never_supplements_or_retries(
    local: LocalFrameServices, variant: str
) -> None:
    schema = DataSchema(
        tuple(
            DataAxis(
                f"a{i}",
                (DataColumn(f"k{i}", f"key{i}", ScalarDataType("int64"), False),),
                name=f"key{i}",
            )
            for i in range(2)
        ),
        (DataColumn("v", "amount", ScalarDataType("int64"), True),),
        value_paths=(("amount",),),
    )
    left, right = (
        _data_frame(local, schema, {"k0": [1], "k1": [2], "v": [3]}),
        _data_frame(local, schema, {"k0": [1], "k1": [2], "v": [4]}),
    )
    on = (
        ((left.axes[0], right.axes[0]),)
        if variant == "partial"
        else ((left.axes[0], right.axes[0]), (left.axes[0], right.axes[1]))
        if variant == "duplicate"
        else ()
    )
    with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
        left.align(right, on=on)


def test_nullable_field_union_and_null_is_not_a_string_sentinel(local: LocalFrameServices) -> None:
    left = _memory(local, ["null"], [8])
    schema = _complete_schema(left)
    axis = schema.axes[0]
    assert isinstance(axis, DataAxis)
    schema = replace(
        schema,
        axes=(replace(axis, columns=(replace(axis.columns[0], nullable=False),)),),
    )
    left = _data_frame(local, schema, {"key": ["null"], "amount": [8]})
    right = _memory(local, [None], [2])
    result = left.align(right, on=((left.axes[0], right.axes[0]),)).compute()
    assert result._schema.axes[0].columns[0].nullable is True
    assert _memory_backing(result).data.copy_rows()["key"].tolist() == ["null", pd.NA]
    assert result.to_pandas()[("presence", "left_present")].tolist() == [True, False]


@pytest.mark.parametrize("invalid", ["duration_overflow", "decimal_overflow", "duration_unit"])
def test_exact_carrier_failure_is_checked_before_projection(
    local: LocalFrameServices, invalid: str
) -> None:
    left_type: DurationDataType | DecimalDataType
    right_type: DurationDataType | DecimalDataType
    left_value: pd.Series | list[Decimal]
    right_value: pd.Series | list[Decimal]
    if invalid.startswith("duration"):
        left_type = DurationDataType("s")
        right_type = DurationDataType("ms") if invalid == "duration_unit" else left_type
        left_value = pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([2**63 - 1], type=pa.int64()).cast(pa.duration("s"))
            )
        )
        right_value = pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([-1], type=pa.int64()).cast(pa.duration(right_type.unit))
            )
        )
    else:
        left_type = right_type = DecimalDataType(38, 0)
        left_value, right_value = [Decimal("9" * 38)], [Decimal("-" + "9" * 38)]

    def frame(dtype: DataType, value: object) -> AnalysisFrame:
        return _data_frame(
            local,
            DataSchema((), (DataColumn("v", "v", dtype, True),), value_paths=(("v",),)),
            {"v": value},
        )

    left, right = frame(left_type, left_value), frame(right_type, right_value)
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        combined = left.compare({"reference": right})
        combined.select({"constant": 1}).compute()


def test_pending_binary_show_is_metadata_only(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    left = _memory(local, ["A"], [8])
    combined = left.compare({"reference": left}, on={"reference": ((left.axes[0], left.axes[0]),)})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata show cannot read")

    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    combined.show()
    text = capsys.readouterr().out
    assert "compare(baselines=" in text
    assert "zero_denominator" not in text and "how=" not in text
    with pytest.raises(AnalysisError):
        combined["current.present"].sum()


def test_different_name_align_and_explicit_attainment(local: LocalFrameServices) -> None:
    left, right = _memory(local, ["B", "A"], [8, 12]), _memory(local, ["A", "B"], [4, 2])
    actual, budget = (
        left.select({"actual": left["amount"]}),
        right.select({"budget": right["amount"]}),
    )
    aligned = actual.align(budget, on=((actual.axes[0], budget.axes[0]),))
    assert aligned.output_names == ("left.actual", "right.budget")
    ratio = aligned.select(
        {"attainment": aligned["left.actual"] / aligned["right.budget"]}
    ).compute()
    table = ratio.to_pandas()
    assert table[("value", "attainment")].tolist() == [4.0, 3.0]
    assert table[("coordinate", "axis.0")].tolist() == ["B", "A"]


def test_empty_values_do_not_qualify_for_compare(local: LocalFrameServices) -> None:
    schema = DataSchema(
        (DataAxis("axis", (DataColumn("k", "k", ScalarDataType("int64"), False),), name="k"),), ()
    )
    frame = _data_frame(local, schema, {"k": [1]})
    assert frame.align(frame, on=((frame.axes[0], frame.axes[0]),)).compute().output_names == ()
    with pytest.raises(AnalysisError, match="equal nonempty complete value path sets"):
        frame.compare({"reference": frame}, on={"reference": ((frame.axes[0], frame.axes[0]),)})


def test_source_binary_failure_still_closes_and_publishes_nothing(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": amount.sum()},
    )
    zero = source.compute().select({"amount": 0}).compute()
    # Preserve the source's unit rather than infer it from a numeric literal.
    zero_schema = replace(
        _complete_schema(zero),
        values=(replace(_complete_schema(zero).values[0], unit=source._schema.values[0].unit),),
    )
    zero = _data_frame(session._runtime.local, zero_schema, {zero_schema.values[0].slot_id: [0]})
    close = SourceSession.close
    closed: list[bool] = []

    def fail_close(self: SourceSession) -> None:
        close(self)
        closed.append(True)
        raise RuntimeError("injected binary source close")

    monkeypatch.setattr(SourceSession, "close", fail_close)
    with pytest.raises(MaterializationError, match="source cleanup"):
        source.compare({"reference": zero}).select({"ignored": 1}).compute()
    assert closed and terminal_runs(session) == ()


def test_empty_ordinary_domain_preserves_typed_schema(local: LocalFrameServices) -> None:
    empty, one = _memory(local, [], []), _memory(local, ["A"], [1])
    result = empty.align(empty, on=((empty.axes[0], empty.axes[0]),)).compute()
    assert len(result.to_pandas()) == 0 and len(result.axes) == 1
    result = empty.compare(
        {"reference": one}, on={"reference": ((empty.axes[0], one.axes[0]),)}
    ).compute()
    assert _memory_backing(result).data.copy_rows()["key"].tolist() == ["A"]
    assert _values(result, "reference.baseline.amount") == [1]


@pytest.mark.parametrize(
    "dtype,physical",
    [
        (DurationDataType("ns"), pa.duration("ns")),
        (TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC")),
    ],
)
def test_minimum_nanosecond_field_key_is_not_null(
    local: LocalFrameServices,
    dtype: DataType,
    physical: pa.DataType,
) -> None:
    schema = DataSchema(
        (DataAxis("axis.0", (DataColumn("k", "time", dtype, True),), name="time"),),
        (DataColumn("v", "amount", ScalarDataType("int64"), False),),
        value_paths=(("amount",),),
    )

    def frame(key: int | None, value: int) -> AnalysisFrame:
        keys = pd.Series(
            pd.arrays.ArrowExtensionArray(pa.array([key], type=pa.int64()).cast(physical))
        )
        return _data_frame(local, schema, {"k": keys, "v": [value]})

    left, right = frame(-(2**63), 10), frame(None, 3)
    expression = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}
    )
    output = expression.compute()
    rows = _memory_backing(output).data.copy_rows()
    assert pa.array(rows["k"]).cast(pa.int64()).to_pylist() == [-(2**63), None]
    assert rows[output._schema.presence[0].slot_id].tolist() == [True, False]
    assert rows[output._schema.presence[1].slot_id].tolist() == [False, True]
    assert rows[output._schema.values[2].slot_id].isna().tolist() == [True, True]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("kind", ["duration", "timestamp"])
def test_temporal_extreme_field_keys_self_align_exactly(
    local: LocalFrameServices, unit: Literal["s", "ms", "us", "ns"], kind: str
) -> None:
    dtype = DurationDataType(unit) if kind == "duration" else TimestampDataType(unit, "UTC")
    physical = pa.duration(unit) if kind == "duration" else pa.timestamp(unit, "UTC")
    schema = DataSchema(
        (DataAxis("axis.0", (DataColumn("k", "time", dtype, True),), name="time"),),
        (DataColumn("v", "amount", ScalarDataType("int64"), False),),
        value_paths=(("amount",),),
    )
    expected = [-(2**63), None, 2**63 - 1]
    keys = pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array(expected, type=pa.int64()).cast(physical))
    )
    frame = _data_frame(local, schema, {"k": keys, "v": [10, 3, 7]})
    output = frame.align(frame, on=((frame.axes[0], frame.axes[0]),)).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert pa.array(rows["k"]).cast(pa.int64()).to_pylist() == expected
    assert rows[output._schema.presence[0].slot_id].tolist() == [True, True, True]
    assert rows[output._schema.presence[1].slot_id].tolist() == [True, True, True]


def test_date_field_keys_use_exact_day_components(local: LocalFrameServices) -> None:
    schema = DataSchema(
        (DataAxis("axis.0", (DataColumn("k", "day", ScalarDataType("date"), True),), name="day"),),
        (DataColumn("v", "amount", ScalarDataType("int64"), False),),
        value_paths=(("amount",),),
    )
    expected = [-(2**31), None, 2**31 - 1]
    keys = pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array(expected, type=pa.int32()).cast(pa.date32()))
    )
    frame = _data_frame(local, schema, {"k": keys, "v": [10, 3, 7]})
    output = frame.align(frame, on=((frame.axes[0], frame.axes[0]),)).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert pa.array(rows["k"]).cast(pa.int32()).to_pylist() == expected


def _interval(
    local: LocalFrameServices,
    start_day: int,
    end_day: int,
    *,
    zone: str = "UTC",
    amount: int | None = 5,
) -> AnalysisFrame:
    from marivo._definition.types import IntervalCoordinate
    from marivo._temporal import anchor_from_datetime
    from marivo.analysis.core.frame_calendar import freeze_interval

    frozen = freeze_interval(
        IntervalCoordinate(
            "manual",
            anchor_from_datetime(datetime(2024, 1, start_day, tzinfo=timezone.utc)),
            anchor_from_datetime(datetime(2024, 1, end_day, tzinfo=timezone.utc)),
            zone,
        ),
        token="interval",
        name="interval",
        key=DataColumn("key", "interval", ScalarDataType("int64"), False),
    )
    schema = DataSchema(
        (frozen.axis,),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True),),
        value_paths=(("amount",),),
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame(
            {
                "key": pd.Series([0], dtype="int64[pyarrow]"),
                "amount": pd.Series([amount], dtype="int64[pyarrow]"),
            }
        ),
        (frozen.directory,),
    )
    metadata = FrameResultMetadata(
        "interval-manual",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def test_interval_exact_identity_outer_and_ordinal(local: LocalFrameServices) -> None:
    from marivo.analysis.dsl.correspondence import ordinal

    left, equal, other = (
        _interval(local, 1, 4),
        _interval(local, 1, 4, amount=None),
        _interval(local, 2, 5),
    )
    assert left.axes[0].role == "interval"
    same = left.compare({"reference": equal}).compute()
    assert _values(same, "reference.absolute.amount") == [pd.NA]
    assert same.to_pandas()[("presence", "reference.present")].tolist() == [True]
    assert len(left.align(other).compute().to_pandas()) == 2
    with pytest.raises(AnalysisError, match="actual baseline time row"):
        left.compare({"reference": other}).compute()
    paired = left.compare({"reference": other}, match={"reference": ordinal()}).compute()
    assert paired.axes[0].role == "interval"
    assert _values(paired, "reference.absolute.amount") == [0]
    with pytest.raises(AnalysisError):
        left.align(_interval(local, 1, 4, zone="Asia/Shanghai"), match=ordinal()).compute()
    different_zone = left.align(_interval(local, 1, 4, zone="Asia/Shanghai")).compute()
    assert len(different_zone.to_pandas()) == 2
    assert len(_memory_backing(different_zone).data.copy_attachments()[0].rows) == 2


def test_interval_declared_directory_survives_filter_reduce_and_matching(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.dsl.correspondence import ordinal

    left, right = _interval(local, 1, 4, amount=2), _interval(local, 2, 5, amount=3)
    merged = left.align(right).compute()
    selected = merged.where(merged["left.amount"] > 0)
    sparse = selected.select({"amount": selected["left.amount"]}).compute()
    assert len(sparse.to_pandas()) == 1
    assert len(_memory_backing(sparse).data.copy_attachments()[0].rows) == 2
    assert len(sparse.align(left).compute().to_pandas()) == 1
    with pytest.raises(AnalysisError, match="one declared interval"):
        sparse.align(left, match=ordinal()).compute()
    reduced = sparse.reduce({"n": row_count()}, by=(sparse.axes[0],)).compute()
    assert len(_memory_backing(reduced).data.copy_attachments()[0].rows) == 2
    assert reduced.reduce({"n": row_count()}).compute().axes == ()


def test_named_baselines_share_current_values_and_preserve_ordered_union(
    local: LocalFrameServices,
) -> None:
    current = _memory(local, ["C", "A", None], [12, None, 3])
    yoy = _memory(local, ["B", None, "C"], [5, 0, -4])
    mom = _memory(local, ["D", "C"], [7, 6])
    on = {
        name: ((current.axes[0], baseline.axes[0]),)
        for name, baseline in (("yoy", yoy), ("mom", mom))
    }
    combined = current.compare({"yoy": yoy, "mom": mom}, on=on).compute()
    assert _memory_backing(combined).data.copy_rows()["key"].tolist() == ["C", "A", pd.NA, "B", "D"]
    assert combined.output_names == (
        "amount",
        "yoy.baseline.amount",
        "yoy.absolute.amount",
        "yoy.relative.amount",
        "mom.baseline.amount",
        "mom.absolute.amount",
        "mom.relative.amount",
    )
    assert _values(combined, "amount") == [12, pd.NA, 3, pd.NA, pd.NA]
    assert _values(combined, "yoy.absolute.amount") == [16, pd.NA, 3, pd.NA, pd.NA]
    assert _values(combined, "mom.relative.amount") == [1.0, pd.NA, pd.NA, pd.NA, pd.NA]
    assert combined.presence_names == ("current.present", "yoy.present", "mom.present")
    table = combined.to_pandas()
    assert table[("presence", "mom.present")].tolist() == [True, False, False, False, True]
    independent = current.compare({"yoy": yoy}, on={"yoy": on["yoy"]}).compute()
    assert _values(combined, "yoy.relative.amount")[:4] == _values(
        independent, "yoy.relative.amount"
    )


def test_zero_second_baseline_returns_na_and_allows_downstream_selection(
    local: LocalFrameServices,
) -> None:
    current, good, zero = (
        _memory(local, ["A"], [4]),
        _memory(local, ["A"], [2]),
        _memory(local, ["A"], [0]),
    )
    compared = current.compare(
        {"good": good, "zero": zero},
        on={"good": ((current.axes[0], good.axes[0]),), "zero": ((current.axes[0], zero.axes[0]),)},
    )
    assert _values(compared.compute(), "zero.relative.amount") == [pd.NA]
    assert _values(compared.select({"current": compared["amount"]}).compute(), "current") == [4]
    assert compared.top(0, by=compared["good.absolute.amount"]).compute().to_pandas().empty


def test_comparison_path_collision_requires_explicit_rename(local: LocalFrameServices) -> None:
    current = _memory(local, ["A"], [4])
    first = current.compare(
        {"reference": current}, on={"reference": ((current.axes[0], current.axes[0]),)}
    ).compute()
    with pytest.raises(AnalysisError, match="Select and rename"):
        first.compare({"reference": first}, on={"reference": ((first.axes[0], first.axes[0]),)})
    with pytest.raises(AnalysisError, match="Select and rename"):
        first.compare(first, on=((first.axes[0], first.axes[0]),))


def test_comparison_select_retains_only_direct_bindings_and_exact_presence(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, baseline = _memory(local, ["A"], [4]), _memory(local, ["B"], [2])
    first = current.compare(
        {"reference": baseline}, on={"reference": ((current.axes[0], baseline.axes[0]),)}
    )
    selected = first.select(
        {"one": first["amount"], "two": first["amount"], "derived": first["amount"] + 1}
    ).compute()
    assert tuple(value.value_path for value in selected._schema.comparisons[0].values) == (
        ("one",),
        ("two",),
    )
    presence_only = selected.select({"literal": 1}).compute()
    assert presence_only._schema.comparisons[0].values == ()
    second = presence_only.compare(
        {"reference": presence_only},
        on={"reference": ((presence_only.axes[0], presence_only.axes[0]),)},
    ).compute()
    assert tuple((fact.namespace, fact.alias) for fact in second._schema.comparisons) == (
        ((), "reference"),
        (("current",), "reference"),
        (("reference",), "reference"),
    )
    constant = second.select({"n": 1})
    filtered = constant.where(constant["n"] > 0)
    assert (
        filtered.top(1, by=filtered["n"]).compute()._schema.comparisons
        == constant._schema.comparisons
    )
    reduced = constant.reduce({"n": row_count()}, by=constant.axes).compute()
    assert reduced._schema.comparisons == () and reduced.presence_names == ()

    assert isinstance(second._backing, MemoryFrameBacking)
    data = _memory_backing(second).data
    original = CompletedFrameData.copy_rows

    def corrupt(value: CompletedFrameData) -> pd.DataFrame:
        rows = original(value)
        if value is data:
            parent = value.schema.presence[
                value.schema.presence_paths.index(("current", "present"))
            ]
            rows.loc[0, parent.slot_id] = False
        return rows

    monkeypatch.setattr(CompletedFrameData, "copy_rows", corrupt)
    with pytest.raises(AnalysisError, match="inherited true presence"):
        constant.top(0, by=constant["n"]).compute()


def test_interval_comparison_alignment_transports_explicit_maps(local: LocalFrameServices) -> None:
    from marivo.analysis.core.data_schema import IntervalComparisonBinding
    from marivo.analysis.dsl.correspondence import ordinal

    current, baseline = _interval(local, 1, 4), _interval(local, 2, 5)
    first = current.compare({"reference": baseline}, match={"reference": ordinal()})
    selected = first.select({"delta": first["reference.absolute.amount"]})
    aligned = selected.align(selected).compute()
    assert tuple(fact.namespace for fact in aligned._schema.comparisons) == (("left",), ("right",))
    assert all(
        isinstance(fact.binding, IntervalComparisonBinding) for fact in aligned._schema.comparisons
    )
    assert isinstance(aligned._backing, MemoryFrameBacking)
    assert len(_memory_backing(aligned).data.copy_attachments()) == 5
    assert _values(aligned, "left.delta") == [0]


def test_stage_completed_schema_requires_exact_columns_and_keys(local: LocalFrameServices) -> None:
    frame = _memory(local, ["A"], [4])
    assert isinstance(frame._schema, DataSchema)
    schema = frame._schema
    assert isinstance(frame._backing, MemoryFrameBacking)
    rows = _memory_backing(frame).data.copy_rows()
    stage = StageOutput.from_frame(rows, schema.columns, schema.key_fields, frame_schema=schema)
    assert stage.frame_schema is schema
    with pytest.raises(MaterializationError, match="matching exact stage columns and keys"):
        StageOutput.from_frame(rows, schema.columns, (), frame_schema=schema)


def test_bare_compare_preserves_outer_values_presence_and_reference_order(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _memory(local, ["A", "B", "C", "E"], [12, None, 3, 8])
    baseline = _memory(local, ["A", "B", "C", "D"], [-4, 2, 0, 7])
    on = ((current.axes[0], baseline.axes[0]),)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("compare construction must not read rows or execute")

    before = tuple(terminal_runs(local))
    with monkeypatch.context() as construction:
        construction.setattr(CompletedFrameData, "copy_rows", forbidden)
        construction.setattr(
            "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
        )
        pending = current.compare(baseline, on=on)
        assert pending.output_names == (
            "amount",
            "reference.baseline.amount",
            "reference.absolute.amount",
            "reference.relative.amount",
        )
        assert pending.presence_names == ("current.present", "reference.present")
    assert tuple(terminal_runs(local)) == before
    result = pending.compute()
    table = result.to_pandas()
    assert table[("coordinate", "axis.0")].tolist() == ["A", "B", "C", "E", "D"]
    assert table[("value", "amount")].tolist() == [12, pd.NA, 3, 8, pd.NA]
    assert table[("value", "reference.baseline.amount")].tolist() == [-4, 2, 0, pd.NA, 7]
    assert table[("value", "reference.absolute.amount")].tolist() == [16, pd.NA, 3, pd.NA, pd.NA]
    assert table[("value", "reference.relative.amount")].tolist() == [
        4.0,
        pd.NA,
        pd.NA,
        pd.NA,
        pd.NA,
    ]
    assert table[("presence", "current.present")].tolist() == [True, True, True, True, False]
    assert table[("presence", "reference.present")].tolist() == [True, True, True, False, True]
    explicit = current.compare({"reference": baseline}, on={"reference": on}).compute()
    assert result._schema == explicit._schema
    pd.testing.assert_frame_equal(table, explicit.to_pandas())


def test_bare_compare_scalar_and_explicit_time_matching(local: LocalFrameServices) -> None:
    from marivo.analysis.dsl.correspondence import ordinal

    current = frame_sources.values(local, columns={"amount": "USD"}, rows=(((), {"amount": 12}),))
    baseline = frame_sources.values(local, columns={"amount": "USD"}, rows=(((), {"amount": -4}),))
    scalar = current.compare(baseline).compute()
    assert scalar.axes == ()
    scalar_table = scalar.to_pandas()
    assert scalar_table.iloc[0].tolist() == [12, -4, 16, 4.0, True, True]
    pd.testing.assert_frame_equal(
        scalar_table, current.compare({"reference": baseline}).compute().to_pandas()
    )
    left, right = _interval(local, 1, 4, amount=8), _interval(local, 2, 5, amount=2)
    timed = left.compare(right, match=ordinal()).compute()
    assert timed._schema.axes == left._schema.axes
    timed_table = timed.to_pandas()
    assert timed_table[("value", "reference.absolute.amount")].tolist() == [6]
    assert timed_table[("value", "reference.relative.amount")].tolist() == [3.0]
    explicit = left.compare({"reference": right}, match={"reference": ordinal()}).compute()
    pd.testing.assert_frame_equal(timed_table, explicit.to_pandas())


def test_compare_rejects_mixed_parameter_shapes_and_unnamed_multiple_inputs(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.dsl.correspondence import ordinal

    current, baseline = _memory(local, ["A"], [4]), _memory(local, ["A"], [2])
    on = ((current.axes[0], baseline.axes[0]),)
    mixed: tuple[Callable[[], AnalysisFrame], ...] = (
        lambda: current.compare(baseline, on={"reference": on}),
        lambda: current.compare(baseline, match={"reference": ordinal()}),
        lambda: current.compare({"actual": baseline}, on=on),
        lambda: current.compare({"actual": baseline}, match=ordinal()),
    )
    for call in mixed:
        with pytest.raises(AnalysisError) as error:
            call()
        assert "bare" in error.value.expected or "per-alias" in error.value.expected
        assert error.value.received in ("dict", "tuple", "CalendarCorrespondence")
        assert error.value.repair.help_target.canonical_id == "analysis_frame.compare"
        assert error.value.repair.action
    for invalid in ((), [], {}, (baseline,), [baseline], iter((baseline,)), "baseline"):
        with pytest.raises(AnalysisError) as error:
            current.compare(invalid)
        assert "one baseline Frame" in error.value.expected
        assert "distinct explicit aliases" in error.value.repair.action
    with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
        current.compare(baseline)


def test_bare_compare_preserves_ownership_names_and_unit_checks(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    current, baseline = _memory(local, ["A"], [4]), _memory(local, ["A"], [2])
    other_local = DatasetRuntime.create(tmp_path / "other", "other", timezone="UTC").local
    foreign = _memory(other_local, ["A"], [2])
    with pytest.raises(AnalysisError) as error:
        current.compare(foreign, on=((current.axes[0], foreign.axes[0]),))
    assert error.value.expected == "Frame inputs from the same Store and Session"
    renamed = baseline.select({"different": baseline["amount"]})
    with pytest.raises(AnalysisError, match="value paths"):
        current.compare(renamed, on=((current.axes[0], renamed.axes[0]),))
    unitless = baseline.select({"amount": baseline["amount"] / baseline["amount"]})
    with pytest.raises(AnalysisError, match="unit"):
        current.compare(unitless, on=((current.axes[0], unitless.axes[0]),))
