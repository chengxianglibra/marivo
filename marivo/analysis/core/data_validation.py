"""Pure exact carrier, complete column and full-key validation."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.unique_keys import UNKNOWN_UNIQUE_KEYS, UniqueKeys
from marivo.analysis.materialization.errors import MaterializationError


def typed_null_mask(values: pd.Series) -> pd.Series:
    """Read validity without treating valid floating NaN as a missing value.

    Stage inputs carry Arrow validity. Local object intermediates use explicit
    null sentinels; untyped pandas carriers retain their ingress convention.
    """
    if values.dtype != object:
        return values.isna()
    return pd.Series(
        [value is None or value is pd.NA or value is pd.NaT for value in values],
        dtype=bool,
        index=values.index,
    )


def stage_arrow_type(dtype: DataType) -> pa.DataType:
    """Return the exact Arrow carrier for one current Query stage slot."""
    if isinstance(dtype, ScalarDataType):
        return {
            "boolean": pa.bool_(),
            "string": pa.string(),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "date": pa.date32(),
        }[dtype.name]
    if isinstance(dtype, DecimalDataType):
        return pa.decimal128(dtype.precision, dtype.scale)
    if isinstance(dtype, TimestampDataType):
        return pa.timestamp(dtype.unit, dtype.timezone)
    return pa.duration(dtype.unit)


def _stage_error(
    expected: str,
    received: str,
    repair: str = "Correct the source field type or stage layout; Marivo does not cast lossy values back.",
) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair=repair,
        stage="stage_output",
        help_target="analysis_frame.compute",
    )


def _type_name(dtype: DataType) -> str:
    if isinstance(dtype, ScalarDataType):
        return dtype.name
    if isinstance(dtype, DecimalDataType):
        return f"decimal({dtype.precision},{dtype.scale})"
    if isinstance(dtype, TimestampDataType):
        return f"timestamp({dtype.unit},{dtype.timezone})"
    return f"duration({dtype.unit})"


def _admits(source: pa.DataType, target: DataType) -> bool:
    if pa.types.is_null(source):
        return True
    if isinstance(target, ScalarDataType):
        if target.name == "boolean":
            return bool(pa.types.is_boolean(source))
        if target.name == "string":
            return bool(pa.types.is_string(source) or pa.types.is_large_string(source))
        if target.name == "int64":
            return bool(pa.types.is_integer(source))
        if target.name == "float64":
            return bool(pa.types.is_floating(source))
        return bool(pa.types.is_date(source) or pa.types.is_timestamp(source))
    if isinstance(target, DecimalDataType):
        return bool(
            pa.types.is_decimal(source)
            or (source == pa.uint64() and target.precision >= 20 and target.scale == 0)
        )
    if isinstance(target, TimestampDataType):
        return bool(pa.types.is_timestamp(source))
    return bool(pa.types.is_duration(source))


def _stage_column(series: pd.Series, column: DataColumn) -> pd.Series:
    try:
        values = pa.array(series, from_pandas=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
        raise _stage_error(
            f"{column.slot_id}: one typed {_type_name(column.dtype)} column",
            f"{series.dtype}: {error}",
        ) from None
    if not _admits(values.type, column.dtype):
        raise _stage_error(
            f"{column.slot_id}: a lossless {_type_name(column.dtype)} carrier",
            f"{column.slot_id}: {series.dtype} delivered for {_type_name(column.dtype)}",
        )
    if column.dtype == ScalarDataType("date") and pa.types.is_timestamp(values.type):
        dates = values.cast(pa.date32())
        if values.type.tz is not None or (
            not pc.all(pc.equal(values, dates.cast(values.type))).as_py()
            and values.null_count != len(values)
        ):
            raise _stage_error(
                f"{column.slot_id}: civil dates without time or timezone",
                f"{column.slot_id}: non-midnight or zoned timestamp values",
            )
    if (
        isinstance(column.dtype, TimestampDataType)
        and pa.types.is_timestamp(values.type)
        and values.type.tz != column.dtype.timezone
    ):
        raise _stage_error(
            f"{column.slot_id}: timezone {column.dtype.timezone}",
            f"{column.slot_id}: timezone {values.type.tz} delivered",
            "Explicitly unify naive and aware identity values in the Entity output before matching. TimeDimension parse does not rewrite primary_key; keep the exact source timestamp precision.",
        )
    if not column.nullable and values.null_count:
        raise _stage_error(
            f"{column.slot_id}: a nonnullable column without NA",
            f"{values.null_count} NA values",
        )
    try:
        exact = values.cast(stage_arrow_type(column.dtype), safe=True)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise _stage_error(
            f"{column.slot_id}: a lossless {_type_name(column.dtype)} conversion", str(error)
        ) from None
    return pd.Series(pd.arrays.ArrowExtensionArray(exact), name=column.slot_id)


def normalize_stage_columns(frame: pd.DataFrame, columns: tuple[DataColumn, ...]) -> pd.DataFrame:
    """Normalize exact carriers and column order without a relation-key obligation."""
    expected = tuple(column.slot_id for column in columns)
    if tuple(frame.columns) != expected:
        raise _stage_error(
            f"stage column order {expected!r}", repr(tuple(str(c) for c in frame.columns))
        )
    if not columns:
        return pd.DataFrame(index=pd.RangeIndex(len(frame)))
    if all(
        isinstance(frame[column.slot_id].dtype, pd.ArrowDtype)
        and frame[column.slot_id].dtype == pd.ArrowDtype(stage_arrow_type(column.dtype))
        for column in columns
    ):
        # Exact Arrow carriers already encode type, precision and timezone.
        # Retain validity checks and independent pandas wrappers, without
        # reconstructing every column through Arrow and Series again.
        for column in columns:
            if not column.nullable:
                nulls = pa.array(frame[column.slot_id]).null_count
                if nulls:
                    raise _stage_error(
                        f"{column.slot_id}: a nonnullable column without NA",
                        f"{nulls} NA values",
                    )
        result = frame.copy()
        result.index = pd.RangeIndex(len(frame))
        result.columns = pd.Index(expected)
        result.attrs = {}
        return result
    result = pd.DataFrame(
        {column.slot_id: _stage_column(frame[column.slot_id], column) for column in columns},
        columns=list(expected),
    )
    return result


def normalize_stage_frame(
    frame: pd.DataFrame,
    columns: tuple[DataColumn, ...],
    keys: tuple[str, ...],
    *,
    unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS,
) -> pd.DataFrame:
    """Return a new exact-typed stage frame; source and local outputs share this entry.

    Column order, dtype, timezone and NA are checked against planned slots.
    Complete keys are scanned only without exact producer or prior-check facts.
    Lossy conversions fail instead of being cast back afterward.
    """
    expected = tuple(column.slot_id for column in columns)
    result = normalize_stage_columns(frame, columns)
    if keys and not unique_keys.project(expected).proves(keys):
        _validate_stage_keys(result, keys)
    return result


def _validate_stage_keys(result: pd.DataFrame, keys: tuple[str, ...]) -> None:
    """Scan complete typed keys when no producer or prior check proves uniqueness."""
    arrays = tuple(pa.array(result[key], from_pandas=True) for key in keys)
    seen: set[tuple[pa.Scalar | None, ...]] = set()
    for cells in zip(*arrays, strict=True):
        # Arrow preserves valid minimum temporal ticks separately from null.
        # Keep pandas' repeated-NaN identity without conflating Arrow nulls.
        key = tuple(
            None
            if pa.types.is_floating(cell.type) and cell.is_valid and pc.is_nan(cell).as_py()
            else cell
            for cell in cells
        )
        if key in seen:
            raise _stage_error(
                f"a complete unique stage key {keys!r}", "null or duplicate key values"
            )
        seen.add(key)


def normalize_complete_frame(
    frame: pd.DataFrame,
    columns: tuple[DataColumn, ...],
    keys: tuple[str, ...],
    *,
    unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS,
) -> pd.DataFrame:
    """Validate exact carriers, layout and complete keys before consumption.

    Backend floating values retain their validity, including NaN and infinity;
    Arrow nulls remain distinct missing values.
    """
    if not keys and len(frame) > 1:
        raise _stage_error("at most one row for a zero-axis Frame", f"{len(frame)} rows")
    return normalize_stage_frame(frame, columns, keys, unique_keys=unique_keys)
