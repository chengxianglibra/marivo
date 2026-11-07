"""Immutable, self-contained Parquet results outside the Analysis Store."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.server.endpoints import (
    DataFrameReference,
    dataframe_id,
    missing,
    publication_file,
    state_path,
)
from marivo.server.frame_projection import scalar
from marivo.server.wire import HELP, Budget, JsonObject, JsonValue, ServiceError

_METADATA = b"marivo.dataframe"
_FORMAT = "marivo.dataframe_snapshot/v1"


def _unencodable(location: str, received: str) -> ServiceError:
    return ServiceError(
        "unencodable_value",
        f"DataFrame cannot be saved: {received}.",
        location,
        "Use unique nonempty string columns, reset business indexes into columns, and return supported scalar values.",
        HELP,
    )


def _corrupt() -> ServiceError:
    return ServiceError(
        "corrupt_dataframe",
        "The Parquet result or its captured metadata is invalid.",
        "dataframe",
        "Restore the original Parquet file; fixed reads never rerun its script.",
        HELP,
    )


def dtype(value: pa.DataType, location: str) -> JsonObject:
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


def publish(root: Path, frame: pd.DataFrame, session_id: str) -> DataFrameReference:
    table = _table(frame)
    reference = DataFrameReference("df_" + uuid4().hex)
    metadata: JsonObject = {
        "schema": _FORMAT,
        "result_id": reference.result_id,
        "session_id": session_id,
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


def _metadata(file: pq.ParquetFile, result_id: str) -> tuple[str, str, int]:
    raw: object = json.loads((file.schema_arrow.metadata or {})[_METADATA])
    if not isinstance(raw, dict) or set(raw) != {
        "schema",
        "result_id",
        "session_id",
        "committed_at",
        "logical_row_count",
    }:
        raise _corrupt()
    session, committed, count = raw["session_id"], raw["committed_at"], raw["logical_row_count"]
    if (
        raw["schema"] != _FORMAT
        or raw["result_id"] != result_id
        or not isinstance(session, str)
        or not session
        or not isinstance(committed, str)
        or type(count) is not int
        or count < 0
    ):
        raise _corrupt()
    if datetime.fromisoformat(committed).utcoffset() != timedelta(0):
        raise _corrupt()
    if len(file.schema_arrow) and count != file.metadata.num_rows:
        raise _corrupt()
    return session, committed, count


def report(root: Path, result_id: str, budget: Budget) -> bytes:
    path = state_path(root, f"dataframes/{dataframe_id(result_id)}.parquet")
    try:
        # One open file owns metadata and rows, even if directories change during a read.
        with path.open("rb") as source:
            file = pq.ParquetFile(source)
            session, committed, count = _metadata(file, result_id)
            names = file.schema_arrow.names
            if any(not name for name in names) or len(set(names)) != len(names):
                raise _corrupt()
            columns: list[JsonValue] = [
                {"name": field.name, "dtype": dtype(field.type, f"columns.{field.name}")}
                for field in file.schema_arrow
            ]
            metadata: JsonObject = {
                "kind": "dataframe",
                "ref": DataFrameReference(result_id).ref(),
                "session_id": session,
                "committed_at": committed,
                "schema": {"columns": columns},
            }
            data = bytearray()
            budget.write(data, metadata)
            data.pop()
            budget.used -= 1
            budget.append(data, b',"data":{"layout":')
            budget.write(data, names)
            budget.append(data, b',"rows":[')
            seen = 0
            if names:
                for batch in file.iter_batches(batch_size=10_000):
                    budget.check()
                    for index in range(batch.num_rows):
                        row = [
                            scalar(column[index], f"columns.{name}", exact=True)
                            for name, column in zip(names, batch.columns, strict=True)
                        ]
                        if seen:
                            budget.append(data, b",")
                        budget.write(data, row)
                        seen += 1
                if seen != count:
                    raise _corrupt()
            else:
                for index in range(count):
                    budget.check()
                    budget.append(data, b",[]" if index else b"[]")
            budget.append(data, b'],"row_count":')
            budget.write(data, count)
            budget.append(data, b"}}")
            budget.check()
            return bytes(data)
    except FileNotFoundError:
        raise missing("result_id") from None
    except (OSError, pa.ArrowException, ValueError, KeyError, TypeError):
        raise _corrupt() from None
