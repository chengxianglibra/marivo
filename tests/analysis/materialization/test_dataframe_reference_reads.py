"""Exact native table reads preserve fixed values, ownership and failures."""

from __future__ import annotations

import errno
import json
import math
import struct
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.core.result_reference import DataFrameReference
from marivo.analysis.materialization.result_files import ResultStorageError


def snapshot_path(root: Path, reference: DataFrameReference) -> Path:
    return root / ".marivo/server/dataframes" / (reference.result_id + ".parquet")


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        (pa.null(), [None, None, None]),
        (pa.bool_(), [True, False, None]),
        (pa.string(), ["", "east", None]),
        (pa.large_string(), ["south", "海", None]),
        (pa.int8(), [-128, 127, None]),
        (pa.int16(), [-32768, 32767, None]),
        (pa.int32(), [-(2**31), 2**31 - 1, None]),
        (pa.int64(), [-(2**63), 2**63 - 1, None]),
        (pa.uint8(), [0, 255, None]),
        (pa.uint16(), [0, 65535, None]),
        (pa.uint32(), [0, 2**32 - 1, None]),
        (pa.uint64(), [0, 2**64 - 1, None]),
        (pa.float32(), [1.5, -2.25, None]),
        (pa.float64(), [1.5, -2.25, None]),
        (pa.decimal128(25, 3), [Decimal("9007199254740993.100"), Decimal("-0.010"), None]),
        (pa.decimal256(60, 4), [Decimal("123456789012345678901234567890.1234"), None, None]),
        (pa.date32(), [date(2026, 11, 9), date(1970, 1, 1), None]),
    ],
)
def test_saved_scalar_values_and_physical_types(
    tmp_path: Path, dtype: pa.DataType, values: list[object]
) -> None:
    source = pd.DataFrame({"value": pd.arrays.ArrowExtensionArray(pa.array(values, type=dtype))})
    source.attrs["discarded"] = "input-only"
    reference = mv.persist_dataframe(source, project_root=tmp_path)
    result = mv.read_dataframe(reference)
    rows = result.to_pandas()
    assert isinstance(result, mv.DataFrameResult)
    assert result.kind == "dataframe" and result.ref == reference and result.row_count == 3
    assert result.ref.project_root == tmp_path.resolve()
    assert result.schema.names == ["value"] and result.schema.field("value").type == dtype
    assert result.schema.metadata is None
    assert rows.index.equals(pd.RangeIndex(3)) and rows.index.name is None and rows.attrs == {}
    assert rows["value"].dtype == pd.ArrowDtype(dtype)
    assert pa.array(rows["value"]).to_pylist() == values


@pytest.mark.parametrize(
    "dtype",
    [
        pa.timestamp("ns", "UTC"),
        pa.timestamp("us", "Asia/Shanghai"),
        pa.timestamp("ms"),
        pa.duration("ns"),
        pa.duration("us"),
        pa.duration("ms"),
        pa.duration("s"),
    ],
)
def test_time_types_preserve_exact_ticks(tmp_path: Path, dtype: pa.DataType) -> None:
    ticks = [-(2**63), 1700000000000000001, None]
    reference = mv.persist_dataframe(
        pd.DataFrame({"time": pd.arrays.ArrowExtensionArray(pa.array(ticks, type=dtype))}),
        project_root=tmp_path,
    )
    result = mv.read_dataframe(reference.to_dict())
    rows = result.to_pandas()
    assert result.schema.field("time").type == dtype
    assert rows["time"].dtype == pd.ArrowDtype(dtype)
    assert pa.array(rows["time"]).cast(pa.int64()).to_pylist() == ticks


def test_nonfinite_floats_and_null_keep_distinct_validity(tmp_path: Path) -> None:
    values = pa.array([float("nan"), float("inf"), -float("inf"), None], type=pa.float64())
    reference = mv.persist_dataframe(
        pd.DataFrame({"ratio": pd.arrays.ArrowExtensionArray(values)}), project_root=tmp_path
    )
    result = mv.read_dataframe(reference)
    exported = pa.array(result.to_pandas()["ratio"])
    assert exported.is_valid().to_pylist() == [True, True, True, False]
    assert math.isnan(exported[0].as_py())
    assert exported[1].as_py() == float("inf") and exported[2].as_py() == -float("inf")


