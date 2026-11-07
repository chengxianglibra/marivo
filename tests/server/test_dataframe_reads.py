"""Exact DataFrame values survive publication and source-free cold reads."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from threading import Event

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.server.dataframes import publish, report
from marivo.server.wire import Budget, Limits, ServiceError


def read(root: Path, result_id: str) -> dict[str, object]:
    limits = Limits()
    budget = Budget(limits, "dataframe", limits.deadline(), Event())
    body = report(root, result_id, budget)
    assert len(body) == budget.used
    result: dict[str, object] = json.loads(body)
    return result


def test_exact_values_and_cold_reader_without_store(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "id": pd.Series([9007199254740993, 9007199254740995, None], dtype="int64[pyarrow]"),
            "amount": pd.Series(
                [Decimal("9007199254740993.100"), None, Decimal("-0.010")],
                dtype=pd.ArrowDtype(pa.decimal128(25, 3)),
            ),
            "stamp": pd.Series(
                [1700000000000000001, -(2**63), None],
                dtype=pd.ArrowDtype(pa.timestamp("ns", "UTC")),
            ),
            "elapsed": pd.Series([1, -2, None], dtype=pd.ArrowDtype(pa.duration("ns"))),
            "ratio": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([float("nan"), float("inf"), None], type=pa.float64())
                )
            ),
        }
    )
    frame.attrs["not_json"] = object()
    reference = publish(tmp_path, frame, "session_capture")
    assert not (tmp_path / ".marivo/analysis").exists()
    payload = read(tmp_path, reference.result_id)
    assert payload["kind"] == "dataframe" and payload["session_id"] == "session_capture"
    assert payload["ref"] == {"schema": "marivo.dataframe_ref/v1", "result_id": reference.result_id}
    assert payload["data"] == {
        "layout": ["id", "amount", "stamp", "elapsed", "ratio"],
        "row_count": 3,
        "rows": [
            [
                "9007199254740993",
                "9007199254740993.100",
                "2023-11-14T22:13:20.000000001+00:00",
                "1",
                {"float": "nan"},
            ],
            [
                "9007199254740995",
                None,
                "1677-09-21T00:12:43.145224192+00:00",
                "-2",
                {"float": "inf"},
            ],
            [None, "-0.010", None, None, None],
        ],
    }
    assert frame.attrs.keys() == {"not_json"}
    cold = subprocess.check_output(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from threading import Event
from marivo.server.dataframes import report
from marivo.server.wire import Budget, Limits
limits = Limits()
sys.stdout.buffer.write(report(Path(sys.argv[1]), sys.argv[2], Budget(limits, 'dataframe', limits.deadline(), Event())))
""",
            str(tmp_path),
            reference.result_id,
        ]
    )
    assert json.loads(cold) == payload


@pytest.mark.parametrize("rows", [0, 1, 3])
def test_zero_column_row_count_is_retained(tmp_path: Path, rows: int) -> None:
    reference = publish(tmp_path, pd.DataFrame(index=pd.RangeIndex(rows)), "session_zero")
    payload = read(tmp_path, reference.result_id)
    assert payload["schema"] == {"columns": []}
    assert payload["data"] == {"layout": [], "rows": [[] for _ in range(rows)], "row_count": rows}


def test_zero_column_invalid_logical_row_count_fails(tmp_path: Path) -> None:
    reference = publish(tmp_path, pd.DataFrame(index=pd.RangeIndex(3)), "session_zero")
    path = tmp_path / ".marivo/server/dataframes" / (reference.result_id + ".parquet")
    table = pq.read_table(path)
    metadata = json.loads(table.schema.metadata[b"marivo.dataframe"])
    metadata["logical_row_count"] = -1
    pq.write_table(
        table.replace_schema_metadata({b"marivo.dataframe": json.dumps(metadata).encode()}), path
    )
    with pytest.raises(ServiceError) as failure:
        read(tmp_path, reference.result_id)
    assert failure.value.code == "corrupt_dataframe"


def test_empty_typed_columns_and_immutable_versions(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "count": pd.Series([], dtype="uint64[pyarrow]"),
            "amount": pd.Series([], dtype=pd.ArrowDtype(pa.decimal128(18, 2))),
        }
    )
    old = publish(tmp_path, frame, "session_old")
    newer = publish(tmp_path, pd.DataFrame({"count": [20]}), "session_new")
    assert old != newer
    payload = read(tmp_path, old.result_id)
    assert payload["schema"] == {
        "columns": [
            {"name": "count", "dtype": {"kind": "uint64"}},
            {"name": "amount", "dtype": {"kind": "decimal", "precision": 18, "scale": 2}},
        ]
    }
    assert payload["data"] == {"layout": ["count", "amount"], "rows": [], "row_count": 0}
    assert read(tmp_path, newer.result_id)["data"] == {
        "layout": ["count"],
        "rows": [["20"]],
        "row_count": 1,
    }


@pytest.mark.parametrize(
    "frame,location",
    [
        (pd.DataFrame([[1, 2]], columns=["value", "value"]), "columns[1]"),
        (pd.DataFrame({1: [2]}), "columns[0]"),
        (pd.DataFrame({"": [2]}), "columns[0]"),
        (pd.DataFrame({"value": [1]}, index=["member"]), "index"),
        (pd.DataFrame({"value": [object()]}), "columns.value"),
        (pd.DataFrame({"value": [[1, 2]]}), "columns.value"),
    ],
)
def test_unsupported_structure_is_not_silently_coerced(
    tmp_path: Path, frame: pd.DataFrame, location: str
) -> None:
    with pytest.raises(ServiceError) as failure:
        publish(tmp_path, frame, "session_bad")
    assert failure.value.code == "unencodable_value"
    assert failure.value.location == location
    assert not list(tmp_path.rglob("*.parquet"))


def test_corrupt_metadata_missing_data_and_response_budget(tmp_path: Path) -> None:
    saved = publish(tmp_path, pd.DataFrame({"value": ["x" * 1000] * 1200}), "session_budget")
    limits = Limits(mib=1)
    with pytest.raises(ServiceError) as failure:
        report(tmp_path, saved.result_id, Budget(limits, "dataframe", limits.deadline(), Event()))
    assert failure.value.code == "response_too_large"
    path = tmp_path / ".marivo/server/dataframes" / (saved.result_id + ".parquet")
    table = pq.read_table(path)
    metadata = json.loads(table.schema.metadata[b"marivo.dataframe"])
    metadata["logical_row_count"] = 10
    pq.write_table(
        table.replace_schema_metadata({b"marivo.dataframe": json.dumps(metadata).encode()}), path
    )
    with pytest.raises(ServiceError) as failure:
        read(tmp_path, saved.result_id)
    assert failure.value.code == "corrupt_dataframe"
    path.unlink()
    with pytest.raises(ServiceError) as failure:
        read(tmp_path, saved.result_id)
    assert failure.value.code == "result_not_found"
