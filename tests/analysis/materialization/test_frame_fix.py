"""Single-member restriction preserves cells, keys and missing-row semantics."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
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
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "fix", timezone="UTC").local


def memory(
    local: LocalFrameServices,
    axes: tuple[DataAxis, ...],
    rows: pd.DataFrame,
    *,
    values: bool = True,
) -> AnalysisFrame:
    schema = DataSchema(
        axes,
        (DataColumn("v", "value", ScalarDataType("int64"), True, "USD"),) if values else (),
        value_paths=(("value",),) if values else (),
    )
    if values:
        rows = rows.assign(v=pd.array(rows["v"], dtype="Int64"))
    data = CompletedFrameData(schema, rows)
    metadata = FrameResultMetadata(
        "manual",
        EvaluationRecord(datetime(2026, 10, 26, tzinfo=timezone.utc), (), ()),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def axis(name: str, dtype: DataType | None = None, *, nullable: bool = True) -> DataAxis:
    return DataAxis(
        name, (DataColumn(name, name, dtype or ScalarDataType("string"), nullable),), name=name
    )


def test_fix_preserves_order_cells_and_complete_other_coordinates(
    local: LocalFrameServices,
) -> None:
    frame = memory(
        local,
        (axis("region"), axis("cluster"), axis("item", ScalarDataType("int64"))),
        pd.DataFrame(
            {
                "region": ["west", "east", "west", "east"],
                "cluster": ["bi1", "bi2", "bi2", "bi1"],
                "item": [2, 1, 2, 1],
                "v": [120, 100, 90, None],
            }
        ),
    )
    fixed = frame.fix(frame.axis("cluster"), "bi1").compute()
    assert [a.name for a in fixed.axes] == ["region", "item"]
    assert fixed._schema.values == frame._schema.values
    assert fixed.schema.fixed_axes[0].axis.name == "cluster"
    assert fixed.schema.fixed_axes[0].key == ("bi1",)
    assert "cluster" not in fixed.schema.slots
    rows = fixed.to_pandas()
    assert rows["region"].tolist() == ["west", "east"]
    assert rows["item"].tolist() == [2, 1]
    assert rows["value"].iloc[0] == 120
    assert pd.isna(rows["value"].iloc[1])
    with pytest.raises(AnalysisError):
        fixed.fix(frame.axis("region"), "west")
    a = fixed.fix(fixed.axis("region"), "west")
    b = frame.fix(frame.axis("region"), "west")
    b = b.fix(b.axis("cluster"), "bi1")
    pd.testing.assert_frame_equal(a.compute().to_pandas(), b.compute().to_pandas())


def test_fix_null_composite_and_absent_scalar(local: LocalFrameServices) -> None:
    composite = DataAxis(
        "customer",
        (
            DataColumn("tenant", "tenant", ScalarDataType("string"), True),
            DataColumn("id", "id", ScalarDataType("int64"), False),
        ),
        name="customer",
    )
    frame = memory(
        local,
        (composite,),
        pd.DataFrame({"tenant": [None, "a", "a"], "id": [1, 1, 2], "v": [12, 14, 16]}),
    )
    fixed = frame.fix(frame.axes[0], (None, 1)).compute()
    assert fixed.axes == ()
    assert fixed.to_pandas()["value"].tolist() == [12]
    missing = frame.fix(frame.axes[0], ("absent", 1)).compute()
    assert missing.axes == () and len(missing.to_pandas()) == 0
    compared = fixed.compare({"missing": mv.baseline(missing)}).compute().to_pandas()
    assert compared["current.present"].tolist() == [True]
    assert compared["missing.present"].tolist() == [False]
    assert pd.isna(compared["missing.absolute.value"].iloc[0])
    for bad in ("a", ("a",), ("a", None), ("a", True), ("a", 1, 2)):
        with pytest.raises(AnalysisError):
            frame.fix(frame.axes[0], bad)


@pytest.mark.parametrize(
    ("dtype", "key", "bad"),
    [
        (ScalarDataType("int64"), 2, True),
        (ScalarDataType("float64"), 2.0, 2),
        (ScalarDataType("boolean"), True, 1),
        (ScalarDataType("string"), "2", 2),
        (DecimalDataType(12, 3), Decimal("2.125"), 2.125),
        (ScalarDataType("date"), date(2026, 10, 1), "2026-10-01"),
        (
            TimestampDataType("us", "UTC"),
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 1),
        ),
        (DurationDataType("us"), timedelta(seconds=2), 2),
    ],
)
def test_fix_uses_typed_complete_keys(
    local: LocalFrameServices, dtype: DataType, key: object, bad: object
) -> None:
    frame = memory(local, (axis("key", dtype),), pd.DataFrame({"key": [key], "v": [7]}))
    assert frame.fix(frame.axes[0], key).compute().to_pandas()["value"].tolist() == [7]
    with pytest.raises(AnalysisError):
        frame.fix(frame.axes[0], bad)


@pytest.mark.parametrize(
    "dtype",
    [TimestampDataType("ns", None), TimestampDataType("ns", "UTC"), DurationDataType("ns")],
)
def test_fix_preserves_nanosecond_keys_and_rejects_lossy_narrowing(
    local: LocalFrameServices, dtype: TimestampDataType | DurationDataType
) -> None:
    keys = (
        pd.to_datetime([-123, 0, 123], unit="ns", utc=dtype.timezone == "UTC")
        if isinstance(dtype, TimestampDataType)
        else pd.to_timedelta([-123, 0, 123], unit="ns")
    )
    frame = memory(local, (axis("key", dtype),), pd.DataFrame({"key": keys, "v": [7, 11, 19]}))
    for key, expected in zip(keys, (7, 11, 19), strict=True):
        fixed = frame.fix(frame.axes[0], key).compute()
        assert fixed.schema.fixed_axes[0].key[0].value == key.value
        rows = fixed.to_pandas()
        assert rows["value"].tolist() == [expected]

    coarse_dtype = (
        TimestampDataType("us", dtype.timezone)
        if isinstance(dtype, TimestampDataType)
        else DurationDataType("us")
    )
    coarse = memory(
        local, (axis("key", coarse_dtype),), pd.DataFrame({"key": [keys[1]], "v": [11]})
    )
    for key in (keys[0], keys[2]):
        with pytest.raises(AnalysisError, match="lossless complete key"):
            coarse.fix(coarse.axes[0], key)


@pytest.mark.parametrize("key", ["found", "missing", None])
def test_fix_zero_column_row_count_and_empty_input(
    local: LocalFrameServices, key: str | None
) -> None:
    frame = memory(local, (axis("key"),), pd.DataFrame({"key": ["found", None]}), values=False)
    fixed = frame.fix(frame.axes[0], key).compute()
    assert fixed.to_pandas().shape == (0 if key == "missing" else 1, 0)
    empty = frame.fix(frame.axes[0], "missing")
    assert empty.compute().to_pandas().shape == (0, 0)


def test_fix_after_compare_preserves_presence_and_value_bindings(local: LocalFrameServices) -> None:
    left = memory(
        local, (axis("cluster"),), pd.DataFrame({"cluster": ["bi1", "bi2"], "v": [120, None]})
    )
    right = memory(local, (axis("cluster"),), pd.DataFrame({"cluster": ["bi1"], "v": [100]}))
    compared = left.compare({"reference": mv.baseline(right, on=((left.axes[0], right.axes[0]),))})
    fixed = compared.fix(compared.axes[0], "bi2").compute()
    rows = fixed.to_pandas()
    assert rows["current.present"].tolist() == [True]
    assert rows["reference.present"].tolist() == [False]
    assert fixed._schema.comparisons == compared.compute()._schema.comparisons
    assert fixed.presence_names == compared.presence_names


def test_fix_cold_roundtrip_and_source_free_continuation(local: LocalFrameServices) -> None:
    import subprocess
    import sys

    frame = memory(
        local,
        (axis("region"), axis("cluster")),
        pd.DataFrame({"region": ["east", "west"], "cluster": ["bi1", "bi1"], "v": [120, 80]}),
    )
    saved = frame.fix(frame.axis("cluster"), "bi1").persist()
    script = """
