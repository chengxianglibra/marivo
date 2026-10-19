"""Pure finite calendar directories and nominal maps over closed typed data."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from typing import NoReturn, TypeAlias

import pandas as pd
import pyarrow as pa

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    NamedCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_structure import coordinate_attachments
from marivo.analysis.core.model import reject

FrozenScalar: TypeAlias = bool | str | int | float | date | Decimal
FrozenKey: TypeAlias = tuple[tuple[DataType, FrozenScalar | None], ...]
_BOUND_NAMES = ("nominal_start", "nominal_end", "actual_start", "actual_end")


def _fail(expected: str, received: str, *, match: bool = False) -> NoReturn:
    reject(
        expected,
        received,
        "Construct compatible complete source calendar buckets before matching."
        if match
        else "Retain the exact frozen coordinate descriptors and complete typed attachments.",
        "frame.calendar.match" if match else "frame.calendar.data",
    )


@dataclass(frozen=True, slots=True)
class FrozenAttachment:
    """A borrowed read-only table view; never a retained reader or execution object."""

    name: str
    schema: DataSchema
    rows: pd.DataFrame


@dataclass(frozen=True, slots=True)
class FrozenCalendarBucket:
    key: FrozenKey
    nominal_label: str
    ordinal: int
    nominal_start: datetime
    nominal_end: datetime
    actual_start: datetime
    actual_end: datetime


@dataclass(frozen=True, slots=True)
class FrozenCalendarDirectory:
    axis: CalendarDataAxis
    buckets: tuple[FrozenCalendarBucket, ...]


@dataclass(frozen=True, slots=True)
class FrozenBucketMatch:
    left_key: FrozenKey | None
    right_key: FrozenKey | None


def _calendar(axis: CalendarDataAxis) -> None:
    if type(axis) is not CalendarDataAxis or axis.frozen is None:
        _fail("a complete frozen calendar descriptor", repr(axis))
    if any(column.nullable for column in axis.columns):
        _fail("nonnullable complete calendar bucket keys", repr(axis.columns))


def calendar_directory_schema(axis: CalendarDataAxis) -> DataSchema:
    """Build the single calendar.v1 directory layout used by writers and readers."""
    _calendar(axis)
    values = (
        DataColumn("nominal_label", "nominal_label", ScalarDataType("string"), False),
        DataColumn("ordinal", "ordinal", ScalarDataType("int64"), False),
        *(DataColumn(name, name, TimestampDataType("us", "UTC"), False) for name in _BOUND_NAMES),
    )
    return DataSchema(
        (DataAxis(axis.token, axis.columns, name=axis.name, at=axis.at),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )


def paired_mapping_schema(axis: PairedDataAxis) -> DataSchema:
    """Build the complete pair key and nullable all-or-none endpoint key groups."""
    _calendar(axis.left)
    _calendar(axis.right)
    if any(column.nullable for column in axis.columns):
        _fail("nonnullable complete paired keys", repr(axis.columns))
    values = tuple(
        DataColumn(f"{side}.{i}", f"{side}.{i}", column.dtype, True, column.unit)
        for side, endpoint in (("left", axis.left), ("right", axis.right))
        for i, column in enumerate(endpoint.columns)
    )
    return DataSchema(
        (DataAxis(axis.token, axis.columns, name=axis.name, at=axis.at),),
        values,
        value_paths=tuple(
            (side, str(i))
            for side, endpoint in (("left", axis.left), ("right", axis.right))
            for i in range(len(endpoint.columns))
        ),
    )


def coordinate_attachment_schemas(schema: DataSchema) -> tuple[tuple[str, DataSchema], ...]:
    """Validate closed references and return exact required attachment layouts."""
    result: list[tuple[str, DataSchema]] = []
    required = coordinate_attachments(schema)
    tokens: set[str] = set()

    def visit(axis: DataAxis) -> None:
        if axis.token in tokens:
            return  # Exact calendar reuse was validated by coordinate_attachments.
        tokens.add(axis.token)
        if isinstance(axis, PairedDataAxis):
            visit(axis.left)
            visit(axis.right)
            result.append((axis.mapping_name, paired_mapping_schema(axis)))
        elif isinstance(axis, CalendarDataAxis):
            _calendar(axis)
            assert axis.frozen is not None
            result.append((axis.frozen.directory_name, calendar_directory_schema(axis)))
        elif axis.kind == "correspondence":
            _fail("a closed paired coordinate descriptor", repr(axis))

    for axis in schema.axes:
        visit(axis)
    assert tuple(name for name, _ in result) == required
    return tuple(result)


def _arrow_type(dtype: DataType) -> pa.DataType:
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


def _array(rows: pd.DataFrame, column: DataColumn) -> pa.Array:
    try:
        values = pa.array(rows[column.slot_id], from_pandas=True)
        expected = _arrow_type(column.dtype)
        # pandas' Arrow string extension uses large_string for the same string
        # type. No other physical conversion may erase exact units or precision.
        if values.type == pa.large_string() and expected == pa.string():
            values = values.cast(expected)
        if values.type != expected:
            _fail(
                "exact declared physical column type",
                f"{column.slot_id}: {values.type} != {expected}",
            )
        if values.null_count and not column.nullable:
            _fail("nonnull declared coordinate cells", column.slot_id)
        return values
    except (pa.ArrowException, TypeError, ValueError, OverflowError) as error:
        _fail("exact typed coordinate cells", f"{column.slot_id}: {error}")


def _layout(schema: DataSchema, rows: pd.DataFrame) -> None:
    if not isinstance(rows, pd.DataFrame) or tuple(rows.columns) != tuple(
        column.slot_id for column in schema.columns
    ):
        _fail("complete coordinate table in exact slot order", repr(tuple(rows.columns)))


def _scalar(values: pa.Array, index: int) -> FrozenScalar | None:
    if not values[index].is_valid:
        return None
    # Arrow validity, rather than pandas scalar NaT, is the null authority.
    value: object = values[index].as_py()
    if isinstance(value, float) and not isfinite(value):
        _fail("finite typed coordinate values", repr(value))
    if isinstance(value, (bool, str, int, float, date, Decimal)):
        return value
    _fail("a closed exact physical coordinate scalar", repr(value))


def frozen_key_rows(rows: pd.DataFrame, columns: tuple[DataColumn, ...]) -> tuple[FrozenKey, ...]:
    """Read dtype-tagged keys using Arrow validity and exact temporal ticks."""
    arrays: list[pa.Array] = []
    for column in columns:
        values = _array(rows, column)
        if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
            values = values.cast(pa.int64())
        elif column.dtype == ScalarDataType("date"):
            values = values.cast(pa.int32())
        arrays.append(values)
    return tuple(
        tuple(
            (column.dtype, _scalar(values, i))
            for column, values in zip(columns, arrays, strict=True)
        )
        for i in range(len(rows))
    )


def frozen_key_table(
    columns: tuple[DataColumn, ...], keys: tuple[FrozenKey | None, ...]
) -> pd.DataFrame:
    """Encode canonical physical keys without converting temporal ticks to scalars."""
    result: dict[str, pd.Series] = {}
    for i, column in enumerate(columns):
        if any(
            key is not None and (len(key) != len(columns) or key[i][0] != column.dtype)
            for key in keys
        ):
            _fail("canonical complete keys with exact declared types", repr(keys))
        values = tuple(None if key is None else key[i][1] for key in keys)
        physical = (
            pa.int64()
            if isinstance(column.dtype, (TimestampDataType, DurationDataType))
            else pa.int32()
            if column.dtype == ScalarDataType("date")
            else _arrow_type(column.dtype)
        )
        array = pa.array(values, type=physical).cast(_arrow_type(column.dtype))
        result[column.slot_id] = pd.Series(pd.arrays.ArrowExtensionArray(array))
    return pd.DataFrame(result, index=pd.RangeIndex(len(keys)))


def calendar_directory_rows(directory: FrozenCalendarDirectory) -> pd.DataFrame:
    """Encode immutable directory records using the shared calendar.v1 layout."""
    rows = frozen_key_table(
        directory.axis.columns, tuple(bucket.key for bucket in directory.buckets)
    )
    rows["nominal_label"] = pd.Series(
        [b.nominal_label for b in directory.buckets], dtype="string[pyarrow]"
    )
    rows["ordinal"] = pd.Series([b.ordinal for b in directory.buckets], dtype="int64[pyarrow]")
    for name, values in (
        ("nominal_start", tuple(b.nominal_start for b in directory.buckets)),
        ("nominal_end", tuple(b.nominal_end for b in directory.buckets)),
        ("actual_start", tuple(b.actual_start for b in directory.buckets)),
        ("actual_end", tuple(b.actual_end for b in directory.buckets)),
    ):
        rows[name] = pd.Series(values, dtype="timestamp[us, tz=UTC][pyarrow]")
    return rows


def _unique(keys: tuple[FrozenKey, ...], role: str) -> None:
    if len(set(keys)) != len(keys):
        _fail(f"unique complete {role} keys", "duplicate complete keys")


def _label(label: str, axis: CalendarDataAxis) -> None:
    assert axis.frozen is not None
    definition = axis.frozen.definition
    try:
        if isinstance(definition, NamedCalendarData):
            value = json.loads(label)
            if (
                type(value) not in (str, int, float, bool)
                or (isinstance(value, float) and not isfinite(value))
                or json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                != label
            ):
                raise ValueError("noncanonical scalar label")
        elif definition.unit in ("second", "minute", "hour"):
            stamp = datetime.fromisoformat(label)
            if stamp.utcoffset() is None or stamp.isoformat(timespec="microseconds") != label:
                raise ValueError("missing captured offset or microseconds")
        elif date.fromisoformat(label).isoformat() != label:
            raise ValueError("noncanonical civil date")
    except (ValueError, TypeError, OverflowError) as error:
        _fail("a canonical frozen nominal label", f"{label!r}: {error}")


def _datetime(values: pa.Array, i: int) -> datetime:
    try:
        value: object = values[i].as_py()
    except (OverflowError, ValueError) as error:
        _fail("representable frozen UTC microsecond bounds", str(error))
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        _fail("nonnull frozen UTC microsecond bounds", repr(value))
    return value.astimezone(timezone.utc)


def read_calendar_directory(
    axis: CalendarDataAxis, schema: DataSchema, rows: pd.DataFrame
) -> FrozenCalendarDirectory:
    """Validate every original directory record without requiring matchability."""
    if schema != calendar_directory_schema(axis):
        _fail("the exact calendar.v1 directory schema", repr(schema))
    _layout(schema, rows)
    arrays = {column.name: _array(rows, column) for column in schema.values}
    keys = frozen_key_rows(rows, axis.columns)
    _unique(keys, "calendar bucket")
    buckets: list[FrozenCalendarBucket] = []
    for i, key in enumerate(keys):
        label: object = arrays["nominal_label"][i].as_py()
        ordinal: object = arrays["ordinal"][i].as_py()
        if not isinstance(label, str) or type(ordinal) is not int:
            _fail("nonnull labels and int64 ordinals", repr((label, ordinal)))
        _label(label, axis)
        start, end, actual_start, actual_end = tuple(
            _datetime(arrays[name], i) for name in _BOUND_NAMES
        )
        if not start <= actual_start < actual_end <= end:
            _fail(
                "valid contained half-open nominal and actual bounds",
                repr((start, end, actual_start, actual_end)),
            )
        buckets.append(
            FrozenCalendarBucket(key, label, ordinal, start, end, actual_start, actual_end)
        )
    labels = tuple(bucket.nominal_label for bucket in buckets)
    ordinals = tuple(bucket.ordinal for bucket in buckets)
    if len(set(labels)) != len(labels):
        _fail("unique nominal labels", repr(labels))
    if sorted(ordinals) != list(range(len(buckets))):
        _fail("the complete original ordinal sequence 0..n-1", repr(ordinals))
    return FrozenCalendarDirectory(axis, tuple(sorted(buckets, key=lambda bucket: bucket.ordinal)))


def _endpoint_keys(
    rows: pd.DataFrame, columns: tuple[DataColumn, ...]
) -> tuple[FrozenKey | None, ...]:
    keys = frozen_key_rows(rows, columns)
    result: list[FrozenKey | None] = []
    for key in keys:
        missing = tuple(value is None for _, value in key)
        if any(missing) and not all(missing):
            _fail("all-or-none complete endpoint keys", repr(key))
        result.append(None if all(missing) else key)
    return tuple(result)


def validate_paired_mapping(
    axis: PairedDataAxis,
    schema: DataSchema,
    rows: pd.DataFrame,
    left: FrozenCalendarDirectory,
    right: FrozenCalendarDirectory,
) -> None:
    """Validate nominal bucket endpoints independently from primary row presence."""
    if schema != paired_mapping_schema(axis) or (left.axis, right.axis) != (axis.left, axis.right):
        _fail("the exact paired mapping schema and endpoint directories", repr(schema))
    _layout(schema, rows)
    _unique(frozen_key_rows(rows, axis.columns), "paired")
    n = len(axis.left.columns)
    endpoints = (_endpoint_keys(rows, schema.values[:n]), _endpoint_keys(rows, schema.values[n:]))
    if any(a is None and b is None for a, b in zip(*endpoints, strict=True)):
        _fail("paired endpoints that are not both absent", "both absent")
    for keys, directory in zip(endpoints, (left, right), strict=True):
        present = tuple(key for key in keys if key is not None)
        _unique(present, "one-to-one endpoint")
        domain = {bucket.key for bucket in directory.buckets}
        if any(key not in domain for key in present):
            _fail("endpoint keys resolving in their frozen directory", repr(present))


def validate_frozen_coordinates(
    schema: DataSchema, rows: pd.DataFrame, attachments: tuple[FrozenAttachment, ...]
) -> None:
    """Validate complete coordinate closure, all attachments and primary references."""
    layouts = coordinate_attachment_schemas(schema)
    names = tuple(attachment.name for attachment in attachments)
    if len(set(names)) != len(names) or set(names) != {name for name, _ in layouts}:
        _fail("exactly the required unique coordinate attachments", repr(names))
    tables = {attachment.name: attachment for attachment in attachments}
    for name, expected in layouts:
        if tables[name].schema != expected:
            _fail("the exact required coordinate attachment schema", name)
    _layout(schema, rows)
    _unique(frozen_key_rows(rows, tuple(c for axis in schema.axes for c in axis.columns)), "row")

    def read(axis: CalendarDataAxis) -> FrozenCalendarDirectory:
        assert axis.frozen is not None
        attachment = tables[axis.frozen.directory_name]
        return read_calendar_directory(axis, attachment.schema, attachment.rows)

    for axis in schema.axes:
        if isinstance(axis, PairedDataAxis):
            left, right = read(axis.left), read(axis.right)
            table = tables[axis.mapping_name]
            validate_paired_mapping(axis, table.schema, table.rows, left, right)
            domain = set(frozen_key_rows(table.rows, axis.columns))
        elif isinstance(axis, CalendarDataAxis):
            domain = {bucket.key for bucket in read(axis).buckets}
        else:
            continue
        primary = frozen_key_rows(rows, axis.columns)
        if any(key not in domain for key in primary):
            _fail("every primary coordinate key resolving in its frozen attachment", axis.token)
        if isinstance(axis, PairedDataAxis):
            table = tables[axis.mapping_name]
            positions = {key: i for i, key in enumerate(frozen_key_rows(table.rows, axis.columns))}
            width = len(axis.left.columns)
            for side, columns in (
                ("left", table.schema.values[:width]),
                ("right", table.schema.values[width:]),
            ):
                path = (f"{side}_present",)
                if path not in schema.presence_paths:
                    continue  # Reduce removes row presence while keeping the pair map.
                present = _array(rows, schema.presence[schema.presence_paths.index(path)])
                endpoints = _endpoint_keys(table.rows, columns)
                if any(
                    endpoints[positions[key]] is None and present[i].as_py() is True
                    for i, key in enumerate(primary)
                ):
                    _fail("row presence consistent with absent calendar endpoints", side)


def require_matchable_calendar_directory(directory: FrozenCalendarDirectory) -> None:
    """Require a full nominal partition and full actual coverage of every bucket."""
    for bucket in directory.buckets:
        if (bucket.actual_start, bucket.actual_end) != (bucket.nominal_start, bucket.nominal_end):
            _fail(
                "complete actual coverage of every nominal bucket", bucket.nominal_label, match=True
            )
    for before, after in zip(directory.buckets, directory.buckets[1:], strict=False):
        if before.nominal_end != after.nominal_start:
            _fail(
                "a continuous nonoverlapping nominal partition",
                repr((before.nominal_label, after.nominal_label)),
                match=True,
            )


def require_compatible_calendar_axes(left: CalendarDataAxis, right: CalendarDataAxis) -> None:
    """Validate captured interpretation without reading rows or reconstructing rules."""
    _calendar(left)
    _calendar(right)
    assert left.frozen is not None and right.frozen is not None
    if (left.frozen.definition, left.frozen.rule_version, left.timezone, left.at) != (
        right.frozen.definition,
        right.frozen.rule_version,
        right.timezone,
        right.at,
    ) or tuple(c.dtype for c in left.columns) != tuple(c.dtype for c in right.columns):
        _fail(
            "compatible frozen calendar interpretations and complete key types",
            repr((left, right)),
            match=True,
        )


def require_calendar_match_rule(axis: CalendarDataAxis, rule: CalendarCorrespondence) -> None:
    """Check static rule applicability using only the frozen descriptor."""
    _calendar(axis)
    assert axis.frozen is not None
    definition = axis.frozen.definition
    if type(rule) is not CalendarCorrespondence:
        _fail("a closed calendar correspondence rule", repr(rule), match=True)
    if rule.kind != "ordinal" and (
        not isinstance(definition, BuiltinCalendarData)
        or definition.unit
        not in (
            ("day", "month", "quarter", "year")
            if rule.kind == "previous_year"
            else ("day", "week", "month", "quarter", "year")
        )
    ):
        _fail("supported builtin civil grains for this match rule", repr(definition), match=True)


def _shift(label: str, definition: BuiltinCalendarData, rule: CalendarCorrespondence) -> str | None:
    current = date.fromisoformat(label)
    try:
        if rule.kind == "previous_year":
            return current.replace(year=current.year - 1).isoformat()
        count = definition.step * rule.by
        if definition.unit in ("day", "week"):
            return (
                current + timedelta(days=count * (7 if definition.unit == "week" else 1))
            ).isoformat()
        months = count * {"month": 1, "quarter": 3, "year": 12}[definition.unit]
        year, month = divmod(current.year * 12 + current.month - 1 + months, 12)
        return current.replace(year=year, month=month + 1).isoformat()
    except (ValueError, OverflowError):
        return None


def build_partial_calendar_map(
    left: FrozenCalendarDirectory,
    right: FrozenCalendarDirectory,
    rule: CalendarCorrespondence,
) -> tuple[FrozenBucketMatch, ...]:
    """Map full nominal directories before any actual-row exact/outer policy."""
    require_compatible_calendar_axes(left.axis, right.axis)
    require_matchable_calendar_directory(left)
    require_matchable_calendar_directory(right)
    assert left.axis.frozen is not None
    definition = left.axis.frozen.definition
    require_calendar_match_rule(left.axis, rule)
    ordinals = {bucket.ordinal: bucket for bucket in right.buckets}
    labels = {bucket.nominal_label: bucket for bucket in right.buckets}
    result: list[FrozenBucketMatch] = []
    used: set[FrozenKey] = set()
    for bucket in left.buckets:
        if rule.kind == "ordinal":
            partner = ordinals.get(bucket.ordinal)
        else:
            assert isinstance(definition, BuiltinCalendarData)
            target = _shift(bucket.nominal_label, definition, rule)
            partner = labels.get(target) if target is not None else None
        if partner is not None:
            if partner.key in used:
                _fail("a one-to-one nominal calendar map", repr(partner.key), match=True)
            used.add(partner.key)
        result.append(FrozenBucketMatch(bucket.key, None if partner is None else partner.key))
    result.extend(
        FrozenBucketMatch(None, bucket.key) for bucket in right.buckets if bucket.key not in used
    )
    return tuple(result)


def merge_equal_calendar_directories(
    left: FrozenCalendarDirectory,
    right: FrozenCalendarDirectory,
    output_axis: CalendarDataAxis,
) -> FrozenCalendarDirectory:
    """Merge equal interpretation without renumbering keys, labels or ordinals."""
    require_compatible_calendar_axes(left.axis, right.axis)
    require_compatible_calendar_axes(left.axis, output_axis)
    by_key = {bucket.key: bucket for bucket in left.buckets}
    by_label = {bucket.nominal_label: bucket for bucket in left.buckets}
    by_ordinal = {bucket.ordinal: bucket for bucket in left.buckets}
    for bucket in right.buckets:
        for old in (
            by_key.get(bucket.key),
            by_label.get(bucket.nominal_label),
            by_ordinal.get(bucket.ordinal),
        ):
            if old is not None and old != bucket:
                _fail(
                    "equal frozen buckets without directory conflicts",
                    repr((old, bucket)),
                    match=True,
                )
        by_key[bucket.key] = bucket
        by_label[bucket.nominal_label] = bucket
        by_ordinal[bucket.ordinal] = bucket
    buckets = tuple(sorted(by_key.values(), key=lambda bucket: bucket.ordinal))
    if tuple(bucket.ordinal for bucket in buckets) != tuple(range(len(buckets))):
        _fail(
            "merged original ordinals without conflicts or renumbering", repr(buckets), match=True
        )
    return replace(left, axis=output_axis, buckets=buckets)