def test_fixed_result_and_exports_are_independent_of_mutation_and_file(tmp_path: Path) -> None:
    source = pd.DataFrame({"region": ["south", "east"], "orders": [12, 7]})
    reference = mv.persist_dataframe(source, project_root=tmp_path)
    wire = reference.to_dict()
    result = mv.read_dataframe(wire)
    other = mv.read_dataframe(reference)
    table = pq.read_table(snapshot_path(tmp_path, reference))
    raw = json.loads(table.schema.metadata[b"marivo.dataframe"])
    assert result.committed_at == datetime.fromisoformat(raw["committed_at"])
    assert result.committed_at.tzinfo == timezone.utc
    source.loc[0, "orders"] = 99
    wire["result_id"] = "changed"
    first, second = result.to_pandas(), result.to_pandas()
    first.loc[0, "region"] = "changed"
    # Even writable Arrow buffer access must not mutate the captured result or another export.
    buffer = pa.array(first["orders"]).buffers()[1]
    struct.pack_into("=q", buffer, 0, 99)
    assert second["region"].tolist() == ["south", "east"]
    assert second["orders"].tolist() == [12, 7]
    assert result.to_pandas()["orders"].tolist() == [12, 7]
    assert other.to_pandas()["orders"].tolist() == [12, 7]
    assert result.ref.to_dict() == reference.to_dict()
    snapshot_path(tmp_path, reference).unlink()
    assert result.to_pandas()["orders"].tolist() == [12, 7]
    with pytest.raises(ResultStorageError) as absent:
        mv.read_dataframe(reference)
    assert absent.value.code == "result_not_found"
    with pytest.raises(FrozenInstanceError):
        field = "row_count"
        setattr(result, field, 99)
    with pytest.raises(TypeError, match="read_dataframe"):
        mv.DataFrameResult()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_zero_column_logical_rows(tmp_path: Path, count: int) -> None:
    reference = mv.persist_dataframe(
        pd.DataFrame(index=pd.RangeIndex(count)), project_root=tmp_path
    )
    result = mv.read_dataframe(reference)
    rows = result.to_pandas()
    assert result.row_count == count and len(result.schema) == 0
    assert rows.shape == (count, 0) and rows.index.equals(pd.RangeIndex(count))


def test_empty_typed_columns_and_hidden_metadata(tmp_path: Path) -> None:
    source = pd.DataFrame({"value": pd.Series([], dtype="uint64[pyarrow]")})
    reference = mv.persist_dataframe(source, project_root=tmp_path)
    path = snapshot_path(tmp_path, reference)
    stored = pq.read_table(path)
    schema = stored.schema.set(0, stored.schema[0].with_metadata({b"private": b"implementation"}))
    pq.write_table(stored.cast(schema), path)
    result = mv.read_dataframe(reference)
    assert result.row_count == 0 and result.schema == pa.schema([("value", pa.uint64())])
    assert result.schema.metadata is None and result.schema[0].metadata is None
    assert result.to_pandas()["value"].dtype == pd.ArrowDtype(pa.uint64())


@pytest.mark.parametrize(
    "encoded",
    [
        "null",
        "3",
        '"df_0123456789abcdef0123456789abcdef"',
        "[]",
        "{}",
        '{"schema":"marivo.frame_ref/v2","store_id":"s","session_id":"s","artifact_id":"a"}',
        '{"schema":"marivo.dataframe_ref/v2","result_id":"df_0123456789abcdef0123456789abcdef"}',
        '{"schema":"marivo.dataframe_ref/v1","project_root":"<root>","result_id":"df_0123456789abcdef0123456789abcdef"}',
        '{"schema":"marivo.dataframe_ref/v2","project_root":"<root>","result_id":"../outside"}',
        '{"schema":"marivo.dataframe_ref/v2","project_root":"<root>","result_id":false}',
        '{"schema":"marivo.dataframe_ref/v2","project_root":"<root>","result_id":"df_0123456789abcdef0123456789abcdef","extra":1}',
    ],
)
def test_invalid_wire_references_fail_without_state(tmp_path: Path, encoded: str) -> None:
    payload = json.loads(encoded)
    if isinstance(payload, dict) and payload.get("project_root") == "<root>":
        payload["project_root"] = str(tmp_path)
    with pytest.raises(ResultStorageError) as failure:
        mv.read_dataframe(payload)
    assert failure.value.code == "invalid_result_reference"
    assert failure.value.location == "reference" and failure.value.expected
    assert failure.value.repair is not None
    assert failure.value.repair.help_target.canonical_id == "read_dataframe"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("root", [None, False, 3, "", "relative", "../parent", "\x00"])
def test_invalid_reference_locations_fail_without_state(tmp_path: Path, root: object) -> None:
    payload = json.loads(
        json.dumps(
            {
                "schema": "marivo.dataframe_ref/v2",
                "project_root": root,
                "result_id": "df_" + "0" * 32,
            }
        )
    )
    with pytest.raises(ResultStorageError) as failure:
        mv.read_dataframe(payload)
    assert failure.value.code == "invalid_result_reference"
    assert failure.value.location == "reference.project_root"
    assert list(tmp_path.iterdir()) == []


