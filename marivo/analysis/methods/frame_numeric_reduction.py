"""Native numeric finishers shared by grouped and singleton Frame reductions."""

from __future__ import annotations

from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.methods.numeric_state import (
    finish_division,
    finish_float64,
    sum_values,
    typed_null_mask,
)
from marivo.analysis.methods.physical import DurationType
from marivo.analysis.model.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.semantic.spi import numeric_failure


def reduce_numeric(
    series: pd.Series, dtype: DataType, method: Literal["sum", "mean"], *, empty_group: bool
) -> object:
    value: object
    try:
        if empty_group and method == "sum":
            value = (
                sum_values((), stage_arrow_type(dtype))
                if not isinstance(dtype, DurationDataType)
                else 0
            )
        elif isinstance(dtype, DurationDataType):
            active = series.loc[~typed_null_mask(series)]
            ticks = tuple(
                int(tick)
                for tick in (
                    pa.array(active, type=pa.duration(dtype.unit), from_pandas=True)
                    .cast(pa.int64())
                    .to_pylist()
                )
            )
            value = (
                finish_division(
                    sum(ticks),
                    1 if method == "sum" else len(ticks),
                    DurationType(dtype.unit),
                )
                if ticks
                else None
            )
        elif method == "sum" and isinstance(series.dtype, pd.ArrowDtype):
            array = pa.array(series, from_pandas=False)
            if pa.types.is_floating(array.type) and array.null_count:
                # Arrow's bitmap and dense float sums can round differently.
                active = series.loc[~typed_null_mask(series)]
                array = pa.array(active, from_pandas=False)
            value = pc.sum(array, skip_nulls=True, min_count=1).as_py()
        else:
            active = series.loc[~typed_null_mask(series)]
            array = pa.array(active, from_pandas=False)
            value = (
                (pc.sum(array, min_count=0) if method == "sum" else pc.mean(array)).as_py()
                if len(active) or empty_group
                else None
            )
    except (ValueError, OverflowError) as error:
        numeric_failure("a native current-row statistic", str(error))
    return value


def finish_reduced_values(values: pd.Series, column: DataColumn) -> pd.Series:
    if isinstance(column.dtype, DurationDataType):
        ticks = pa.array(values, type=pa.int64(), from_pandas=True)
        return pd.Series(pd.arrays.ArrowExtensionArray(ticks.cast(pa.duration(column.dtype.unit))))
    if column.dtype == ScalarDataType("float64"):
        return finish_float64(values, "Frame.reduce")
    return values


def singleton_arrow_numeric(
    values: pd.Series, dtype: DataType, method: Literal["sum", "mean"]
) -> pd.Series | None:
    """Use the same output carriers and singleton arithmetic as Arrow reductions."""
    if not isinstance(values.dtype, pd.ArrowDtype):
        return None
    array = pa.array(values, from_pandas=False)
    if isinstance(dtype, DurationDataType):
        return values if array.type == pa.duration(dtype.unit) else None
    if dtype == ScalarDataType("float64") and array.type == pa.float64():
        # The native accumulator starts at +0, including for a singleton -0.0.
        return pd.Series(pd.arrays.ArrowExtensionArray(pc.add(array, 0.0)))
    if dtype == ScalarDataType("int64") and array.type == pa.int64():
        if method == "mean":
            return pd.Series(
                pd.arrays.ArrowExtensionArray(pc.cast(array, pa.float64(), safe=False))
            )
        return pd.Series(array.to_pylist(), dtype=object)
    if isinstance(dtype, DecimalDataType) and pa.types.is_decimal(array.type):
        # The grouped kernel also returns boxed scalars for these types.
        return pd.Series(array.to_pylist(), dtype=object)
    return None
