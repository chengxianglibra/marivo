"""Typed invocation-local pandas values exchanged between Query stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.physical import stage_arrow_type

if TYPE_CHECKING:
    from marivo.analysis.materialization.query_components import ComponentFrame


_NORMALIZED = object()


def _stage_error(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Correct the source field type or stage layout; Marivo does not cast lossy values back.",
        stage="stage_output",
        help_target="query.execute",
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
        return bool(pa.types.is_decimal(source))
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


def source_columns(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...], keys: tuple[str, ...] = ()
) -> tuple[DataColumn, ...]:
    """Resolve native numerical carriers once at the pandas stage boundary."""
    from dataclasses import replace

    result: list[DataColumn] = []
    for column in columns:
        numeric = isinstance(column.dtype, DecimalDataType) or column.dtype in (
            ScalarDataType("int64"),
            ScalarDataType("float64"),
        )
        if not numeric or column.slot_id not in frame.columns:
            result.append(column)
            continue
        try:
            values = pa.array(frame[column.slot_id], from_pandas=True)
        except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError) as error:
            raise _stage_error(f"{column.slot_id}: a native numeric column", str(error)) from None
        dtype: DataType = column.dtype
        if pa.types.is_floating(values.type):
            dtype = ScalarDataType("float64")
        elif pa.types.is_integer(values.type):
            dtype = ScalarDataType("int64")
        elif pa.types.is_decimal(values.type):
            dtype = DecimalDataType(
                max(
                    values.type.precision,
                    column.dtype.precision if isinstance(column.dtype, DecimalDataType) else 0,
                ),
                values.type.scale,
            )
        elif not pa.types.is_null(values.type):
            raise _stage_error(f"{column.slot_id}: a native numeric column", str(values.type))
        if (
            column.slot_id in keys
            and column.dtype == ScalarDataType("int64")
            and dtype == ScalarDataType("float64")
            and pc.any(
                pc.or_(
                    pc.greater_equal(pc.abs(values), 2**53),
                    pc.not_equal(values, pc.floor(values)),
                )
            ).as_py()
        ):
            raise _stage_error(
                f"{column.slot_id}: complete integer keys preserved by the native carrier",
                "floating keys outside the unambiguous integer range or containing fractions",
            )
        result.append(replace(column, dtype=dtype))
    return tuple(result)


def normalize_stage_frame(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> pd.DataFrame:
    """Return a new exact-typed stage frame; source and local outputs share this entry.

    Column order, complete keys, dtype, timezone and NA are checked against the
    planned slots. Lossy conversions fail instead of being cast back afterward.
    """
    expected = tuple(column.slot_id for column in columns)
    if tuple(frame.columns) != expected:
        raise _stage_error(
            f"stage column order {expected!r}", repr(tuple(str(c) for c in frame.columns))
        )
    nonnullable_keys = [
        column.slot_id for column in columns if column.slot_id in keys and not column.nullable
    ]
    if keys and (
        frame.duplicated(list(keys)).any() or frame.loc[:, nonnullable_keys].isna().to_numpy().any()
    ):
        raise _stage_error(f"a complete unique stage key {keys!r}", "null or duplicate key values")
    if not columns:
        return pd.DataFrame(index=pd.RangeIndex(len(frame)))
    return pd.DataFrame(
        {column.slot_id: _stage_column(frame[column.slot_id], column) for column in columns},
        columns=list(expected),
    )


def normalize_complete_frame(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> pd.DataFrame:
    """Validate complete data before any consumer can hide invalid cells.

    Execution stages may carry unchecked values until their owning checks run;
    complete data cannot retain nonfinite floating values. Arrow nulls remain NA.
    """
    result = normalize_stage_frame(frame, columns, keys)
    for column in columns:
        if column.dtype == ScalarDataType("float64"):
            values = pa.array(result[column.slot_id], from_pandas=True)
            if pc.any(pc.invert(pc.is_finite(values))).as_py():
                raise _stage_error(
                    f"{column.slot_id}: finite float64 values or NA",
                    "nonfinite floating values",
                )
    return result


@dataclass(frozen=True, slots=True)
class CheckReceipt:
    """One completed check tied to its exact Query stage."""

    check_id: str
    stage_id: str


@dataclass(frozen=True, slots=True, eq=False)
class StageOutput:
    """Invocation-local typed pandas stage data; build only through ``from_frame``."""

    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    table: pd.DataFrame
    receipts: tuple[CheckReceipt, ...] = ()
    _token: object = None
    key_groups: tuple[tuple[str, ...], ...] = ()
    layout: ComparisonEndpoint | None = None
    components: tuple[ComponentFrame, ...] = ()
    population_frames: tuple[pd.DataFrame, ...] = ()
    source_rows: int | None = None
    frame_attachments: tuple[OutputAttachment, ...] = ()

    def __post_init__(self) -> None:
        if self._token is not _NORMALIZED:
            raise _stage_error(
                "a normalized stage frame from StageOutput.from_frame",
                "an unnormalized stage table",
            )

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        columns: tuple[DataColumn, ...],
        keys: tuple[str, ...],
        receipts: tuple[CheckReceipt, ...] = (),
        key_groups: tuple[tuple[str, ...], ...] = (),
        layout: ComparisonEndpoint | None = None,
        components: tuple[ComponentFrame, ...] = (),
        population_frames: tuple[pd.DataFrame, ...] = (),
        source_rows: int | None = None,
        frame_attachments: tuple[OutputAttachment, ...] = (),
    ) -> StageOutput:
        return cls(
            columns,
            keys,
            normalize_stage_frame(frame, columns, keys),
            receipts,
            _NORMALIZED,
            key_groups,
            layout,
            components,
            population_frames,
            source_rows,
            frame_attachments,
        )

    @classmethod
    def from_source_frame(
        cls,
        frame: pd.DataFrame,
        columns: tuple[DataColumn, ...],
        keys: tuple[str, ...],
        *,
        components: tuple[ComponentFrame, ...] = (),
        value_keys: tuple[str, ...] = (),
        source_rows: int | None = None,
    ) -> StageOutput:
        """Accept native source numeric types without casting back to declarations."""
        effective = source_columns(
            frame, columns, tuple(key for key in keys if key not in value_keys)
        )
        return cls.from_frame(
            frame, effective, keys, components=components, source_rows=source_rows
        )