def test_reference_location_ignores_current_directory_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    reference = mv.persist_dataframe(pd.DataFrame({"value": [5]}), project_root=Path("saved"))
    assert reference.project_root == root.resolve()
    assert reference.to_dict()["project_root"] == str(root.resolve())
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(elsewhere))
    assert mv.read_dataframe(reference).to_pandas()["value"].tolist() == [5]
    wire = reference.to_dict()
    assert mv.read_dataframe(json.loads(json.dumps(wire))).ref == reference
    wire["project_root"] = str(elsewhere)
    with pytest.raises(ResultStorageError) as absent:
        mv.read_dataframe(wire)
    assert absent.value.code == "result_not_found" and list(elsewhere.iterdir()) == []
    assert mv.read_dataframe(reference).ref.project_root == root.resolve()


def test_missing_captured_project_does_not_fall_back_or_create_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "original"
    root.mkdir()
    reference = mv.persist_dataframe(pd.DataFrame({"value": [5]}), project_root=root)
    moved = root.rename(tmp_path / "moved")
    monkeypatch.chdir(moved)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(moved))
    with pytest.raises(ResultStorageError) as absent:
        mv.read_dataframe(reference)
    assert absent.value.code == "result_not_found"
    assert absent.value.location == "reference.project_root"
    assert not root.exists() and snapshot_path(moved, reference).is_file()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "wrong"),
        ("result_id", "df_" + "0" * 32),
        ("committed_at", "2026-11-09T00:00:00"),
        ("committed_at", "bad-date"),
        ("committed_at", "2026-11-09T00:00:00+08:00"),
        ("logical_row_count", -1),
        ("logical_row_count", True),
        ("logical_row_count", 2),
    ],
)
def test_invalid_saved_metadata_fails_without_repairing_file(
    tmp_path: Path, field: str, value: object
) -> None:
    reference = mv.persist_dataframe(pd.DataFrame({"value": [1]}), project_root=tmp_path)
    path = snapshot_path(tmp_path, reference)
    table = pq.read_table(path)
    raw = json.loads(table.schema.metadata[b"marivo.dataframe"])
    raw[field] = value
    pq.write_table(
        table.replace_schema_metadata({b"marivo.dataframe": json.dumps(raw).encode()}), path
    )
    before = path.read_bytes()
    with pytest.raises(ResultStorageError) as failure:
        mv.read_dataframe(reference)
    assert failure.value.code == "corrupt_dataframe"
    assert failure.value.location == "dataframe." + field
    assert path.read_bytes() == before and not (tmp_path / ".marivo/analysis").exists()


@pytest.mark.parametrize(
    "fault", ["schema", "duplicate_columns", "empty_column", "missing_metadata", "parquet", "page"]
)
def test_corrupt_saved_structure(tmp_path: Path, fault: str) -> None:
    reference = mv.persist_dataframe(pd.DataFrame({"value": [1]}), project_root=tmp_path)
    path = snapshot_path(tmp_path, reference)
    if fault == "parquet":
        path.write_bytes(b"not a Parquet file")
    elif fault == "page":
        with pq.ParquetFile(path) as file:
            offset = file.metadata.row_group(0).column(0).data_page_offset
        data = bytearray(path.read_bytes())
        data[offset : offset + 8] = b"\x00" * 8
        path.write_bytes(data)
    elif fault == "missing_metadata":
        pq.write_table(pa.table({"value": [1]}), path)
    else:
        original = pq.read_table(path)
        if fault == "duplicate_columns":
            bad = pa.Table.from_arrays([pa.array([1]), pa.array([2])], names=["value", "value"])
        elif fault == "empty_column":
            bad = pa.table({"": [1]})
        else:
            bad = pa.table({"value": [[1, 2]]})
        bad = bad.replace_schema_metadata(original.schema.metadata)
        pq.write_table(bad, path)
    with pytest.raises(ResultStorageError) as failure:
        mv.read_dataframe(reference)
    assert failure.value.code == "corrupt_dataframe"


@pytest.mark.parametrize("fault", ["permission", "io", "symlink"])
def test_path_failures_are_read_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Literal["permission", "io", "symlink"]
) -> None:
    reference = mv.persist_dataframe(pd.DataFrame({"value": [1]}), project_root=tmp_path)
    path = snapshot_path(tmp_path, reference)
    if fault == "symlink":
        saved = path.rename(path.with_suffix(".saved"))
        path.symlink_to(saved)
    else:

        def denied(self: Path, mode: str = "r") -> NoReturn:
            if fault == "io":
                raise OSError(errno.EIO, "injected storage failure")
            raise PermissionError("injected unreadable snapshot")

        monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ResultStorageError) as failure:
        mv.read_dataframe(reference)
    assert failure.value.code == (
        "invalid_result_reference" if fault == "symlink" else "result_read_failed"
    )
    assert failure.value.repair is not None
    assert failure.value.repair.help_target.canonical_id == "read_dataframe"
