"""R2.3 pandas StageOutput: one normalization entry for source and local stage data."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput, normalize_stage_frame

INT = ScalarDataType("int64")
KEY = DataColumn("k.0", "id", INT, False)
VALUE = DataColumn("r.0", "amount", INT, True)


def test_normalization_returns_a_new_exact_arrow_frame() -> None:
    frame = pd.DataFrame(
        {
            "k.0": pd.Series([2, 1], dtype=pd.ArrowDtype(pa.int32())),
            "r.0": pd.Series([9007199254740993, None], dtype=object),
            "s.0": ["APAC", None],
            "d.0": [Decimal("1.25"), None],
            "t.0": [datetime(2026, 1, 1, tzinfo=timezone.utc), None],
        }
    )
    columns = (
        KEY,
        VALUE,
        DataColumn("s.0", "region", ScalarDataType("string"), True),
        DataColumn("d.0", "price", DecimalDataType(10, 2), True),
        DataColumn("t.0", "ordered_at", TimestampDataType("us", "UTC"), True),
    )
    result = normalize_stage_frame(frame, columns, ("k.0",))
    assert result is not frame
    assert [str(dtype) for dtype in result.dtypes] == [
        "int64[pyarrow]",
        "int64[pyarrow]",
        "string[pyarrow]",
        "decimal128(10, 2)[pyarrow]",
        "timestamp[us, tz=UTC][pyarrow]",
    ]
    assert result["r.0"].iloc[0] == 9007199254740993
    assert all(pd.isna(result.iloc[1, 1:]))
    assert frame["k.0"].dtype == pd.ArrowDtype(pa.int32())


@pytest.mark.parametrize(
    ("frame", "fragment"),
    (
        (pd.DataFrame({"r.0": [1], "k.0": [1]}), "column order"),
        (pd.DataFrame({"k.0": [1], "r.0": [1.5]}), "float64 delivered for int64"),
        (pd.DataFrame({"k.0": [1], "r.0": [1.0]}), "float64 delivered for int64"),
        (pd.DataFrame({"k.0": [None, 2], "r.0": [1, 2]}), "null or duplicate key"),
        (pd.DataFrame({"k.0": [1, 1], "r.0": [1, 2]}), "null or duplicate key"),
    ),
)
def test_normalization_rejects_layout_and_lossy_values(frame: pd.DataFrame, fragment: str) -> None:
    with pytest.raises(MaterializationError) as caught:
        normalize_stage_frame(frame, (KEY, VALUE), ("k.0",))
    assert fragment in str(caught.value)
    assert caught.value.repair is not None


def test_normalization_rejects_timezone_and_nullability_drift() -> None:
    column = DataColumn("t.0", "ordered_at", TimestampDataType("us", "UTC"), False)
    shanghai = pd.DataFrame({"t.0": [pd.Timestamp("2026-01-01", tz="Asia/Shanghai")]})
    with pytest.raises(MaterializationError, match="timezone"):
        normalize_stage_frame(shanghai, (column,), ())
    with pytest.raises(MaterializationError, match="nonnullable"):
        normalize_stage_frame(pd.DataFrame({"t.0": [None]}), (column,), ())


def test_stage_output_is_built_only_through_normalization() -> None:
    output = StageOutput.from_frame(
        pd.DataFrame({"k.0": [1], "r.0": [None]}), (KEY, VALUE), ("k.0",)
    )
    assert output.keys == ("k.0",)
    assert output.receipts == ()
    assert str(output.table["r.0"].dtype) == "int64[pyarrow]"
    with pytest.raises(MaterializationError, match="normalized"):
        StageOutput((KEY, VALUE), ("k.0",), pd.DataFrame({"k.0": [1], "r.0": [1]}))
