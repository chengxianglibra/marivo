"""Fixed temporal expressions use Arrow validity and exact physical tick oracles."""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from marivo._definition.query import SelectValues, ValueSlot, ValueUnary
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.query_values import _temporal_comparison_values, expression_series
from marivo.analysis.session.core import Session
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import terminal_runs

TimeUnit = Literal["s", "ms", "us", "ns"]


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session._from_runtime(
        DatasetRuntime.create(tmp_path, "temporal-expressions", timezone="UTC")
    )


def _series(ticks: list[int | None], physical: pa.DataType) -> pd.Series:
    integer = pa.int32() if physical == pa.date32() else pa.int64()
    return pd.Series(pd.arrays.ArrowExtensionArray(pa.array(ticks, type=integer).cast(physical)))


def _frame(
    session: Session,
    dtype: DataType,
    physical: pa.DataType,
    ticks: list[int | None],
    *,
    nullable: bool = True,
) -> AnalysisFrame:
    schema = DataSchema(
        (DataAxis("time", (DataColumn("k", "key", dtype, nullable),), name="time"),),
        (DataColumn("v", "value", dtype, nullable),),
        value_paths=(("value",),),
    )
    return AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                schema, pd.DataFrame({"k": _series(ticks, physical), "v": _series(ticks, physical)})
            ),
            FrameResultMetadata(
                "exact-temporal-input",
                FrameAudit(
                    (), EvaluationRecord(datetime(2026, 10, 20, tzinfo=timezone.utc), (), ())
                ),
            ),
        ),
    )


TEMPORAL_TYPES: tuple[tuple[DataType, pa.DataType], ...] = (
    *((DurationDataType(unit), pa.duration(unit)) for unit in ("s", "ms", "us", "ns")),
    *(
        (TimestampDataType(unit, zone), pa.timestamp(unit, zone))
        for unit in ("s", "ms", "us", "ns")
        for zone in (None, "UTC", "Asia/Shanghai")
    ),
    (ScalarDataType("date"), pa.date32()),
)

