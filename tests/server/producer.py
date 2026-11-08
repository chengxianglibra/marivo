"""Independent producer used by the HTTP read service acceptance tests."""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa

import marivo.analysis as mv
from marivo._temporal import builtin_grain
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.admission import DatasetRuntime


def produce(root: Path, *, extended: bool = False) -> dict[str, str]:
    local = DatasetRuntime.create(root, "service-fixture", timezone="UTC").local
    key = DataColumn("key", "id", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("row", (key,), name="example.row"),),
        (
            DataColumn("value", "amount", DecimalDataType(25, 3), True, "USD"),
            DataColumn("stamp", "event_time", TimestampDataType("ns", "UTC"), False),
            DataColumn("elapsed", "elapsed", DurationDataType("ns"), False),
            DataColumn("float", "ratio", ScalarDataType("float64"), False),
        ),
        (DataColumn("present", "present", ScalarDataType("boolean"), False),),
        value_paths=(("amount",), ("event_time",), ("elapsed",), ("ratio",)),
        presence_paths=(("present",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(
                [9007199254740993, 9007199254740995, 9007199254740997], dtype="int64[pyarrow]"
            ),
            "value": pd.Series(
                [Decimal("9007199254740993.100"), None, None],
                dtype=pd.ArrowDtype(pa.decimal128(25, 3)),
            ),
            "stamp": pd.Series(
                [1700000000000000001, 1700000000000000002, -(2**63)],
                dtype=pd.ArrowDtype(pa.timestamp("ns", tz="UTC")),
            ),
            "elapsed": pd.Series([1, 2, -3], dtype=pd.ArrowDtype(pa.duration("ns"))),
            "float": pd.arrays.ArrowExtensionArray(pa.array([1.5, float("nan"), float("inf")])),
            "present": pd.Series([True, True, False], dtype="bool[pyarrow]"),
        }
    )

    def completed(data: CompletedFrameData, description: str) -> AnalysisFrame:
        metadata = FrameResultMetadata(
            description, EvaluationRecord(datetime(2026, 11, 6, tzinfo=timezone.utc), (), ())
        )
        return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))

    def save(frame: AnalysisFrame, description: str) -> str:
        return frame.persist(description=description).artifact_id

    ids = {
        "store_id": local.store.store_id,
        "session_id": local.session_ref,
        "artifact_id": save(
            completed(CompletedFrameData(schema, rows), "exact"), "service fixture"
        ),
        "empty_id": save(
            completed(CompletedFrameData(schema, rows.iloc[:0]), "empty"), "empty fixture"
        ),
    }
    if not extended:
        return ids

    unit = CompletedFrameData(DataSchema((), ()), pd.DataFrame(index=pd.RangeIndex(1)))
    ids["unit_id"] = save(completed(unit, "unit"), "zero columns")

    composite = DataSchema(
        (
            DataAxis(
                "first",
                (
                    DataColumn("country", "country", ScalarDataType("string"), False),
                    DataColumn("region_id", "id", ScalarDataType("int64"), False),
                ),
                kind="member",
                name="sales.region",
            ),
            DataAxis(
                "second",
                (DataColumn("shipping", "shipping", ScalarDataType("string"), False),),
                name="shipping.region",
            ),
        ),
        (
            DataColumn("value", "region", ScalarDataType("int64"), False),
            DataColumn("value2", "region__2", ScalarDataType("int64"), False),
        ),
        value_paths=(("region",), ("region__2",)),
    )
    collision_rows = pd.DataFrame(
        {
            "country": pd.Series(["CN"], dtype="string[pyarrow]"),
            "region_id": pd.Series([7], dtype="int64[pyarrow]"),
            "shipping": pd.Series(["SH"], dtype="string[pyarrow]"),
            "value": pd.Series([4], dtype="int64[pyarrow]"),
            "value2": pd.Series([5], dtype="int64[pyarrow]"),
        }
    )
    ids["collision_id"] = save(
        completed(CompletedFrameData(composite, collision_rows), "collision"), "name collision"
    )

    def calendar(count: int, name: str, *, has_rows: bool = True) -> AnalysisFrame:
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = (start + timedelta(days=count)).date().isoformat()
        axis = exact_scope("2000-01-01", end, "UTC").by(builtin_grain("day"))
        frozen = freeze_calendar(
            axis._binding,
            token="period",
            name="day",
            key=DataColumn("bucket", "bucket", TimestampDataType("us", "UTC"), False),
        )
        time_schema = DataSchema(
            (frozen.axis,),
            (DataColumn("amount", "amount", ScalarDataType("int64"), False),),
            value_paths=(("amount",),),
        )
        data = pd.DataFrame(
            {
                "bucket": frozen.directory.rows["bucket"],
                "amount": pd.Series(range(count), dtype="int64[pyarrow]"),
            }
        )
        return completed(
            CompletedFrameData(
                time_schema, data if has_rows else data.iloc[:0], frozen.attachments
            ),
            name,
        )

    ids["large_id"] = save(calendar(10_001, "large"), "multi-batch rows and periods")
    ids["empty_time_id"] = save(calendar(2, "empty-time", has_rows=False), "periods without rows")
    current, baseline = calendar(4, "current"), calendar(3, "baseline")
    ids["compare_id"] = save(
        current.compare({"base": mv.baseline(baseline, match=mv.ordinal())}).compute(),
        "comparison with an absent baseline period",
    )
    ids["paired_id"] = save(
        current.align(baseline, match=mv.ordinal()).compute(),
        "paired periods",
    )
    return ids


if __name__ == "__main__":
    print(json.dumps(produce(Path(sys.argv[1]), extended="--extended" in sys.argv)))
