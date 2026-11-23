"""Admit physical Arrow types and carry their exact identities into source schemas."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.query_stages import SourceField
from marivo.analysis.model.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.datasource.spi import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    physical_fields,
)


def _preparation_error(
    expected: str,
    received: str,
    repair: str = "Use a supported physical field type while preserving the requested key, time and unit contract; no result was completed or published.",
) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair=repair,
        stage="execute_preparation",
        help_target="analysis_frame.compute",
    )


def _validate_timestamp_precision(field: pa.Field, path: str) -> None:
    original_unit = (field.metadata or {}).get(TIMESTAMP_UNIT_METADATA_KEY)
    if original_unit is None:
        return
    physical = field.type
    units = (b"s", b"ms", b"us", b"ns")
    if not pa.types.is_timestamp(physical) or original_unit not in units:
        raise _preparation_error("a timestamp with an exact source unit", repr(field))
    if units.index(original_unit) > units.index(physical.unit.encode("ascii")):
        raise _preparation_error(
            f"{path}: a lossless timestamp[{original_unit.decode('ascii')}] source carrier",
            f"{path}: {physical} loses source precision",
            f"Expose {path} through a source or Entity projection retaining the original timestamp unit; do not truncate identity values. No result was completed or published.",
        )


def admit_source_type(field: pa.Field, path: str) -> DataType:
    """Admit one bound source type family before reading native pandas values."""
    physical = field.type
    _validate_timestamp_precision(field, path)
    unit = (field.metadata or {}).get(DURATION_UNIT_METADATA_KEY)
    if unit is not None:
        if physical != pa.int64() or unit not in (b"s", b"ms", b"us", b"ns"):
            raise _preparation_error("int64 ticks with a fixed Duration unit", repr(field))
        return {
            b"s": DurationDataType("s"),
            b"ms": DurationDataType("ms"),
            b"us": DurationDataType("us"),
            b"ns": DurationDataType("ns"),
        }[unit]
    if pa.types.is_signed_integer(physical) or physical in (pa.uint8(), pa.uint16(), pa.uint32()):
        return ScalarDataType("int64")
    if pa.types.is_floating(physical):
        return ScalarDataType("float64")
    if pa.types.is_boolean(physical):
        return ScalarDataType("boolean")
    if pa.types.is_string(physical) or pa.types.is_large_string(physical):
        return ScalarDataType("string")
    if physical == pa.date32():
        return ScalarDataType("date")
    if pa.types.is_decimal128(physical):
        return DecimalDataType(physical.precision, physical.scale)
    if pa.types.is_timestamp(physical):
        return TimestampDataType(physical.unit, physical.tz)
    if pa.types.is_duration(physical):
        return DurationDataType(physical.unit)
    raise _preparation_error(
        f"{path}: a column type in the supported stage type families",
        f"{path}: {physical}",
        (
            f"Expose {path} as Decimal(20,0) in the source view or Entity body with raw[{path.rsplit('.', 1)[-1]!r}].cast('decimal(20,0)'); UInt64 requires an exact Decimal carrier, never float or unchecked int64. No result was completed or published."
            if physical == pa.uint64()
            else f"Project {path} to a supported scalar type in the Entity body using an inline cast such as raw['column'].cast('string'), preserving its identity, time and unit meaning. Read marivo.help('semantic.entity.define'). No result was completed or published."
        ),
    )


def _admitted(schema: pa.Schema, path: str, column: str) -> SourceField:
    index = schema.get_field_index(column)
    if index < 0:
        raise _preparation_error(
            f"{path}: column {column!r} on the bound source", repr(schema.names)
        )
    item = schema.field(index)
    return SourceField(path, column, admit_source_type(item, path), item.nullable)


def _bound_physical_field(physical: ops.Field, bound: BoundSource) -> pa.Field | None:
    """Recover captured carrier metadata through the source's declared projection."""
    for captured in bound.facts.schema:
        if physical in physical_fields(bound.relation[captured.name]):
            return captured
    return None


def _population_field(
    table: ir.Table, column: str, bounds: Mapping[str, BoundSource]
) -> SourceField:
    """Restore fixed Duration metadata through selected attribute projections."""
    admitted = _admitted(table.schema().to_pyarrow(), column, column)
    if admitted.dtype != ScalarDataType("int64"):
        return admitted
    duration: DurationDataType | None = None
    for physical in physical_fields(table[column]):
        for bound in bounds.values():
            field = _bound_physical_field(physical, bound)
            if field is None:
                continue
            if DURATION_UNIT_METADATA_KEY not in (field.metadata or {}):
                continue
            dtype = admit_source_type(field, column)
            assert isinstance(dtype, DurationDataType)
            if duration is not None and duration != dtype:
                raise _preparation_error("one exact Duration unit", column)
            duration = dtype
    return replace(admitted, dtype=duration) if duration is not None else admitted
