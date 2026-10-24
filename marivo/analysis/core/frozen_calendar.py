"""Pure finite calendar directories and nominal maps over closed typed data."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from typing import Literal, NoReturn, TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._temporal_grid import (
    CivilShiftResult,
    CivilShiftTarget,
    builtin_nominal_ordinal,
    civil_shift,
)
from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
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
_CIVIL_NAMES = tuple(f"{name}_civil" for name in _BOUND_NAMES)


def _fail(expected: str, received: str, *, match: bool = False) -> NoReturn:
    reject(
        expected,
        received,
        "Construct compatible complete time coordinates before matching."
        if match
        else "Retain the exact frozen coordinate descriptors and complete typed attachments.",
        "frame.coordinates.match" if match else "frame.coordinates.data",
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
    nominal_ordinal: int
    nominal_start_civil: str
    nominal_end_civil: str
    actual_start_civil: str
    actual_end_civil: str


@dataclass(frozen=True, slots=True)
class FrozenCalendarDirectory:
    axis: CalendarDataAxis
    buckets: tuple[FrozenCalendarBucket, ...]


@dataclass(frozen=True, slots=True)
class FrozenBucketMatch:
    left_key: FrozenKey | None
    right_key: FrozenKey | None


@dataclass(frozen=True, slots=True)
class FrozenDirectMatch:
    origin_key: FrozenKey
    target_key: FrozenKey


@dataclass(frozen=True, slots=True)
class FrozenDirectAbsent:
    origin_key: FrozenKey
    reason: Literal["period_absent", "cutoff_absent"]


FrozenDirectEntry: TypeAlias = FrozenDirectMatch | FrozenDirectAbsent


def direct_mapping_schema(axis: CalendarDataAxis) -> DataSchema:
    """Build one origin-key row with a target group or a closed no-match reason."""
    _calendar(axis)
    assert axis.frozen is not None and axis.frozen.direct is not None
    origin = axis.frozen.direct.axis
    values = (
        *(
            DataColumn(f"target.{i}", f"target.{i}", column.dtype, True, column.unit)
            for i, column in enumerate(axis.columns)
        ),
        DataColumn("reason", "reason", ScalarDataType("string"), True),
    )
    return DataSchema(
        (DataAxis(origin.token, origin.columns, name=origin.name, at=origin.at),),
        values,
        value_paths=(*(("target", str(i)) for i in range(len(axis.columns))), ("reason",)),
    )


def read_direct_mapping(
    axis: CalendarDataAxis,
    schema: DataSchema,
    rows: pd.DataFrame,
    origin: FrozenCalendarDirectory,
    target: FrozenCalendarDirectory,
) -> tuple[FrozenDirectEntry, ...]:
    """Validate complete direct coverage; missing data never means no correspondence."""
    assert axis.frozen is not None and axis.frozen.direct is not None
    if (
        schema != direct_mapping_schema(axis)
        or origin.axis != axis.frozen.direct.axis
        or target.axis != axis
    ):
        _fail("the exact direct mapping layout and endpoint directories", repr(schema))
    for directory in (origin, target):
        if any(
            before.nominal_end > after.nominal_start
            or before.nominal_ordinal >= after.nominal_ordinal
            for before, after in zip(directory.buckets, directory.buckets[1:], strict=False)
        ):
            _fail("ordered nonoverlapping direct endpoint selections", repr(directory.buckets))
    _layout(schema, rows)
    origins = frozen_key_rows(rows, origin.axis.columns)
    expected = tuple(bucket.key for bucket in origin.buckets)
    if origins != expected:
        _fail("exactly one ordered direct mapping row per origin bucket", repr(origins))
    columns = schema.values[:-1]
    arrays = tuple(_array(rows, column) for column in columns)
    target_keys = _key_rows(columns, arrays, len(rows))
    reasons = _array(rows, schema.values[-1])
    result: list[FrozenDirectEntry] = []
    targets: set[FrozenKey] = set()
    for i, key in enumerate(origins):
        nulls = tuple(not array[i].is_valid for array in arrays)
        reason: object = reasons[i].as_py()
        if all(nulls):
            if reason not in ("period_absent", "cutoff_absent"):
                _fail("a closed no-match reason for an absent direct target", repr(reason))
            assert reason == "period_absent" or reason == "cutoff_absent"
            result.append(FrozenDirectAbsent(key, reason))
        elif any(nulls) or reason is not None:
            _fail("one complete target key or one absent reason", repr((nulls, reason)))
        else:
            target_key = target_keys[i]
            if target_key in targets:
                _fail("unique direct target keys", repr(target_key))
            targets.add(target_key)
            result.append(FrozenDirectMatch(key, target_key))
    if targets != {bucket.key for bucket in target.buckets}:
        _fail("direct targets covering exactly the selected target directory", repr(targets))
    return tuple(result)


def _calendar(axis: CalendarDataAxis) -> None:
    if type(axis) is not CalendarDataAxis or axis.frozen is None:
        _fail("a complete frozen calendar descriptor", repr(axis))
    if any(column.nullable for column in axis.columns):
        _fail("nonnullable complete calendar bucket keys", repr(axis.columns))


def calendar_directory_schema(axis: CalendarDataAxis) -> DataSchema:
    """Build the calendar.v2 directory layout used by writers and readers."""
    _calendar(axis)
    values = (
        DataColumn("nominal_label", "nominal_label", ScalarDataType("string"), False),
        DataColumn("ordinal", "ordinal", ScalarDataType("int64"), False),
        DataColumn("nominal_ordinal", "nominal_ordinal", ScalarDataType("int64"), False),
        *(DataColumn(name, name, TimestampDataType("us", "UTC"), False) for name in _BOUND_NAMES),
        *(DataColumn(name, name, ScalarDataType("string"), False) for name in _CIVIL_NAMES),
    )
    return DataSchema(
        (DataAxis(axis.token, axis.columns, name=axis.name, at=axis.at),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )


@dataclass(frozen=True, slots=True)
class FrozenIntervalBucket:
    key: FrozenKey
    start: datetime
    end: datetime
    timezone: str


@dataclass(frozen=True, slots=True)
class FrozenIntervalDirectory:
    axis: IntervalDataAxis
    buckets: tuple[FrozenIntervalBucket, ...]


@dataclass(frozen=True, slots=True)
class FrozenComparisonMatch:
    current_key: FrozenKey
    baseline_key: FrozenKey


@dataclass(frozen=True, slots=True)
class FrozenComparisonAbsent:
    current_key: FrozenKey
    reason: Literal["period_absent", "cutoff_absent", "unmatched"]


FrozenComparisonEntry: TypeAlias = FrozenComparisonMatch | FrozenComparisonAbsent


def comparison_mapping_schema(
    current: CalendarDataAxis | IntervalDataAxis,
    binding: CalendarComparisonBinding | IntervalComparisonBinding,
) -> DataSchema:
    """Build one current-key row with a nullable complete baseline group or reason."""
    if current.token != binding.current_axis_token or type(current) is not type(binding.baseline):
        _fail(
            "the exact authoritative current time axis for a consumed comparison map", repr(binding)
        )
    values = (
        *(
            DataColumn(f"baseline.{i}", f"baseline.{i}", column.dtype, True, column.unit)
            for i, column in enumerate(binding.baseline.columns)
        ),
        DataColumn("reason", "reason", ScalarDataType("string"), True),
    )
    return DataSchema(
        (DataAxis(current.token, current.columns, name=current.name, at=current.at),),
        values,
        value_paths=(
            *(("baseline", str(i)) for i in range(len(binding.baseline.columns))),
            ("reason",),
        ),
    )


def read_comparison_mapping(
    binding: CalendarComparisonBinding | IntervalComparisonBinding,
    schema: DataSchema,
    rows: pd.DataFrame,
    current: FrozenCalendarDirectory | FrozenIntervalDirectory,
    baseline: FrozenCalendarDirectory | FrozenIntervalDirectory,
) -> tuple[FrozenComparisonEntry, ...]:
    """Validate current coverage and partial one-to-one baseline references."""
    if (
        schema != comparison_mapping_schema(current.axis, binding)
        or baseline.axis != binding.baseline
    ):
        _fail("the exact consumed comparison map layout and endpoints", repr(schema))
    _layout(schema, rows)
    keys = frozen_key_rows(rows, current.axis.columns)
    if keys != tuple(bucket.key for bucket in current.buckets):
        _fail("exactly one ordered comparison map row per current bucket", repr(keys))
    columns = schema.values[:-1]
    arrays = tuple(_array(rows, column) for column in columns)
    target_keys = _key_rows(columns, arrays, len(rows))
    reasons = _array(rows, schema.values[-1])
    domain = {bucket.key for bucket in baseline.buckets}
    seen: set[FrozenKey] = set()
    result: list[FrozenComparisonEntry] = []
    for index, key in enumerate(keys):
        nulls = tuple(not array[index].is_valid for array in arrays)
        reason: object = reasons[index].as_py()
        if all(nulls):
            if reason not in ("period_absent", "cutoff_absent", "unmatched"):
                _fail("a closed absent reason for an unmapped comparison bucket", repr(reason))
            assert reason == "period_absent" or reason == "cutoff_absent" or reason == "unmatched"
            result.append(FrozenComparisonAbsent(key, reason))
        elif any(nulls) or reason is not None:
            _fail(
                "one complete baseline key or one absent comparison reason", repr((nulls, reason))
            )
        else:
            target = target_keys[index]
            if target not in domain or target in seen:
                _fail("unique comparison targets resolving in the baseline directory", repr(target))
            seen.add(target)
            result.append(FrozenComparisonMatch(key, target))
    return tuple(result)


def interval_directory_schema(axis: IntervalDataAxis) -> DataSchema:
    """Build the exact interval.v1 finite directory layout."""
    if (
        len(axis.columns) != 1
        or axis.columns[0].dtype != ScalarDataType("int64")
        or axis.columns[0].nullable
    ):
        _fail("one nonnullable int64 interval key", repr(axis.columns))
    values = (
        DataColumn("start", "start", TimestampDataType("us", "UTC"), False),
        DataColumn("end", "end", TimestampDataType("us", "UTC"), False),
        DataColumn("timezone", "timezone", ScalarDataType("string"), False),
    )
    return DataSchema(
        (DataAxis(axis.token, axis.columns, name=axis.name, at=axis.at),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )


def interval_directory_rows(directory: FrozenIntervalDirectory) -> pd.DataFrame:
    """Encode all declared intervals independently of the current primary rows."""
    rows = frozen_key_table(
        directory.axis.columns, tuple(bucket.key for bucket in directory.buckets)
    )
    for name in ("start", "end"):
        rows[name] = pd.Series(
            [getattr(bucket, name) for bucket in directory.buckets],
            dtype="timestamp[us, tz=UTC][pyarrow]",
        )
    rows["timezone"] = pd.Series(
        [bucket.timezone for bucket in directory.buckets], dtype="string[pyarrow]"
    )
    return rows


def read_interval_directory(
    axis: IntervalDataAxis, schema: DataSchema, rows: pd.DataFrame
) -> FrozenIntervalDirectory:
    """Validate exact bounds, inert saved zone names and unique identities."""
    if schema != interval_directory_schema(axis):
        _fail("the exact interval.v1 directory schema", repr(schema))
    _layout(schema, rows)
    keys = frozen_key_rows(rows, axis.columns)
    _unique(keys, "interval")
    arrays = {column.name: _array(rows, column) for column in schema.values}
    buckets: list[FrozenIntervalBucket] = []
    identities: set[tuple[datetime, datetime, str]] = set()
    for index, key in enumerate(keys):
        start, end = _datetime(arrays["start"], index), _datetime(arrays["end"], index)
        zone: object = arrays["timezone"][index].as_py()
        if not isinstance(zone, str) or not zone or any(character.isspace() for character in zone):
            _fail("a nonempty single-line frozen boundary timezone name", repr(zone))
        # Source capture normalizes timezone names. Completed coordinates retain
        # that exact identity and never resolve current timezone rules again.
        if start >= end:
            _fail(
                "nonempty half-open intervals with frozen boundary timezones",
                repr((start, end, zone)),
            )
        identity = (start, end, zone)
        if identity in identities:
            _fail("unique exact interval identities", repr(identity))
        identities.add(identity)
        buckets.append(FrozenIntervalBucket(key, start, end, zone))
    return FrozenIntervalDirectory(axis, tuple(buckets))


def build_partial_interval_map(
    left: FrozenIntervalDirectory, right: FrozenIntervalDirectory, rule: CalendarCorrespondence
) -> tuple[FrozenBucketMatch, ...]:
    """Pair only single declared intervals with the same boundary timezone."""
    if (
        rule.kind != "ordinal"
        or len(left.buckets) != 1
        or len(right.buckets) != 1
        or left.buckets[0].timezone != right.buckets[0].timezone
    ):
        _fail(
            "ordinal between one declared interval on each side with the same boundary timezone",
            repr((left, right, rule)),
            match=True,
        )
    return (FrozenBucketMatch(left.buckets[0].key, right.buckets[0].key),)


def merge_equal_interval_directories(
    left: FrozenIntervalDirectory, right: FrozenIntervalDirectory, output_axis: IntervalDataAxis
) -> FrozenIntervalDirectory:
    """Union exact interval identities without trimming the declaration directory."""
    buckets: list[FrozenIntervalBucket] = []
    identities: set[tuple[datetime, datetime, str]] = set()
    for bucket in (*left.buckets, *right.buckets):
        identity = (bucket.start, bucket.end, bucket.timezone)
        if identity not in identities:
            identities.add(identity)
            key = ((output_axis.columns[0].dtype, len(buckets)),)
            buckets.append(replace(bucket, key=key))
    return FrozenIntervalDirectory(output_axis, tuple(buckets))


def paired_mapping_schema(axis: PairedDataAxis) -> DataSchema:
    """Build the complete pair key and nullable all-or-none endpoint key groups."""
    for endpoint in (axis.left, axis.right):
        if isinstance(endpoint, CalendarDataAxis):
            _calendar(endpoint)
        else:
            interval_directory_schema(endpoint)
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
        elif isinstance(axis, IntervalDataAxis):
            result.append((axis.frozen.directory_name, interval_directory_schema(axis)))
        elif isinstance(axis, CalendarDataAxis):
            _calendar(axis)
            assert axis.frozen is not None
            if axis.frozen.direct is not None:
                visit(axis.frozen.direct.axis)
            result.append((axis.frozen.directory_name, calendar_directory_schema(axis)))
            if axis.frozen.direct is not None:
                result.append((axis.frozen.direct.mapping_name, direct_mapping_schema(axis)))
        elif axis.kind == "correspondence":
            _fail("a closed paired coordinate descriptor", repr(axis))

    for axis in schema.axes:
        visit(axis)
    for fact in schema.comparisons:
        binding = fact.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            visit(binding.baseline)
            current = next(axis for axis in schema.axes if axis.token == binding.current_axis_token)
            assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
            if binding.mapping_name not in {name for name, _ in result}:
                result.append((binding.mapping_name, comparison_mapping_schema(current, binding)))
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


def _key_rows(
    columns: tuple[DataColumn, ...], arrays: tuple[pa.Array, ...], length: int
) -> tuple[FrozenKey, ...]:
    components: list[list[FrozenScalar | None]] = []
    for column, values in zip(columns, arrays, strict=True):
        if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
            values = values.cast(pa.int64())
        elif column.dtype == ScalarDataType("date"):
            values = values.cast(pa.int32())
        elif pa.types.is_floating(values.type) and pc.any(pc.invert(pc.is_finite(values))).as_py():
            _fail("finite typed coordinate values", column.slot_id)
        # The exact Arrow carrier admits only FrozenScalar values. Converting
        # each column once also preserves validity for minimum temporal ticks.
        components.append(values.to_pylist())
    return tuple(
        tuple((column.dtype, values[i]) for column, values in zip(columns, components, strict=True))
        for i in range(length)
    )


def frozen_key_rows(rows: pd.DataFrame, columns: tuple[DataColumn, ...]) -> tuple[FrozenKey, ...]:
    """Read dtype-tagged keys using Arrow validity and exact temporal ticks."""
    return _key_rows(columns, tuple(_array(rows, column) for column in columns), len(rows))


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
    """Encode immutable directory records using the shared calendar.v2 layout."""
    rows = frozen_key_table(
        directory.axis.columns, tuple(bucket.key for bucket in directory.buckets)
    )
    rows["nominal_label"] = pd.Series(
        [b.nominal_label for b in directory.buckets], dtype="string[pyarrow]"
    )
    rows["ordinal"] = pd.Series([b.ordinal for b in directory.buckets], dtype="int64[pyarrow]")
    rows["nominal_ordinal"] = pd.Series(
        [b.nominal_ordinal for b in directory.buckets], dtype="int64[pyarrow]"
    )
    for name, values in (
        ("nominal_start", tuple(b.nominal_start for b in directory.buckets)),
        ("nominal_end", tuple(b.nominal_end for b in directory.buckets)),
        ("actual_start", tuple(b.actual_start for b in directory.buckets)),
        ("actual_end", tuple(b.actual_end for b in directory.buckets)),
    ):
        rows[name] = pd.Series(values, dtype="timestamp[us, tz=UTC][pyarrow]")
    for name in _CIVIL_NAMES:
        rows[name] = pd.Series(
            [getattr(b, name) for b in directory.buckets], dtype="string[pyarrow]"
        )
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


def _civil(values: pa.Array, i: int, bound: datetime) -> str:
    value: object = values[i].as_py()
    try:
        if not isinstance(value, str):
            raise ValueError("missing civil boundary")
        stamp = datetime.fromisoformat(value)
        if (
            stamp.utcoffset() is None
            or stamp.isoformat(timespec="microseconds") != value
            or stamp.astimezone(timezone.utc) != bound
        ):
            raise ValueError("civil boundary differs from exact UTC instant")
    except (ValueError, OverflowError) as error:
        _fail("canonical offset-aware civil boundaries matching frozen UTC bounds", str(error))
    return value


def read_calendar_records(
    axis: CalendarDataAxis, schema: DataSchema, rows: pd.DataFrame
) -> tuple[FrozenCalendarBucket, ...]:
    """Validate consumed records without claiming the complete finite directory."""
    if schema != calendar_directory_schema(axis):
        _fail("the exact calendar.v2 directory schema", repr(schema))
    _layout(schema, rows)
    arrays = {column.name: _array(rows, column) for column in schema.values}
    keys = frozen_key_rows(rows, axis.columns)
    _unique(keys, "calendar bucket")
    buckets: list[FrozenCalendarBucket] = []
    for i, key in enumerate(keys):
        label: object = arrays["nominal_label"][i].as_py()
        ordinal: object = arrays["ordinal"][i].as_py()
        nominal_ordinal: object = arrays["nominal_ordinal"][i].as_py()
        if (
            not isinstance(label, str)
            or type(ordinal) is not int
            or type(nominal_ordinal) is not int
        ):
            _fail("nonnull labels and int64 ordinals", repr((label, ordinal, nominal_ordinal)))
        _label(label, axis)
        start, end, actual_start, actual_end = tuple(
            _datetime(arrays[name], i) for name in _BOUND_NAMES
        )
        if not start <= actual_start < actual_end <= end:
            _fail(
                "valid contained half-open nominal and actual bounds",
                repr((start, end, actual_start, actual_end)),
            )
        civil = tuple(
            _civil(arrays[name], i, bound)
            for name, bound in zip(
                _CIVIL_NAMES, (start, end, actual_start, actual_end), strict=True
            )
        )
        assert axis.frozen is not None
        definition = axis.frozen.definition
        if isinstance(
            definition, BuiltinCalendarData
        ) and nominal_ordinal != builtin_nominal_ordinal(
            definition.unit, definition.step, datetime.fromisoformat(civil[0])
        ):
            _fail("the stable builtin nominal period ordinal", repr(nominal_ordinal))
        buckets.append(
            FrozenCalendarBucket(
                key, label, ordinal, start, end, actual_start, actual_end, nominal_ordinal, *civil
            )
        )
    labels = tuple(bucket.nominal_label for bucket in buckets)
    ordinals = tuple(bucket.ordinal for bucket in buckets)
    if len(set(labels)) != len(labels):
        _fail("unique nominal labels", repr(labels))
    if any(ordinal < 0 for ordinal in ordinals) or len(set(ordinals)) != len(ordinals):
        _fail("unique nonnegative original ordinals", repr(ordinals))
    nominal_ordinals = tuple(bucket.nominal_ordinal for bucket in buckets)
    if any(value < 0 for value in nominal_ordinals) or len(set(nominal_ordinals)) != len(
        nominal_ordinals
    ):
        _fail("unique nonnegative nominal ordinals", repr(nominal_ordinals))
    return tuple(buckets)


def read_calendar_directory(
    axis: CalendarDataAxis, schema: DataSchema, rows: pd.DataFrame
) -> FrozenCalendarDirectory:
    """Validate the entire original directory, including its complete ordinal sequence."""
    buckets = read_calendar_records(axis, schema, rows)
    ordinals = tuple(bucket.ordinal for bucket in buckets)
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


def validate_paired_records(
    axis: PairedDataAxis,
    schema: DataSchema,
    rows: pd.DataFrame,
    left: tuple[FrozenCalendarBucket, ...] | tuple[FrozenIntervalBucket, ...],
    right: tuple[FrozenCalendarBucket, ...] | tuple[FrozenIntervalBucket, ...],
) -> None:
    """Validate nominal bucket endpoints independently from primary row presence."""
    if schema != paired_mapping_schema(axis):
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
        domain = {bucket.key for bucket in directory}
        if any(key not in domain for key in present):
            _fail("endpoint keys resolving in their frozen directory", repr(present))


def validate_paired_mapping(
    axis: PairedDataAxis,
    schema: DataSchema,
    rows: pd.DataFrame,
    left: FrozenCalendarDirectory | FrozenIntervalDirectory,
    right: FrozenCalendarDirectory | FrozenIntervalDirectory,
) -> None:
    """Validate the full map against its exact complete endpoint directories."""
    if (left.axis, right.axis) != (axis.left, axis.right):
        _fail("the exact paired endpoint descriptors", repr((left.axis, right.axis)))
    validate_paired_records(axis, schema, rows, left.buckets, right.buckets)


def validate_paired_presence(
    axis: PairedDataAxis,
    schema: DataSchema,
    rows: pd.DataFrame,
    mapping_schema: DataSchema,
    mapping_rows: pd.DataFrame,
) -> None:
    """Check consumed primary presence independently of the bucket-map domain."""
    primary = frozen_key_rows(rows, axis.columns)
    positions = {key: i for i, key in enumerate(frozen_key_rows(mapping_rows, axis.columns))}
    if any(key not in positions for key in primary):
        _fail("every primary paired key resolving in its map", axis.token)
    width = len(axis.left.columns)
    for side, columns in (
        ("left", mapping_schema.values[:width]),
        ("right", mapping_schema.values[width:]),
    ):
        path = (f"{side}_present",)
        if path not in schema.presence_paths:
            continue
        present = _array(rows, schema.presence[schema.presence_paths.index(path)])
        endpoints = _endpoint_keys(mapping_rows, columns)
        if any(
            endpoints[positions[key]] is None and present[i].as_py() is True
            for i, key in enumerate(primary)
        ):
            _fail("row presence consistent with absent calendar endpoints", side)


def validate_presence(schema: DataSchema, rows: pd.DataFrame) -> None:
    """Require inherited true flags to agree with existing generated ancestors."""
    columns = dict(zip(schema.presence_paths, schema.presence, strict=True))
    arrays = {path: _array(rows, column) for path, column in columns.items()}
    for path, child in arrays.items():
        for index, side in enumerate(path[:-1]):
            if side not in ("left", "right"):
                continue
            parent = arrays.get((*path[:index], f"{side}_present"))
            if parent is not None and pc.any(pc.and_(child, pc.invert(parent))).as_py():
                _fail("inherited true presence only beneath present outer rows", ".".join(path))
    for fact in schema.comparisons:
        for endpoint in (
            fact.current_presence,
            fact.baseline_presence,
            *fact.enclosing_presence,
        ):
            for path in endpoint.inherited_paths:
                if pc.any(pc.and_(arrays[path], pc.invert(arrays[endpoint.path]))).as_py():
                    _fail(
                        "inherited true presence only beneath its exact captured endpoint flag",
                        repr((endpoint.path, path)),
                    )


def validate_frozen_coordinates(
    schema: DataSchema, rows: pd.DataFrame, attachments: tuple[FrozenAttachment, ...]
) -> tuple[FrozenKey, ...]:
    """Validate complete coordinate closure, all attachments and primary references."""
    validate_presence(schema, rows)
    layouts = coordinate_attachment_schemas(schema)
    names = tuple(attachment.name for attachment in attachments)
    if len(set(names)) != len(names) or set(names) != {name for name, _ in layouts}:
        _fail("exactly the required unique coordinate attachments", repr(names))
    tables = {attachment.name: attachment for attachment in attachments}
    for name, expected in layouts:
        if tables[name].schema != expected:
            _fail("the exact required coordinate attachment schema", name)
    _layout(schema, rows)
    columns = tuple(c for axis in schema.axes for c in axis.columns)
    complete_keys = frozen_key_rows(rows, columns)
    _unique(complete_keys, "row")
    positions = {column.slot_id: index for index, column in enumerate(columns)}
    primary_keys = {
        axis.token: tuple(
            tuple(key[positions[column.slot_id]] for column in axis.columns)
            for key in complete_keys
        )
        for axis in schema.axes
    }
    directories: dict[
        CalendarDataAxis | IntervalDataAxis, FrozenCalendarDirectory | FrozenIntervalDirectory
    ] = {}

    def read(
        axis: CalendarDataAxis | IntervalDataAxis,
    ) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
        if axis in directories:
            return directories[axis]
        assert axis.frozen is not None
        attachment = tables[axis.frozen.directory_name]
        directory = (
            read_interval_directory(axis, attachment.schema, attachment.rows)
            if isinstance(axis, IntervalDataAxis)
            else read_calendar_directory(axis, attachment.schema, attachment.rows)
        )
        if isinstance(axis, CalendarDataAxis) and axis.frozen.direct is not None:
            direct = axis.frozen.direct
            origin = read(direct.axis)
            assert isinstance(origin, FrozenCalendarDirectory) and isinstance(
                directory, FrozenCalendarDirectory
            )
            table = tables[direct.mapping_name]
            read_direct_mapping(axis, table.schema, table.rows, origin, directory)
        directories[axis] = directory
        return directory

    for axis in schema.axes:
        if isinstance(axis, PairedDataAxis):
            left, right = read(axis.left), read(axis.right)
            table = tables[axis.mapping_name]
            validate_paired_mapping(axis, table.schema, table.rows, left, right)
            domain = set(frozen_key_rows(table.rows, axis.columns))
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            domain = {bucket.key for bucket in read(axis).buckets}
        else:
            continue
        primary = primary_keys[axis.token]
        if any(key not in domain for key in primary):
            _fail("every primary coordinate key resolving in its frozen attachment", axis.token)
        if isinstance(axis, PairedDataAxis):
            table = tables[axis.mapping_name]
            validate_paired_presence(axis, schema, rows, table.schema, table.rows)
    for fact in schema.comparisons:
        binding = fact.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            current = next(axis for axis in schema.axes if axis.token == binding.current_axis_token)
            assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
            table = tables[binding.mapping_name]
            entries = read_comparison_mapping(
                binding, table.schema, table.rows, read(current), read(binding.baseline)
            )
            absent = {
                entry.current_key for entry in entries if isinstance(entry, FrozenComparisonAbsent)
            }
            present = _array(
                rows,
                schema.presence[schema.presence_paths.index(fact.baseline_presence.path)],
            )
            if any(
                key in absent and present[index].as_py() is True
                for index, key in enumerate(primary_keys[current.token])
            ):
                _fail(
                    "row presence consistent with absent comparison endpoints",
                    repr((*fact.namespace, fact.alias, fact.baseline_presence.path)),
                )
    return complete_keys


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
        or definition.unit not in ("day", "week", "month", "quarter", "year")
    ):
        _fail("supported builtin civil grains for this match rule", repr(definition), match=True)


@dataclass(frozen=True, slots=True)
class FrozenCalendarIntent:
    """One civil intention, independent of selected target availability."""

    key: FrozenKey
    result: CivilShiftResult


def calendar_match_intents(
    directory: FrozenCalendarDirectory, rule: CalendarCorrespondence
) -> tuple[FrozenCalendarIntent, ...]:
    """Describe offset target bounds or a legal absent period/cutoff, without TZ lookup."""
    require_calendar_match_rule(directory.axis, rule)
    if rule.kind == "ordinal":
        _fail("an offset rule for civil intentions", repr(rule), match=True)
    if any(
        before.nominal_end > after.nominal_start
        for before, after in zip(directory.buckets, directory.buckets[1:], strict=False)
    ):
        _fail(
            "ordered nonoverlapping selected nominal buckets", repr(directory.buckets), match=True
        )
    assert directory.axis.frozen is not None
    definition = directory.axis.frozen.definition
    assert isinstance(definition, BuiltinCalendarData)
    result: list[FrozenCalendarIntent] = []
    for bucket in directory.buckets:
        try:
            intent = civil_shift(
                definition.unit,
                definition.step,
                rule.kind,
                rule.by,
                (
                    bucket.nominal_start_civil,
                    bucket.nominal_end_civil,
                    bucket.actual_start_civil,
                    bucket.actual_end_civil,
                ),
            )
        except ValueError as error:
            _fail(
                "complete buckets or supported exact bucket-start prefixes", str(error), match=True
            )
        result.append(FrozenCalendarIntent(bucket.key, intent))
    return tuple(result)


def _wall_bounds(bucket: FrozenCalendarBucket) -> tuple[datetime, datetime, datetime, datetime]:
    return (
        datetime.fromisoformat(bucket.nominal_start_civil).replace(tzinfo=None),
        datetime.fromisoformat(bucket.nominal_end_civil).replace(tzinfo=None),
        datetime.fromisoformat(bucket.actual_start_civil).replace(tzinfo=None),
        datetime.fromisoformat(bucket.actual_end_civil).replace(tzinfo=None),
    )


def build_partial_calendar_map(
    left: FrozenCalendarDirectory,
    right: FrozenCalendarDirectory,
    rule: CalendarCorrespondence,
) -> tuple[FrozenBucketMatch, ...]:
    """Map declared finite directories before any actual-row exact/outer policy."""
    require_compatible_calendar_axes(left.axis, right.axis)
    require_calendar_match_rule(left.axis, rule)
    if rule.kind == "ordinal":
        require_matchable_calendar_directory(left)
        require_matchable_calendar_directory(right)
        intents: tuple[FrozenCalendarIntent, ...] = ()
    else:
        intents = calendar_match_intents(left, rule)
        # Validate target admission independently; never clip an aggregated target.
        calendar_match_intents(right, CalendarCorrespondence("periods", 0))
    ordinals = {bucket.ordinal: bucket for bucket in right.buckets}
    nominal = {
        (_wall_bounds(bucket)[0], _wall_bounds(bucket)[1]): bucket for bucket in right.buckets
    }
    result: list[FrozenBucketMatch] = []
    used: set[FrozenKey] = set()
    for i, bucket in enumerate(left.buckets):
        if rule.kind == "ordinal":
            partner = ordinals.get(bucket.ordinal)
        else:
            intended = intents[i].result
            partner = None
            if isinstance(intended, CivilShiftTarget):
                partner = nominal.get((intended.bounds[0], intended.bounds[1]))
                if partner is not None and (
                    _wall_bounds(partner) != intended.bounds
                    or (
                        intended.exact_instants is not None
                        and intended.exact_instants
                        != (
                            partner.nominal_start,
                            partner.nominal_end,
                            partner.actual_start,
                            partner.actual_end,
                        )
                    )
                ):
                    _fail(
                        "the exact saved nominal and prefix progress boundaries",
                        partner.nominal_label,
                        match=True,
                    )
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
