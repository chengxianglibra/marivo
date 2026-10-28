"""Stage uniqueness uses exact Arrow keys without temporal factorization loss."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.data_validation import normalize_stage_frame
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    MemoryFrameBacking,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from tests.shared_fixtures import terminal_runs

_UNITS: tuple[Literal["s", "ms", "us", "ns"], ...] = ("s", "ms", "us", "ns")
_TEMPORAL_TYPES = tuple(
    dtype
    for unit in _UNITS
    for dtype in (
        TimestampDataType(unit, None),
        TimestampDataType(unit, "UTC"),
        DurationDataType(unit),
    )
)
# Error-path checks below are carrier-generic; the exact-tick matrix above owns the
# per-unit carrier evidence, so these tests keep one representative of each family.
_TEMPORAL_CARRIERS = (
    TimestampDataType("ns", None),
    TimestampDataType("ns", "UTC"),
    DurationDataType("ns"),
)
_MINIMUM = -(2**63)
_TICKS = (_MINIMUM, None, _MINIMUM + 1, 0)


def _temporal(
    dtype: TimestampDataType | DurationDataType, ticks: tuple[int | None, ...]
) -> pd.Series:
    physical = (
        pa.timestamp(dtype.unit, dtype.timezone)
        if isinstance(dtype, TimestampDataType)
        else pa.duration(dtype.unit)
    )
    return pd.Series(pd.arrays.ArrowExtensionArray(pa.array(ticks, type=pa.int64()).cast(physical)))


@pytest.mark.parametrize("dtype", _TEMPORAL_TYPES)
def test_exact_temporal_complete_keys_preserve_validity_ticks_and_sequence(
    dtype: TimestampDataType | DurationDataType,
) -> None:
    for shape in ("single", "composite", "joint"):
        key = DataColumn("key", "key", dtype, True)
        axes: tuple[DataAxis, ...] = (DataAxis("time", (key,), name="time"),)
        ticks = _TICKS if shape == "single" else _TICKS * 2
        rows = pd.DataFrame({"key": _temporal(dtype, ticks)})
        if shape != "single":
            part = DataColumn("part", "part", ScalarDataType("string"), False)
            axes = (
                (DataAxis("time", (key, part), name="time"),)
                if shape == "composite"
                else (*axes, DataAxis("part", (part,), name="part"))
            )
            rows["part"] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array(["x"] * 4 + ["y"] * 4, type=pa.string()))
            )
        rows["value"] = pd.Series(
            ([2**63 - 1, None, _MINIMUM, 0] * 2)[: len(ticks)], dtype="int64[pyarrow]"
        )
        rows["present"] = pd.Series(([True, False] * 4)[: len(ticks)], dtype="bool[pyarrow]")
        schema = DataSchema(
            axes,
            (DataColumn("value", "value", ScalarDataType("int64"), True),),
            (DataColumn("present", "present", ScalarDataType("boolean"), False),),
            value_paths=(("value",),),
            presence_paths=(("present",),),
        )
        for actual in (
            StageOutput.from_frame(rows, schema.columns, schema.key_fields).table,
            CompletedFrameData(schema, rows).copy_rows(),
        ):
            assert tuple(actual.columns) == tuple(rows.columns)
            for name in rows.columns:
                assert pa.array(actual[name]).equals(pa.array(rows[name]))
            keys = pa.array(actual["key"])
            assert keys.is_valid().to_pylist() == [tick is not None for tick in ticks]
            assert keys.cast(pa.int64()).to_pylist() == list(ticks)


@pytest.mark.parametrize("dtype", _TEMPORAL_CARRIERS)
def test_true_temporal_complete_key_duplicates_retain_typed_stage_error(
    dtype: TimestampDataType | DurationDataType,
) -> None:
    for duplicate in (_MINIMUM, None, _MINIMUM + 1, 0):
        for composite in (False, True):
            columns: tuple[DataColumn, ...] = (DataColumn("key", "key", dtype, True),)
            rows = pd.DataFrame({"key": _temporal(dtype, (duplicate, duplicate))})
            if composite:
                columns += (DataColumn("part", "part", ScalarDataType("string"), False),)
                rows["part"] = pd.Series(["same", "same"], dtype="string[pyarrow]")
            with pytest.raises(MaterializationError, match="complete unique stage key") as caught:
                StageOutput.from_frame(rows, columns, tuple(column.slot_id for column in columns))
            assert caught.value.stage == "stage_output"


@pytest.mark.parametrize("dtype", _TEMPORAL_CARRIERS)
def test_nonnullable_temporal_null_remains_a_typed_materialization_error(
    dtype: TimestampDataType | DurationDataType,
) -> None:
    column = DataColumn("key", "key", dtype, False)
    rows = pd.DataFrame({"key": _temporal(dtype, (_MINIMUM, None, _MINIMUM + 1))})
    with pytest.raises(MaterializationError) as caught:
        normalize_stage_frame(rows, (column,), ("key",))
    assert caught.value.stage == "stage_output"
    assert "NA" in str(caught.value) or "null" in str(caught.value)


@pytest.mark.parametrize(
    "series",
    (
        pd.Series(np.array([0.0, 1.0])),
        pd.Series(["2024-01-01", "invalid"], dtype=object),
        pd.Series([object(), object()], dtype=object),
    ),
)
def test_bad_temporal_carriers_retain_typed_materialization_error(series: pd.Series) -> None:
    column = DataColumn("key", "key", TimestampDataType("ns", None), True)
    with pytest.raises(MaterializationError) as caught:
        normalize_stage_frame(pd.DataFrame({"key": series}), (column,), ("key",))
    assert caught.value.stage == "stage_output"


@pytest.mark.parametrize(
    "dtype,physical,values",
    (
        (ScalarDataType("boolean"), pa.bool_(), [False, None, True]),
        (ScalarDataType("string"), pa.string(), ["", None, "next"]),
        (ScalarDataType("int64"), pa.int64(), [_MINIMUM, None, _MINIMUM + 1]),
        (ScalarDataType("float64"), pa.float64(), [0.0, None, 1.0]),
        (DecimalDataType(18, 2), pa.decimal128(18, 2), [Decimal("0.00"), None, Decimal("0.01")]),
        (ScalarDataType("date"), pa.date32(), [-(2**31), None, -(2**31) + 1]),
    ),
)
def test_nontemporal_key_semantics_remain_exact(
    dtype: DataType,
    physical: pa.DataType,
    values: list[object],
) -> None:
    array = (
        pa.array(values, type=pa.int32()).cast(physical)
        if pa.types.is_date32(physical)
        else pa.array(values, type=physical)
    )
    rows = pd.DataFrame({"key": pd.Series(pd.arrays.ArrowExtensionArray(array))})
    column = DataColumn("key", "key", dtype, True)
    normalized = normalize_stage_frame(rows, (column,), ("key",))
    assert pa.array(normalized["key"]).equals(array)
    for index in range(len(rows)):
        repeated = pd.DataFrame(
            {
                "key": pd.Series(
                    pd.arrays.ArrowExtensionArray(pa.concat_arrays((array, array.slice(index, 1))))
                )
            }
        )
        with pytest.raises(MaterializationError, match="complete unique stage key"):
            normalize_stage_frame(repeated, (column,), ("key",))


@pytest.mark.parametrize("values", ([0.0, -0.0], [float("nan"), float("nan")]))
@pytest.mark.parametrize("temporal", (False, True))
def test_stage_float_duplicate_semantics_include_signed_zero_and_valid_nan(
    values: list[float],
    temporal: bool,
) -> None:
    rows = pd.DataFrame(
        {"key": pd.Series(pd.arrays.ArrowExtensionArray(pa.array(values, type=pa.float64())))}
    )
    columns: tuple[DataColumn, ...] = (DataColumn("key", "key", ScalarDataType("float64"), True),)
    if temporal:
        dtype = TimestampDataType("ns", "UTC")
        rows["time"] = _temporal(dtype, (_MINIMUM, _MINIMUM))
        columns += (DataColumn("time", "time", dtype, True),)
    with pytest.raises(MaterializationError, match="complete unique stage key"):
        normalize_stage_frame(rows, columns, tuple(column.slot_id for column in columns))


def test_stage_valid_nan_and_null_remain_distinct_complete_keys() -> None:
    rows = pd.DataFrame(
        {"key": pd.Series(pd.arrays.ArrowExtensionArray(pa.array([float("nan"), None])))}
    )
    column = DataColumn("key", "key", ScalarDataType("float64"), True)
    result = normalize_stage_frame(rows, (column,), ("key",))
    assert pa.array(result["key"]).is_valid().to_pylist() == [True, False]
    schema = DataSchema((DataAxis("float", (column,), name="float"),), ())
    with pytest.raises(CoreRuleError, match="finite typed coordinate values"):
        CompletedFrameData(schema, rows)


def test_exact_temporal_keys_persist_reopen_and_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("exact-stage-keys", timezone="UTC")
    dtype = TimestampDataType("ns", "UTC")
    schema = DataSchema(
        (DataAxis("time", (DataColumn("key", "key", dtype, True),), name="time"),),
        (DataColumn("value", "value", ScalarDataType("int64"), False),),
        value_paths=(("value",),),
    )
    rows = pd.DataFrame(
        {"key": _temporal(dtype, _TICKS), "value": pd.Series([1, 2, 3, 4], dtype="int64[pyarrow]")}
    )
    metadata = FrameResultMetadata(
        "exact-temporal-result",
        FrameAudit(
            (), EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",))
        ),
    )
    memory = mv.AnalysisFrame._from_memory(
        session._runtime.local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata)
    )
    saved = memory.persist()
    reopened = session.persisted_frame(saved.artifact_id)
    loaded = reopened.compute()
    continued = reopened.select({"next": reopened["value"] + 1}).compute()
    for frame in (memory, loaded, continued):
        assert isinstance(frame._backing, MemoryFrameBacking)
        keys = pa.array(frame._backing.data.copy_rows()["key"])
        assert keys.type == pa.timestamp("ns", "UTC")
        assert keys.cast(pa.int64()).to_pylist() == list(_TICKS)
    assert continued.to_pandas()["next"].tolist() == [2, 3, 4, 5]
    assert isinstance(loaded._backing, MemoryFrameBacking)
    assert replace(loaded._backing.metadata, lineage=None) == metadata
    assert len(terminal_runs(session)) == 1


def test_zero_axis_stage_and_column_order_rules_are_unchanged() -> None:
    rows = pd.DataFrame(index=pd.RangeIndex(3))
    assert len(normalize_stage_frame(rows, (), ())) == 3
    column = DataColumn("value", "value", ScalarDataType("int64"), True)
    values = pd.DataFrame({"value": pd.Series([1, 1], dtype="int64[pyarrow]")})
    assert len(normalize_stage_frame(values, (column,), ())) == 2
    with pytest.raises(MaterializationError, match="stage column order"):
        normalize_stage_frame(values.rename(columns={"value": "other"}), (column,), ())
