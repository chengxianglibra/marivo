"""HTTP projection of immutable plain-table results."""

from __future__ import annotations

import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.core.result_reference import DataFrameReference
from marivo.analysis.materialization.dataframe_store import _corrupt, dtype
from marivo.analysis.materialization.dataframe_store import metadata as read_metadata
from marivo.analysis.materialization.result_files import ResultStorageError, state_path
from marivo.server.frame_projection import fact, scalar
from marivo.server.wire import Budget, JsonObject, JsonValue, bad_request


def dataframe_id(value: str) -> str:
    if re.fullmatch(r"df_[0-9a-f]{32}", value) is None:
        raise bad_request(
            "result_id", "Invalid DataFrame identity.", "Use a saved DataFrame reference."
        )
    return value


def report(root: Path, result_id: str, budget: Budget) -> bytes:
    path = state_path(root, f"dataframes/{dataframe_id(result_id)}.parquet")
    try:
        # One open file owns metadata and rows, even if directories change during a read.
        with path.open("rb") as source:
            file = pq.ParquetFile(source)
            committed, count = read_metadata(file, result_id)
            names = file.schema_arrow.names
            if any(not name for name in names) or len(set(names)) != len(names):
                raise _corrupt()
            columns: list[JsonValue] = [
                {"name": field.name, "dtype": dtype(field.type, f"columns.{field.name}")}
                for field in file.schema_arrow
            ]
            metadata: JsonObject = {
                "kind": "dataframe",
                "ref": fact(DataFrameReference._create(result_id).to_dict(), "ref"),
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
        raise ResultStorageError(
            "result_not_found",
            "result_id",
            "result is absent",
            "Check the saved reference and project.",
        ) from None
    except (OSError, pa.ArrowException, ValueError, KeyError, TypeError):
        raise _corrupt() from None
