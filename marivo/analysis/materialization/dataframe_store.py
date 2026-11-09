"""Immutable plain-table persistence, independent of analysis Sessions and HTTP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.core.dataframe_result import DataFrameResult
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


def _corrupt(
    location: str = "dataframe", received: str = "invalid Parquet data or metadata"
) -> ResultStorageError:
    return ResultStorageError(
        "corrupt_dataframe",
        location,
        received,
        "Restore the original complete Parquet file; fixed reads never rerun a producer.",
        target="read_dataframe",
        expected="a complete marivo.dataframe_snapshot/v1 file with supported scalar columns",
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
    reference = DataFrameReference._create(root, "df_" + uuid4().hex)
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
    try:
        raw: object = json.loads((file.schema_arrow.metadata or {})[_METADATA])
    except (ValueError, KeyError, TypeError):
        raise _corrupt(
            "dataframe.metadata", "missing or invalid marivo.dataframe metadata"
        ) from None
    if not isinstance(raw, dict) or not {
        "schema",
        "result_id",
        "committed_at",
        "logical_row_count",
    }.issubset(raw):
        raise _corrupt("dataframe.metadata", "missing required snapshot metadata fields")
    committed, count = raw["committed_at"], raw["logical_row_count"]
    for name, expected in (("schema", _FORMAT), ("result_id", result_id)):
        if raw[name] != expected:
            raise _corrupt(f"dataframe.{name}", f"expected {expected!r}; received {raw[name]!r}")
    try:
        valid_time = isinstance(committed, str) and datetime.fromisoformat(
            committed
        ).utcoffset() == timedelta(0)
    except ValueError:
        valid_time = False
    if not valid_time or not isinstance(committed, str):
        raise _corrupt(
            "dataframe.committed_at", f"expected a UTC timestamp; received {committed!r}"
        )
    if type(count) is not int or count < 0:
        raise _corrupt(
            "dataframe.logical_row_count", f"expected a nonnegative integer; received {count!r}"
        )
    if len(file.schema_arrow) and count != file.metadata.num_rows:
        raise _corrupt(
            "dataframe.logical_row_count",
            f"expected {file.metadata.num_rows} physical rows; received {count}",
        )
    return committed, count


def read_schema(file: pq.ParquetFile) -> pa.Schema:
    """Validate saved physical columns and remove implementation metadata."""
    names = file.schema_arrow.names
    if any(not name for name in names) or len(set(names)) != len(names):
        raise _corrupt("schema.columns", "expected unique nonempty string column names")
    for item in file.schema_arrow:
        try:
            dtype(item.type, f"schema.columns.{item.name}")
        except ResultStorageError:
            raise _corrupt(
                f"schema.columns.{item.name}", f"unsupported saved column type {item.type}"
            ) from None
    return pa.schema([item.remove_metadata() for item in file.schema_arrow])


@contextmanager
def open_snapshot(
    root: Path, result_id: str
) -> Iterator[tuple[pq.ParquetFile, str, int, pa.Schema]]:
    """Consume metadata and rows through one file; both native and HTTP reads use this."""
    try:
        path = state_path(root, f"dataframes/{result_id}.parquet", target="read_dataframe")
        with path.open("rb") as source:
            file = pq.ParquetFile(source)
            committed, count = metadata(file, result_id)
            yield file, committed, count, read_schema(file)
    except FileNotFoundError:
        raise ResultStorageError(
            "result_not_found",
            "result_id",
            "result is absent",
            "Check the saved reference and project; restore the original snapshot if needed.",
            target="read_dataframe",
        ) from None
    except (pa.ArrowException, ValueError, KeyError, TypeError, OverflowError):
        raise _corrupt() from None
    except OSError as error:
        # PyArrow reports malformed Parquet pages as plain OSError without errno.
        if error.errno is None and not isinstance(error, PermissionError):
            raise _corrupt() from None
        raise ResultStorageError(
            "result_read_failed",
            "dataframe",
            f"snapshot I/O failed ({type(error).__name__})",
            "Check file read permissions and storage availability, then explicitly read again.",
            target="read_dataframe",
        ) from None


def read(root: Path, reference: DataFrameReference) -> DataFrameResult:
    with open_snapshot(root, reference.result_id) as (file, committed, count, schema):
        table = file.read()
        if len(schema) and table.num_rows != count:
            raise _corrupt("dataframe.row_count", "decoded row count differs from saved metadata")
        table = pa.Table.from_arrays(table.columns, schema=schema)
        return DataFrameResult._create(reference, datetime.fromisoformat(committed), count, table)
