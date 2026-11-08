"""Immutable plain-table persistence, independent of analysis Sessions and HTTP."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.core.result_reference import DataFrameReference
from marivo.analysis.materialization.result_files import (
    ResultStorageError,
    publication_file,
    state_path,
)

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
_METADATA = b"marivo.dataframe"
_FORMAT = "marivo.dataframe_snapshot/v1"


def _unencodable(location: str, received: str) -> ResultStorageError:
    return ResultStorageError(
        "unencodable_value",
        location,
        received,
        "Use unique nonempty string columns, reset business indexes into columns, and use supported scalar values.",
    )


def _corrupt() -> ResultStorageError:
    return ResultStorageError(
        "corrupt_dataframe",
        "dataframe",
        "invalid Parquet data or metadata",
        "Restore the original complete Parquet file; fixed reads never rerun a producer.",
    )


def dtype(value: pa.DataType, location: str) -> dict[str, JsonValue]:
    if pa.types.is_boolean(value):
        return {"kind": "boolean"}
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return {"kind": "string"}
    if pa.types.is_null(value):
        return {"kind": "null"}
    if pa.types.is_integer(value):
        return {"kind": str(value)}
    if value in (pa.float32(), pa.float64()):
        return {"kind": "float32" if value == pa.float32() else "float64"}
    if pa.types.is_date(value):
        return {"kind": "date"}
    if pa.types.is_decimal(value):
        return {"kind": "decimal", "precision": value.precision, "scale": value.scale}
    if pa.types.is_timestamp(value):
        return {"kind": "timestamp", "unit": value.unit, "timezone": value.tz}
    if pa.types.is_duration(value):
        return {"kind": "duration", "unit": value.unit}
    raise _unencodable(location, f"unsupported column type {value}")


def _table(frame: pd.DataFrame) -> pa.Table:
    if (
        not isinstance(frame.index, pd.RangeIndex)
        or frame.index.name is not None
        or frame.index.start != 0
        or frame.index.step != 1
    ):
        raise _unencodable("index", "expected an unnamed default RangeIndex")
    names: list[str] = []
    arrays: list[pa.Array] = []
    for index, name in enumerate(frame.columns):
        if not isinstance(name, str) or not name or name in names:
            raise _unencodable(f"columns[{index}]", "expected a unique nonempty string column name")
        try:
            # Column conversion preserves Arrow-backed validity without serializing attrs.
            array = pa.array(frame.iloc[:, index], from_pandas=True)
            dtype(array.type, f"columns.{name}")
        except (pa.ArrowException, ValueError, TypeError, OverflowError):
            raise _unencodable(
                f"columns.{name}", "values do not have a supported Arrow representation"
            ) from None
        names.append(name)
        arrays.append(array)
    return pa.Table.from_arrays(arrays, names=names)


def publish(root: Path, frame: pd.DataFrame) -> DataFrameReference:
    table = _table(frame)
    reference = DataFrameReference._create("df_" + uuid4().hex)
    metadata: dict[str, JsonValue] = {
        "schema": _FORMAT,
        "result_id": reference.result_id,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "logical_row_count": len(frame),
    }
    table = table.replace_schema_metadata({_METADATA: json.dumps(metadata).encode()})
    path = state_path(root, f"dataframes/{reference.result_id}.parquet")
    with publication_file(path) as temporary:
        try:
            pq.write_table(table, temporary, version="2.6")
        except (pa.ArrowException, ValueError, TypeError):
            raise _unencodable("dataframe", "the table cannot be represented as Parquet") from None
    return reference


def metadata(file: pq.ParquetFile, result_id: str) -> tuple[str, int]:
    raw: object = json.loads((file.schema_arrow.metadata or {})[_METADATA])
    if not isinstance(raw, dict) or not {
        "schema",
        "result_id",
        "committed_at",
        "logical_row_count",
    }.issubset(raw):
        raise _corrupt()
    committed, count = raw["committed_at"], raw["logical_row_count"]
    if (
        raw["schema"] != _FORMAT
        or raw["result_id"] != result_id
        or not isinstance(committed, str)
        or type(count) is not int
        or count < 0
    ):
        raise _corrupt()
    if datetime.fromisoformat(committed).utcoffset() != timedelta(0):
        raise _corrupt()
    if len(file.schema_arrow) and count != file.metadata.num_rows:
        raise _corrupt()
    return committed, count
