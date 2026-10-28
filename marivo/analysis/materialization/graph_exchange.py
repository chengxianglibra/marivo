"""Typed invocation-local pandas values exchanged between Query stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.core.artifact_data import DataSchema, OutputAttachment
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    ScalarDataType,
)
from marivo.analysis.core.data_validation import (
    _stage_error,
    normalize_stage_frame,
)

if TYPE_CHECKING:
    from marivo.analysis.compiler.attribute_frequencies import (
        AttributeFrequencyCapture,
        AttributeStateCapture,
    )
    from marivo.analysis.core.attribute import AttributeMethodPayload
    from marivo.analysis.core.frame_data import CompletedFrameData
    from marivo.analysis.materialization.query_components import AttributeRootInputs, ComponentFrame


_NORMALIZED = object()


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
            dtype = (
                column.dtype
                if isinstance(column.dtype, DecimalDataType) and values.type == pa.uint64()
                else ScalarDataType("int64")
            )
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


@dataclass(frozen=True, slots=True)
class CheckReceipt:
    """One completed check tied to its exact Query stage."""

    check_id: str
    stage_id: str


@dataclass(frozen=True, slots=True, eq=False)
class StageOutput:
    """Invocation-local typed data, normalized on entry or copied from completed data."""

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
    attribute_inputs: tuple[AttributeRootInputs, ...] = ()
    attribute_methods: tuple[AttributeMethodPayload, ...] = ()
    frame_schema: DataSchema | None = None
    attribute_frequencies: tuple[AttributeFrequencyCapture, ...] = ()
    attribute_state_captures: tuple[AttributeStateCapture, ...] = ()

    def __post_init__(self) -> None:
        if self._token is not _NORMALIZED:
            raise _stage_error(
                "a normalized stage frame from StageOutput.from_frame",
                "an unnormalized stage table",
            )
        if self.frame_schema is not None and (
            self.frame_schema.columns != self.columns or self.frame_schema.key_fields != self.keys
        ):
            raise _stage_error(
                "completed Frame schema matching exact stage columns and keys",
                repr(self.frame_schema),
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
        attribute_inputs: tuple[AttributeRootInputs, ...] = (),
        attribute_methods: tuple[AttributeMethodPayload, ...] = (),
        frame_schema: DataSchema | None = None,
        attribute_frequencies: tuple[AttributeFrequencyCapture, ...] = (),
        attribute_state_captures: tuple[AttributeStateCapture, ...] = (),
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
            attribute_inputs,
            attribute_methods,
            frame_schema,
            attribute_frequencies,
            attribute_state_captures,
        )

    @classmethod
    def from_completed(cls, data: CompletedFrameData) -> StageOutput:
        """Copy already validated immutable data without repeating its integrity scans."""
        schema = data.schema
        return cls(
            schema.columns,
            schema.key_fields,
            data.copy_rows(),
            _token=_NORMALIZED,
            key_groups=tuple(tuple(c.slot_id for c in axis.columns) for axis in schema.axes),
            frame_attachments=data.copy_attachments(),
            frame_schema=schema,
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