import os, sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.analysis.session.core import Session
os.environ['MARIVO_PROJECT_ROOT'] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError('fix attempted a source or catalog read')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    assert saved._derivation is None
    assert saved.lineage.input_id == saved.schema.input_id
    assert saved.schema.fixed_axes[0].axis.name == "cluster"
    assert saved.schema.fixed_axes[0].key == ("bi1",)
    fixed = saved.fix(saved.axis('region'), 'east').compute()
    assert fixed.axes == ()
    assert fixed.to_pandas()['value'].tolist() == [120]
    empty = saved.fix(saved.axis('region'), 'missing').persist()
    assert len(empty.to_pandas()) == 0
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact_id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fix_cannot_hide_strict_input_errors(local: LocalFrameServices) -> None:
    from marivo.semantic.errors import SemanticExpressionError

    frame = memory(
        local, (axis("cluster"),), pd.DataFrame({"cluster": ["bi1", "bi2"], "v": [1, 2**63 - 1]})
    )
    selected = frame.select({"value": frame["value"] + 1})
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        selected.fix(selected.axes[0], "bi1").compute()


def test_fix_is_not_commutative_with_partition_share(local: LocalFrameServices) -> None:
    frame = memory(
        local, (axis("cluster"),), pd.DataFrame({"cluster": ["bi1", "bi2"], "v": [1, 3]})
    )
    shared = frame.select({"share": frame["value"].share(along=frame.axes)})
    assert shared.fix(shared.axes[0], "bi1").compute().to_pandas()["share"].tolist() == [0.25]
    fixed = frame.fix(frame.axes[0], "bi1")
    normalized = fixed.select({"share": fixed["value"] / fixed.over(fixed["value"].sum())})
    assert normalized.compute().to_pandas()["share"].tolist() == [1.0]