# Full-range comparisons run on one high-resolution member of each timezone-aware
# family; all-unit extrema, literal conversion, and cold-storage coverage remain separate.
COMPARISON_TYPES: tuple[tuple[DataType, pa.DataType], ...] = (
    (DurationDataType("ns"), pa.duration("ns")),
    (TimestampDataType("ns", None), pa.timestamp("ns")),
    (TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC")),
    (TimestampDataType("ns", "Asia/Shanghai"), pa.timestamp("ns", "Asia/Shanghai")),
    (ScalarDataType("date"), pa.date32()),
)


@pytest.mark.parametrize("dtype,physical", COMPARISON_TYPES)
@pytest.mark.parametrize("nullable", [False, True])
def test_temporal_comparisons_keep_full_range_and_null_masks(
    session: Session, dtype: DataType, physical: pa.DataType, nullable: bool
) -> None:
    minimum, maximum = (-(2**31), 2**31 - 1) if physical == pa.date32() else (-(2**63), 2**63 - 1)
    ticks: list[int | None] = [minimum, 0, maximum, *([None] if nullable else [])]
    memory = _frame(session, dtype, physical, ticks, nullable=nullable)
    stored = memory.persist()
    reopened = session.persisted_frame(stored.artifact.id)
    for frame in (memory, stored, reopened):
        before = terminal_runs(session)
        value, coordinate = frame["value"], frame.coord(frame.axes[0])
        result = (
            frame.select(
                {
                    "eq": value == coordinate,
                    "ne": value != coordinate,
                    "lt": value < coordinate,
                    "le": value <= coordinate,
                    "gt": value > coordinate,
                    "ge": value >= coordinate,
                    "value_null": value.is_null(),
                    "coordinate_null": coordinate.is_null(),
                }
            )
            .compute()
            .to_pandas()
        )
        for name, expected in (
            ("eq", True),
            ("ne", False),
            ("lt", False),
            ("le", True),
            ("gt", False),
            ("ge", True),
        ):
            array = pa.array(result[name])
            assert array.to_pylist() == [
                expected,
                expected,
                expected,
                *([None] if nullable else []),
            ]
        for name in ("value_null", "coordinate_null"):
            assert result[name].tolist() == [
                False,
                False,
                False,
                *([True] if nullable else []),
            ]
        filtered = frame.where(value == coordinate).compute().to_pandas()
        integer = pa.int32() if physical == pa.date32() else pa.int64()
        assert pa.array(filtered["time"]).cast(integer).to_pylist() == [
            minimum,
            0,
            maximum,
        ]
        assert terminal_runs(session) == before


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_duration_negation_checks_original_unit_overflow_and_preserves_nulls(
    session: Session, unit: TimeUnit
) -> None:
    physical = pa.duration(unit)
    frame = _frame(session, DurationDataType(unit), physical, [-(2**63), 0, None])
    for current in (frame, session.persisted_frame(frame.persist().artifact.id)):
        before = terminal_runs(session)
        with pytest.raises(MaterializationError, match="Duration tick overflow"):
            current.select({"negative": -current["value"]}).compute()
        assert terminal_runs(session) == before
    safe = _frame(session, DurationDataType(unit), physical, [-(2**63) + 1, 0, None])
    negated = safe.select({"negative": -safe["value"]}).compute().to_pandas()
    assert pa.array(negated["negative"]).type == physical
    assert pa.array(negated["negative"]).cast(pa.int64()).to_pylist() == [
        2**63 - 1,
        0,
        None,
    ]
    # The internal absolute-value expression shares the same exact unary kernel.
    assert isinstance(frame._schema, DataSchema)
    program = FrameProgram(SelectValues(()), frame._schema, frame._schema)
    with pytest.raises(SemanticExpressionError, match="Duration tick overflow"):
        expression_series(
            ValueUnary("abs", ValueSlot(0)),
            pd.DataFrame({"v": _series([-(2**63), None], physical)}),
            program,
        )
    absolute = expression_series(
        ValueUnary("abs", ValueSlot(0)),
        pd.DataFrame({"v": _series([-(2**63) + 1, None], physical)}),
        program,
    )
    assert pa.array(absolute).cast(pa.int64()).to_pylist() == [2**63 - 1, None]


def test_timestamp_comparison_scales_units_without_int64_common_unit_cast(session: Session) -> None:
    schema = DataSchema(
        (DataAxis("row", (DataColumn("k", "row", ScalarDataType("int64"), False),), name="row"),),
        (
            DataColumn("s", "seconds", TimestampDataType("s", "UTC"), True),
            DataColumn("ns", "nanoseconds", TimestampDataType("ns", "Asia/Shanghai"), True),
        ),
        value_paths=(("seconds",), ("nanoseconds",)),
    )
    frame = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                pd.DataFrame(
                    {
                        "k": [1, 2, 3, 4],
                        "s": _series([-(2**63), 0, 2**63 - 1, None], pa.timestamp("s", "UTC")),
                        "ns": _series(
                            [-(2**63), 0, 2**63 - 1, None], pa.timestamp("ns", "Asia/Shanghai")
                        ),
                    }
                ),
            ),
            FrameResultMetadata(
                "mixed-ticks",
                FrameAudit(
                    (), EvaluationRecord(datetime(2026, 10, 20, tzinfo=timezone.utc), (), ())
                ),
            ),
        ),
    )
    for current in (frame, session.persisted_frame(frame.persist().artifact.id)):
        left, right = current["seconds"], current["nanoseconds"]
        result = (
            current.select(
                {
                    "eq": left == right,
                    "lt": left < right,
                    "le": left <= right,
                    "gt": left > right,
                    "ge": left >= right,
                    "ne": left != right,
                }
            )
            .compute()
            .to_pandas()
        )
        for name, expected in {
            "eq": [False, True, False, None],
            "ne": [True, False, True, None],
            "lt": [True, False, False, None],
            "le": [True, True, False, None],
            "gt": [False, False, True, None],
            "ge": [False, True, True, None],
        }.items():
            assert pa.array(result[name]).to_pylist() == expected


def test_timestamp_comparison_retains_pandas_literal_nanoseconds(session: Session) -> None:
    frame = _frame(
        session, TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC"), [0, 1, 2, None]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        result = (
            frame.select({"one": frame["value"] == pd.Timestamp(1, unit="ns", tz="UTC")})
            .compute()
            .to_pandas()
        )
    assert pa.array(result["one"]).to_pylist() == [False, True, False, None]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("kind", ["timestamp", "duration"])
