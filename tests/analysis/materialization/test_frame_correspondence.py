"""Ordinary Frame correspondence uses complete keys and independent manual oracles."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_plan import FrameCompute, FrameInput
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
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl.column import row_count
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_sources import values as frame_values
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-correspondence", report_timezone="UTC").local


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
    combined = left.compare(right, on=((left.axes[0], right.axes[0]),), how="outer").compute()
    rows = combined._backing.data.copy_rows()
    assert rows["key"].tolist() == ["C", "A", pd.NA, "B"]
    assert combined.output_names == (
        "left.amount",
        "right.amount",
        "absolute.amount",
        "relative.amount",
    )
    assert combined.presence_names == ("left_present", "right_present")
    assert rows[combined._schema.presence[0].slot_id].tolist() == [True, True, True, False]
    assert rows[combined._schema.presence[1].slot_id].tolist() == [True, False, True, True]
    assert rows[combined._schema.values[2].slot_id].tolist()[:1] == [16]
    assert rows[combined._schema.values[3].slot_id].tolist()[:1] == [4.0]
    assert rows[combined._schema.values[3].slot_id].isna().tolist() == [False, True, True, True]


def test_self_compare_reads_shared_input_and_records_binary_operation(
    local: LocalFrameServices,
) -> None:
    frame = _memory(local, ["B", "A"], [4, 2])
    output = frame.compare(frame, on=((frame.axes[0], frame.axes[0]),)).compute()
    assert len(output._backing.metadata.audit.evaluation.inputs) == 1
    assert output._backing.metadata.audit.evaluation.operations == ("compare",)
    assert output.to_pandas()[("value", "absolute.amount")].tolist() == [0, 0]


def test_compare_check_cannot_be_hidden_by_select(local: LocalFrameServices) -> None:
    left = _memory(local, ["A"], [1])
    right = _memory(local, ["A"], [0])
    combined = left.compare(right, on=((left.axes[0], right.axes[0]),), zero_denominator="error")
    with pytest.raises(MaterializationError, match="nonzero denominator") as failure:
        combined.select({"constant": 1}).compute()
    error = failure.value
    cause = error.__cause__
    assert error.stage == "numeric_value" and error.run_ref is None
    assert isinstance(cause, SemanticExpressionError)
    assert error.expected == cause.expected == "a nonzero denominator"
    assert error.received == cause.received == "metric.divide(zero_denominator='error')"
    assert error.repair is not None and cause.repair is not None
    assert error.repair.action == cause.repair.action


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
    with pytest.raises(AnalysisError, match="actual complete row-key domains"):
        left.align(right, on=on).compute()
    output = left.compare(right, on=on, how="outer").compute()
    rows = output._backing.data.copy_rows()
    assert rows["tenant"].tolist() == ["t2", "t1", "t1", "t2"]
    assert rows["region"].isna().tolist() == [True, False, True, False]
    assert _values(output, "absolute.amount") == [pd.NA] * 4
    assert output.presence_names == ("left_present", "right_present")


@pytest.mark.parametrize("explicit", [False, True])
def test_zero_axis_empty_and_singleton_domains(local: LocalFrameServices, explicit: bool) -> None:
    schema = DataSchema(
        (), (DataColumn("v", "amount", ScalarDataType("int64"), True),), value_paths=(("amount",),)
    )
    empty = _data_frame(local, schema, {"v": pd.Series([], dtype="int64[pyarrow]")})
    singleton = _data_frame(local, schema, {"v": [8]})
    kwargs = {"on": ()} if explicit else {}
    assert len(empty.align(empty, **kwargs).compute().to_pandas()) == 0
    with pytest.raises(AnalysisError, match="actual complete row-key domains"):
        singleton.compare(empty, **kwargs).compute()
    result = singleton.compare(empty, how="outer", **kwargs).compute()
    assert _values(result, "left.amount") == [8]
    assert _values(result, "absolute.amount") == [pd.NA]
    assert singleton.compare(singleton, **kwargs).compute()._schema.axes == ()


def test_no_ordinary_inference_and_complete_mapping_before_values(
    local: LocalFrameServices,
) -> None:
    left, right = _memory(local, ["A"], [1]), _memory(local, ["A"], [2])
    for on in (None, ()):
        with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
            left.compare(right, on=on)
    scalar = left.reduce({"different": row_count()})
    with pytest.raises(AnalysisError, match="equal complete axis counts"):
        left.compare(scalar, on=())
    other = right.select({"different": right["amount"]})
    with pytest.raises(AnalysisError, match="complete explicit axis bijection"):
        left.compare(other, on=())
    with pytest.raises(AnalysisError, match="equal nonempty complete value path sets"):
        left.compare(other, on=((left.axes[0], other.axes[0]),))
    with pytest.raises(AnalysisError, match="exact Frame input"):
        left.align(right, on=((right.axes[0], left.axes[0]),))


def test_ownership_precedes_axis_mapping(local: LocalFrameServices, tmp_path: Path) -> None:
    other_local = DatasetRuntime.create(
        tmp_path / "other", "different", report_timezone="UTC"
    ).local
    left, right = _memory(local, ["A"], [1]), _memory(other_local, ["A"], [2])
    with pytest.raises(AnalysisError, match="same Store and Session"):
        left.compare(right, on=())


@pytest.mark.parametrize("change", ["role", "key_type", "at", "unit", "value_type"])
def test_key_role_time_and_value_compatibility(local: LocalFrameServices, change: str) -> None:
    left = _memory(local, ["A"], [1])
    axis, value = left._schema.axes[0], left._schema.values[0]
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
        left.compare(right, on=((left.axes[0], right.axes[0]),))
    if change in ("unit", "value_type"):
        assert left.align(right, on=((left.axes[0], right.axes[0]),)).compute().output_names == (
            "left.amount",
            "right.amount",
        )


def test_full_path_pairing_right_reordering_and_nested_presence(local: LocalFrameServices) -> None:
    a, b = _memory(local, ["B", "A"], [8, 2]), _memory(local, ["C", "B"], [4, 2])
    outer = a.compare(b, on=((a.axes[0], b.axes[0]),), how="outer").compute()
    other = outer.where(outer["left_present"]).compute()
    # Compare all four current values; right values deliberately use a different order.
    reordered_schema = replace(
        other._schema,
        values=tuple(reversed(other._schema.values)),
        value_paths=tuple(reversed(other._schema.value_paths)),
    )
    reordered = _data_frame(
        local,
        reordered_schema,
        {
            column: other._backing.data.copy_rows()[column]
            for column in [c.slot_id for c in reordered_schema.columns]
        },
    )
    nested = outer.compare(
        reordered, on=((outer.axes[0], reordered.axes[0]),), how="outer"
    ).compute()
    assert nested.output_names[:4] == (
        "left.left.amount",
        "left.right.amount",
        "left.absolute.amount",
        "left.relative.amount",
    )
    assert nested.presence_names == (
        "left_present",
        "right_present",
        "left.left_present",
        "left.right_present",
        "right.left_present",
        "right.right_present",
    )
    table = nested.to_pandas()
    assert table[("presence", "right.left_present")].tolist() == [True, True, False]
    assert table[("presence", "right.right_present")].tolist() == [True, False, False]
    selected = nested.select({"copied": nested["left.left.amount"]}).compute()
    assert selected.output_names == ("copied",) and selected.presence_names == nested.presence_names
    assert len(selected._schema.values) == 1
    assert selected.reduce({"rows": row_count()}, by=selected.axes).compute().presence_names == ()
    scalar = selected.reduce({"rows": row_count()}).compute()
    assert scalar.presence_names == () and scalar.axes == ()
    assert _values(scalar, "rows") == [3]


@pytest.mark.parametrize("prune", ["select", "where", "top"])
@pytest.mark.parametrize("invalid", ["zero", "overflow", "domain"])
def test_required_compare_checks_survive_downstream_pruning(
    local: LocalFrameServices, prune: str, invalid: str
) -> None:
    left = _memory(local, ["A"], [2**63 - 1 if invalid == "overflow" else 1])
    right = _memory(
        local, ["B" if invalid == "domain" else "A"], [-1 if invalid == "overflow" else 0]
    )
    combined = left.compare(right, on=((left.axes[0], right.axes[0]),), zero_denominator="error")
    tail = (
        combined.select({"constant": 1})
        if prune == "select"
        else combined.where(combined["left.amount"] < 0)
        if prune == "where"
        else combined.top(0, by=combined["left.amount"])
    )
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        tail.compute()


@pytest.mark.parametrize("left_value,right_value", [(None, 0), (1, None), (None, None)])
def test_na_precedes_zero_error(
    local: LocalFrameServices, left_value: int | None, right_value: int | None
) -> None:
    left, right = _memory(local, ["A"], [left_value]), _memory(local, ["A"], [right_value])
    output = left.compare(
        right, on=((left.axes[0], right.axes[0]),), zero_denominator="error"
    ).compute()
    assert _values(output, "absolute.amount") == [pd.NA]
    assert _values(output, "relative.amount") == [pd.NA]
    assert output.to_pandas()[("presence", "right_present")].tolist() == [True]


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
    result = left.compare(right).compute()
    assert _values(result, "absolute.amount") == [absolute]
    assert _values(result, "relative.amount") == [relative]
    assert result._schema.values[-1].unit is None


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_duration_uses_exact_original_ticks(local: LocalFrameServices, unit: str) -> None:
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
    output = left.compare(right).compute()
    rows = output._backing.data.copy_rows()
    assert pa.array(rows[output._schema.values[2].slot_id]).cast(pa.int64()).to_pylist() == [12]
    assert output._schema.values[2].dtype == dtype
    assert _values(output, "relative.elapsed") == [4.0]


def test_pending_and_mixed_inference_completes_identical_directories(
    local: LocalFrameServices,
) -> None:
    left = frame_values(local, columns={"amount": "USD"}, rows=(((), {"amount": 8}),))
    right = frame_values(local, columns={"amount": "USD"}, rows=(((), {"amount": 2}),))
    for baseline in (right, right.compute()):
        pending = left.compare(baseline)
        assert isinstance(pending._schema, PendingDataSchema)
        result = pending.compute()
        pending._schema.complete(result._schema)
        assert _values(result, "absolute.amount") == [6]
        assert _values(result, "relative.amount") == [3.0]


def test_fixed_combine_never_prepares_sources_or_catalog(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator

    left, right = _memory(local, ["A"], [8]), _memory(local, ["A"], [2])

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed data cannot prepare sources")

    monkeypatch.setattr(evaluator, "open_query_sources", forbidden)
    monkeypatch.setattr(evaluator, "prepare_context", forbidden)
    monkeypatch.setattr(evaluator, "prepare_source_free_populations", forbidden)
    monkeypatch.setattr("marivo.analysis.session.core.Session.catalog", property(forbidden))
    output = left.compare(right, on=((left.axes[0], right.axes[0]),)).compute()
    assert _values(output, "absolute.amount") == [6]


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
    original = evaluator.run_frame_plan
    schedules: list[tuple[type[object], ...]] = []

    def run(compiled: CompiledFramePlan, **kwargs: object) -> StageOutput:
        schedules.append(tuple(type(s) for s in compiled.lowered.plan.stages))
        return original(compiled, **kwargs)

    monkeypatch.setattr(evaluator, "run_frame_plan", run)
    result = source.compare(fixed).compute()
    assert len(schedules) == 1 and FrameInput in schedules[0] and FrameCompute in schedules[0]
    assert _values(result, "left.amount") == [1176]
    assert _values(result, "absolute.amount") == [0]
    assert len(session.runs().items) == 0


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
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "left.directory"),
    )
    right = replace(
        left,
        token="right",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "right.directory"),
    )
    axis = PairedDataAxis("paired", (key,), left, right, "mapping", name="paired")
    schema = PendingDataSchema(
        (axis,),
        (PendingDataColumn("v", "amount", "numeric", True, "USD"),),
        (("amount",),),
    )
    paired = AnalysisFrame._bind(local, schema, frame._backing, "paired-input")
    scalar = frame.reduce({"rows": row_count()})
    with pytest.raises(AnalysisError, match="without paired axes"):
        paired.compare(scalar, on=())


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
    schema = replace(
        left._schema,
        axes=(
            replace(
                left._schema.axes[0],
                columns=(replace(left._schema.axes[0].columns[0], nullable=False),),
            ),
        ),
    )
    left = _data_frame(local, schema, {"key": ["null"], "amount": [8]})
    right = _memory(local, [None], [2])
    result = left.align(right, on=((left.axes[0], right.axes[0]),), how="outer").compute()
    assert result._schema.axes[0].columns[0].nullable is True
    assert result._backing.data.copy_rows()["key"].tolist() == ["null", pd.NA]
    assert result.to_pandas()[("presence", "left_present")].tolist() == [True, False]


@pytest.mark.parametrize("invalid", ["duration_overflow", "decimal_overflow", "duration_unit"])
def test_exact_carrier_failure_is_checked_before_projection(
    local: LocalFrameServices, invalid: str
) -> None:
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
        left_type = right_type = DecimalDataType(4, 2)
        left_value, right_value = [Decimal("99.99")], [Decimal("-99.99")]

    def frame(dtype: DataType, value: object) -> AnalysisFrame:
        return _data_frame(
            local,
            DataSchema((), (DataColumn("v", "v", dtype, True),), value_paths=(("v",),)),
            {"v": value},
        )

    left, right = frame(left_type, left_value), frame(right_type, right_value)
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        combined = left.compare(right)
        combined.select({"constant": 1}).compute()


def test_pending_binary_show_is_metadata_only(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    left = _memory(local, ["A"], [8])
    combined = left.compare(
        left, on=((left.axes[0], left.axes[0]),), how="outer", zero_denominator="error"
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata show cannot read")

    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    combined.show()
    text = capsys.readouterr().out
    assert "compare" in text and "outer" in text and "zero_denominator='error'" in text
    with pytest.raises(AnalysisError):
        combined["left_present"].sum()


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
        frame.compare(frame, on=((frame.axes[0], frame.axes[0]),))


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
        zero._schema, values=(replace(zero._schema.values[0], unit=source._schema.values[0].unit),)
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
        source.compare(zero, zero_denominator="error").select({"ignored": 1}).compute()
    assert closed and session.runs().items == ()


def test_empty_ordinary_domain_preserves_typed_schema(local: LocalFrameServices) -> None:
    empty, one = _memory(local, [], []), _memory(local, ["A"], [1])
    result = empty.align(empty, on=((empty.axes[0], empty.axes[0]),)).compute()
    assert len(result.to_pandas()) == 0 and len(result.axes) == 1
    result = empty.compare(one, on=((empty.axes[0], one.axes[0]),), how="outer").compute()
    assert result._backing.data.copy_rows()["key"].tolist() == ["A"]
    assert _values(result, "right.amount") == [1]


@pytest.mark.parametrize(
    "dtype,physical",
    [
        (DurationDataType("ns"), pa.duration("ns")),
        (TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC")),
    ],
)
@pytest.mark.parametrize("how", ["exact", "outer"])
def test_minimum_nanosecond_field_key_is_not_null(
    local: LocalFrameServices, dtype: DataType, physical: pa.DataType, how: str
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
    expression = left.compare(right, on=((left.axes[0], right.axes[0]),), how=how)
    if how == "exact":
        with pytest.raises(AnalysisError, match="actual complete row-key domains"):
            expression.compute()
    else:
        output = expression.compute()
        rows = output._backing.data.copy_rows()
        assert pa.array(rows["k"]).cast(pa.int64()).to_pylist() == [-(2**63), None]
        assert rows[output._schema.presence[0].slot_id].tolist() == [True, False]
        assert rows[output._schema.presence[1].slot_id].tolist() == [False, True]
        assert rows[output._schema.values[2].slot_id].isna().tolist() == [True, True]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("kind", ["duration", "timestamp"])
def test_temporal_extreme_field_keys_self_align_exactly(
    local: LocalFrameServices, unit: str, kind: str
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
    rows = output._backing.data.copy_rows()
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
    rows = output._backing.data.copy_rows()
    assert pa.array(rows["k"]).cast(pa.int32()).to_pylist() == expected