def test_fix_complete_identity_and_same_session_axis_ownership(local: LocalFrameServices) -> None:
    identity = DataAxis(
        "member",
        (
            DataColumn("tenant", "tenant", ScalarDataType("string"), False),
            DataColumn("id", "id", ScalarDataType("int64"), False),
        ),
        kind="member",
        name="member",
    )
    frame = memory(
        local, (identity,), pd.DataFrame({"tenant": ["a", "b"], "id": [1, 1], "v": [12, 24]})
    )
    assert frame.fix(frame.axes[0], ("b", 1)).compute().to_pandas()["value"].tolist() == [24]
    with pytest.raises(AnalysisError):
        frame.fix(frame.axes[0], (None, 1))
    other = memory(local, (identity,), pd.DataFrame({"tenant": ["a"], "id": [1], "v": [12]}))
    with pytest.raises(AnalysisError):
        other.fix(frame.axes[0], ("a", 1))


def test_fix_preserves_default_and_explicit_compare_axis_pairing(local: LocalFrameServices) -> None:
    frame = memory(
        local,
        (axis("region"), axis("cluster")),
        pd.DataFrame({"region": ["east", "east"], "cluster": ["bi1", "bi2"], "v": [120, 100]}),
    )
    a = frame.fix(frame.axis("cluster"), "bi1")
    b = frame.fix(frame.axis("cluster"), "bi2")
    inferred = a.compare({"reference": mv.baseline(b)}).compute()
    result = a.compare({"reference": mv.baseline(b, on=((a.axes[0], b.axes[0]),))}).compute()
    assert inferred.to_pandas()["reference.absolute.value"].tolist() == [20]
    assert result.to_pandas()["reference.absolute.value"].tolist() == [20]