def test_temporal_comparison_keeps_native_pandas_literal_extrema(
    session: Session, unit: TimeUnit, kind: str
) -> None:
    dtype = TimestampDataType(unit, "UTC") if kind == "timestamp" else DurationDataType(unit)
    physical = pa.timestamp(unit, "UTC") if kind == "timestamp" else pa.duration(unit)
    literal = (
        pd.Timestamp(2**63 - 1, unit=unit, tz="UTC")
        if kind == "timestamp"
        else pd.Timedelta(np.timedelta64(2**63 - 1, unit))
    )
    expected = (2**63 - 1) * {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}[unit]
    assert _temporal_comparison_values(pd.Series([literal], dtype=object)).tolist() == [expected]
    if kind == "timestamp" and unit != "ns":
        # Ibis refuses these out-of-Python-range literals during inference;
        # the kernel still preserves their native pandas representation.
        return
    frame = _frame(session, dtype, physical, [-(2**63), 0, 2**63 - 1, None])
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        output = frame.select({"maximum": frame["value"] == literal}).compute().to_pandas()
    assert pa.array(output["maximum"]).to_pylist() == [False, False, True, None]


@pytest.mark.parametrize("all_null", [False, True])
def test_timestamp_comparison_preserves_timezone_awareness_distinction(
    session: Session, all_null: bool
) -> None:
    schema = DataSchema(
        (DataAxis("row", (DataColumn("k", "row", ScalarDataType("int64"), False),), name="row"),),
        (
            DataColumn("naive", "naive", TimestampDataType("s", None), True),
            DataColumn("aware", "aware", TimestampDataType("ns", "UTC"), True),
        ),
        value_paths=(("naive",), ("aware",)),
    )
    ticks: list[int | None] = [-(2**63), 0, 2**63 - 1, None]
    rows = pd.DataFrame(
        {
            "k": [1, 2, 3, 4],
            "naive": _series([None] * 4 if all_null else ticks, pa.timestamp("s")),
            "aware": _series(ticks, pa.timestamp("ns", "UTC")),
        }
    )
    frame = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(schema, rows),
            FrameResultMetadata(
                "awareness",
                FrameAudit(
                    (), EvaluationRecord(datetime(2026, 10, 20, tzinfo=timezone.utc), (), ())
                ),
            ),
        ),
    )
    for current in (frame, session.persisted_frame(frame.persist().artifact.id)):
        naive, aware = current["naive"], current["aware"]
        output = current.select({"eq": naive == aware, "ne": naive != aware}).compute().to_pandas()
        assert pa.array(output["eq"]).to_pylist() == (
            [None] * 4 if all_null else [False, False, False, None]
        )
        assert pa.array(output["ne"]).to_pylist() == (
            [None] * 4 if all_null else [True, True, True, None]
        )
        for expression in (naive < aware, naive <= aware, naive > aware, naive >= aware):
            if all_null:
                assert (
                    pa.array(
                        current.select({"ordered": expression}).compute().to_pandas()["ordered"]
                    ).to_pylist()
                    == [None] * 4
                )
            else:
                with pytest.raises(
                    MaterializationError, match="matching timestamp timezone awareness"
                ):
                    current.select({"ordered": expression}).compute()


@pytest.mark.release
def test_temporal_cold_continuation_uses_fixed_data_without_publication(session: Session) -> None:
    artifacts = [
        _frame(
            session, dtype, physical, [-(2**31) if physical == pa.date32() else -(2**63), 0, None]
        )
        .persist()
        .artifact.id
        for dtype, physical in TEMPORAL_TYPES
    ]
    script = """
import json, os, sys
from unittest.mock import patch
import pyarrow as pa
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ['MARIVO_PROJECT_ROOT'] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError('cold temporal continuation attempted source reconstruction')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden), patch.object(frame_source_preparation, 'open_query_sources', forbidden):
    session = mv.session.resume(sys.argv[2])
    before = terminal_runs(session)
    for artifact in json.loads(sys.argv[3]):
        frame = session.persisted_frame(artifact)
        value = frame['value']
        output = frame.select({'same': value == value, 'null': value.is_null()}).compute().to_pandas()
        assert pa.array(output['same']).to_pylist() == [True, True, None]
        assert output['null'].tolist() == [False, False, True]
        assert len(frame.where(value == value).compute().to_pandas()) == 2
    assert terminal_runs(session) == before
print('cold-temporal-expressions-exact')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(session.project_root),
            session._runtime.local.session_ref,
            json.dumps(artifacts),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-temporal-expressions-exact" in result.stdout
