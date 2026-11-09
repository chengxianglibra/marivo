"""HTTP projection of immutable plain-table results."""

from __future__ import annotations

import re
from pathlib import Path

from marivo.analysis.core.result_reference import DataFrameReference
from marivo.analysis.materialization.dataframe_store import _corrupt, dtype, open_snapshot
from marivo.server.frame_projection import fact, scalar
from marivo.server.wire import Budget, JsonObject, JsonValue, bad_request


def dataframe_id(value: str) -> str:
    if re.fullmatch(r"df_[0-9a-f]{32}", value) is None:
        raise bad_request(
            "result_id", "Invalid DataFrame identity.", "Use a saved DataFrame reference."
        )
    return value


def report(root: Path, result_id: str, budget: Budget) -> bytes:
    with open_snapshot(root, dataframe_id(result_id)) as (file, committed, count, schema):
        names = schema.names
        columns: list[JsonValue] = [
            {"name": item.name, "dtype": dtype(item.type, f"columns.{item.name}")}
            for item in schema
        ]
        metadata: JsonObject = {
            "kind": "dataframe",
            "ref": fact(DataFrameReference._create(root, result_id).to_dict(), "ref"),
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
                raise _corrupt(
                    "dataframe.row_count", "decoded row count differs from saved metadata"
                )
        else:
            for index in range(count):
                budget.check()
                budget.append(data, b",[]" if index else b"[]")
        budget.append(data, b'],"row_count":')
        budget.write(data, count)
        budget.append(data, b"}}")
        budget.check()
        return bytes(data)
