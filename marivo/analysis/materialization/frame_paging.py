"""Projection, cursor binding and bounded reads for fixed Frame data."""

from __future__ import annotations

import base64
import json
from hashlib import sha256

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.artifact_data import DataSchema, OutputAttachment
from marivo.analysis.core.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    IntervalComparisonBinding,
    IntervalDataAxis,
    NamedCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.data_validation import normalize_complete_frame
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FrameKeyScalar,
    FramePage,
    FramePairedPeriod,
    FramePeriod,
    FramePeriodItem,
    FramePeriodPage,
    FrameReadStats,
    detached_rows,
)
from marivo.analysis.core.frame_schema import (
    FrameAxisSchema,
    FrameComparison,
    capture_axis,
    capture_comparisons,
    comparison_id,
    export_layout,
    selected_columns,
)
from marivo.analysis.core.frozen_calendar import (
    FrozenCalendarBucket,
    FrozenCalendarDirectory,
    FrozenComparisonAbsent,
    FrozenIntervalBucket,
    FrozenIntervalDirectory,
    FrozenKey,
    frozen_key_rows,
    frozen_key_values,
    read_calendar_records,
    read_comparison_mapping,
    read_interval_directory,
    validate_paired_records,
)
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.contracts import digest
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_reading import (
    FramePreview,
    FrameReader,
    selected_coordinates,
)
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import POLICY


def _cursor(binding: str, position: int) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps([binding, position], separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _position(cursor: str | None, binding: str, total: int, *, periods: bool = False) -> int:
    if cursor is None:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if (
            not isinstance(value, list)
            or len(value) != 2
            or value[0] != binding
            or type(value[1]) is not int
            or not 0 < value[1] < total
        ):
            raise ValueError("cursor does not belong to this read")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        reject(
            "the preceding cursor for this exact input, target and limit"
            if periods
            else "the preceding cursor for this exact input, projection, axis names and limit",
            str(error),
            "Repeat the same read_periods parameters or begin again with cursor=None."
            if periods
            else "Repeat the same read_page parameters or begin again with cursor=None.",
            "frame.read_periods" if periods else "frame.read_page",
            canonical_id="analysis_frame.read_periods" if periods else "analysis_frame.read_page",
        )
    result: int = value[1]
    return result


def _memory_range(
    data: CompletedFrameData, start: int, limit: int, columns: tuple[DataColumn, ...]
) -> tuple[FramePreview, FrameReadStats]:
    names = [column.slot_id for column in columns]
    rows = detached_rows(data._rows.iloc[start : start + limit].loc[:, names])
    rows = normalize_complete_frame(
        rows,
        columns,
        data.schema.key_fields,
        unique_keys=data.unique_keys.project(tuple(c.slot_id for c in columns)),
    )
    coordinate_rows_read = 0
    saved = {part.name: part for part in data._attachments}

    def select(name: str, keys: set[FrozenKey]) -> OutputAttachment:
        nonlocal coordinate_rows_read
        part = saved[name]
        key_columns = tuple(column for axis in part.schema.axes for column in axis.columns)
        chunks: list[pd.DataFrame] = []
        found: set[FrozenKey] = set()
        if keys:
            for offset in range(0, len(part.rows), POLICY.batch_rows):
                batch = part.rows.iloc[offset : offset + POLICY.batch_rows]
                coordinate_rows_read += len(batch)
                selected: list[int] = []
                for index, key in enumerate(frozen_key_rows(batch, key_columns)):
                    if key in keys:
                        if key in found:
                            raise invalid("duplicate selected frozen coordinate key")
                        found.add(key)
                        selected.append(index)
                if selected:
                    chunks.append(detached_rows(batch.iloc[selected]))
        if found != keys:
            raise invalid("selected coordinate does not resolve in its saved attachment")
        selected_rows = (
            pd.concat(chunks, ignore_index=True) if chunks else detached_rows(part.rows.iloc[:0])
        )
        return OutputAttachment(
            name,
            part.schema,
            normalize_complete_frame(selected_rows, part.schema.columns, part.schema.key_fields),
        )

    parts = selected_coordinates(data.schema, rows, select)
    return FramePreview(data.schema, rows, parts), FrameReadStats(
        len(rows), coordinate_rows_read, 0
    )


def memory_preview(data: CompletedFrameData, n: int) -> FramePreview:
    """Copy only the requested prefix and its necessary frozen coordinates."""
    preview, _ = _memory_range(data, 0, n, data.schema.columns)
    return preview


def _coordinates(schema: DataSchema, rows: pd.DataFrame) -> dict[str, tuple[FrameKeyScalar, ...]]:
    result: dict[str, tuple[FrameKeyScalar, ...]] = {}
    for axis in schema.axes:
        for column in axis.columns:
            array = pa.array(rows[column.slot_id], from_pandas=True)
            if isinstance(
                column.dtype, (TimestampDataType, DurationDataType)
            ) or column.dtype == ScalarDataType("date"):
                result[column.slot_id] = tuple(
                    scalar if scalar.is_valid else None for scalar in array
                )
            else:
                result[column.slot_id] = tuple(array.to_pylist())
    return result


def read_frame_page(
    *,
    input_id: str,
    schema: DataSchema,
    backing: MemoryFrameBacking | StoredFrameBacking,
    local: LocalFrameServices,
    columns: tuple[str, ...] | None,
    limit: int,
    cursor: str | None,
    axis_names: tuple[str, ...],
    time_details: bool = False,
) -> FramePage:
    """Validate one exact request before consuming its projected fixed data."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 10000
        or (cursor is not None and type(cursor) is not str)
    ):
        reject(
            "limit 1..10000 and a cursor string or None",
            repr((limit, cursor)),
            "Use the preceding page cursor with the same projection, aliases and limit.",
            "frame.read_page",
            canonical_id="analysis_frame.read_page",
        )
    requested = selected_columns(schema, columns)
    layout = export_layout(
        schema,
        axis_names,
        columns,
        validate_names=True,
        member="read_page",
        time_details=time_details,
    )
    total = (
        backing.data.row_count
        if isinstance(backing, MemoryFrameBacking)
        else backing.data.rows.receipt.realized_row_count
    )
    selected_names = tuple(column.name for column in requested)
    version = (
        digest(
            (
                repr(schema),
                backing.data.rows.payload_digest,
                tuple((part.name, part.rows.payload_digest) for part in backing.data.attachments),
            )
        )
        if isinstance(backing, StoredFrameBacking)
        else input_id
    )
    binding = sha256(
        json.dumps(
            [
                "frame-page-v1",
                input_id,
                version,
                total,
                selected_names,
                axis_names,
                time_details,
                limit,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    start = _position(cursor, binding, total)
    # All presence flags are bounded validation helpers, even when not exported.
    needed = {column.slot_id for column in (*requested, *schema.presence)}
    needed.update(column.slot_id for axis in schema.axes for column in axis.columns)
    read_columns = tuple(column for column in schema.columns if column.slot_id in needed)
    if isinstance(backing, MemoryFrameBacking):
        preview, stats = _memory_range(backing.data, start, limit, read_columns)
    else:
        preview, stats = FrameReader(local.store, local.session_ref).range(
            backing, start, limit, read_columns
        )
    rows = export_frame(
        schema,
        preview.rows,
        preview.attachments,
        axis_names=axis_names,
        columns=columns,
        include_attrs=False,
        time_details=time_details,
    )
    position = start + len(rows)
    has_more = position < total
    return FramePage(
        input_id=input_id,
        layout=layout,
        coordinates=_coordinates(schema, preview.rows),
        limit=limit,
        total_count=total,
        has_more=has_more,
        next_cursor=_cursor(binding, position) if has_more else None,
        read_stats=stats,
        _rows=rows,
    )


class _PeriodRead:
    """Transient per-call attachment access; pages never retain this reader."""

    def __init__(
        self, backing: MemoryFrameBacking | StoredFrameBacking, local: LocalFrameServices
    ) -> None:
        self.backing = backing
        self.reader = FrameReader(local.store, local.session_ref)
        self.stats = FrameReadStats(0, 0, 0)

    def _add(self, stats: FrameReadStats) -> None:
        self.stats = FrameReadStats(
            self.stats.data_rows_read + stats.data_rows_read,
            self.stats.coordinate_rows_read + stats.coordinate_rows_read,
            self.stats.receipt_bytes_hashed + stats.receipt_bytes_hashed,
        )

    def count(self, name: str) -> int:
        if isinstance(self.backing, MemoryFrameBacking):
            part = next((p for p in self.backing.data._attachments if p.name == name), None)
            if part is None:
                raise invalid("required frozen coordinate attachment is missing")
            return len(part.rows)
        saved = next((p for p in self.backing.data.attachments if p.name == name), None)
        if saved is None:
            raise invalid("required frozen coordinate attachment is missing")
        return saved.rows.receipt.realized_row_count

    def range(self, name: str, start: int, limit: int, *, ordinal: bool) -> OutputAttachment:
        if isinstance(self.backing, StoredFrameBacking):
            result, stats = self.reader.attachment_range(
                self.backing, name, start, limit, ordinal=ordinal
            )
            self._add(stats)
            return result
        part = next(p for p in self.backing.data._attachments if p.name == name)
        end = min(start + limit, len(part.rows))
        rows = detached_rows(part.rows.iloc[start:end])
        self._add(FrameReadStats(0, len(rows), 0))
        if ordinal and rows["ordinal"].tolist() != list(range(start, end)):
            raise invalid("selected calendar ordinal differs from its saved physical position")

        return OutputAttachment(name, part.schema, rows)

    def select(self, name: str, keys: set[FrozenKey]) -> OutputAttachment:
        if isinstance(self.backing, StoredFrameBacking):
            result, stats = self.reader.attachment_select(self.backing, name, keys)
            self._add(stats)
            return result
        part = next((p for p in self.backing.data._attachments if p.name == name), None)
        if part is None:
            raise invalid("required frozen coordinate attachment is missing")
        columns = tuple(c for axis in part.schema.axes for c in axis.columns)
        chunks: list[pd.DataFrame] = []
        found: set[FrozenKey] = set()
        if keys:
            for offset in range(0, len(part.rows), POLICY.batch_rows):
                batch = part.rows.iloc[offset : offset + POLICY.batch_rows]
                self._add(FrameReadStats(0, len(batch), 0))
                selected: list[int] = []
                for index, key in enumerate(frozen_key_rows(batch, columns)):
                    if key in keys:
                        if key in found:
                            raise invalid("duplicate selected frozen coordinate key")
                        found.add(key)
                        selected.append(index)
                if selected:
                    chunks.append(detached_rows(batch.iloc[selected]))
        if found != keys:
            raise invalid("selected coordinate does not resolve in its saved attachment")
        return OutputAttachment(
            name,
            part.schema,
            pd.concat(chunks, ignore_index=True) if chunks else detached_rows(part.rows.iloc[:0]),
        )


def _period(
    axis: CalendarDataAxis | IntervalDataAxis,
    bucket: FrozenCalendarBucket | FrozenIntervalBucket,
) -> FramePeriod:
    key = frozen_key_values(bucket.key)
    if isinstance(bucket, FrozenCalendarBucket):
        assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
        label: object = (
            json.loads(bucket.nominal_label)
            if isinstance(axis.frozen.definition, NamedCalendarData)
            else bucket.nominal_label
        )
        assert isinstance(label, (bool, str, int, float))
        return FrameCalendarPeriod(
            key,
            axis.timezone,
            bucket.actual_start,
            bucket.actual_end,
            label,
            bucket.ordinal,
            bucket.nominal_ordinal,
            bucket.nominal_start,
            bucket.nominal_end,
            bucket.nominal_start_civil,
            bucket.nominal_end_civil,
            bucket.actual_start_civil,
            bucket.actual_end_civil,
        )
    return FrameIntervalPeriod(key, bucket.timezone, bucket.start, bucket.end)


def _records(
    axis: CalendarDataAxis | IntervalDataAxis,
    part: OutputAttachment,
) -> tuple[FrozenCalendarBucket, ...] | tuple[FrozenIntervalBucket, ...]:
    return (
        read_calendar_records(axis, part.schema, part.rows)
        if isinstance(axis, CalendarDataAxis)
        else read_interval_directory(axis, part.schema, part.rows).buckets
    )


def _endpoints(
    read: _PeriodRead,
    axis: CalendarDataAxis | IntervalDataAxis,
    keys: tuple[FrozenKey, ...],
) -> tuple[FrozenCalendarBucket, ...] | tuple[FrozenIntervalBucket, ...]:
    assert axis.frozen is not None
    present = {key for key in keys if all(value is not None for _, value in key)}
    return _records(axis, read.select(axis.frozen.directory_name, present))


def _directory(
    axis: CalendarDataAxis | IntervalDataAxis,
    records: tuple[FrozenCalendarBucket | FrozenIntervalBucket, ...],
) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
    if isinstance(axis, CalendarDataAxis):
        assert all(isinstance(record, FrozenCalendarBucket) for record in records)
        return FrozenCalendarDirectory(
            axis, tuple(r for r in records if isinstance(r, FrozenCalendarBucket))
        )
    assert all(isinstance(record, FrozenIntervalBucket) for record in records)
    return FrozenIntervalDirectory(
        axis, tuple(r for r in records if isinstance(r, FrozenIntervalBucket))
    )


def read_frame_periods(
    *,
    input_id: str,
    schema: DataSchema,
    backing: MemoryFrameBacking | StoredFrameBacking,
    local: LocalFrameServices,
    target: FrameAxisSchema | FrameComparison,
    limit: int = 1000,
    cursor: str | None = None,
) -> FramePeriodPage:
    """Read one captured time directory or consumed correspondence, with exact keys."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 10000
        or (cursor is not None and type(cursor) is not str)
    ):
        reject(
            "limit 1..10000 and a cursor string or None",
            repr((limit, cursor)),
            "Use the preceding period cursor with the same target and limit.",
            "frame.read_periods",
            canonical_id="analysis_frame.read_periods",
        )
    axis = None
    binding = None
    if isinstance(target, FrameAxisSchema):
        axis = next(
            (a for a in schema.axes if a.token == target.token and capture_axis(a) == target), None
        )
        if isinstance(axis, PairedDataAxis):
            name, ordinal = axis.mapping_name, False
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            assert axis.frozen is not None
            name, ordinal = axis.frozen.directory_name, isinstance(axis, CalendarDataAxis)
        else:
            reject(
                "one current calendar, interval or paired axis",
                repr(target),
                "Choose a time axis from this Frame's axes.",
                "frame.read_periods",
                canonical_id="analysis_frame.read_periods",
            )
        target_key = ("axis", target.token)
    elif (
        isinstance(target, FrameComparison)
        and target.input_id == input_id
        and target in capture_comparisons(input_id, schema)
    ):
        fact = next(f for f in schema.comparisons if comparison_id(f) == target.comparison_id)
        binding = fact.binding
        if not isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            reject(
                "a current time comparison",
                repr(target),
                "Choose a calendar or interval comparison from frame.schema.comparisons.",
                "frame.read_periods",
                canonical_id="analysis_frame.read_periods",
            )
        axis = next(a for a in schema.axes if a.token == binding.current_axis_token)
        assert isinstance(axis, (CalendarDataAxis, IntervalDataAxis))
        name, ordinal = binding.mapping_name, False
        target_key = ("comparison", target.comparison_id)
    else:
        reject(
            "a time target from this exact Frame input",
            repr(target),
            "Choose a time axis or comparison from this Frame.",
            "frame.read_periods",
            canonical_id="analysis_frame.read_periods",
        )
    read = _PeriodRead(backing, local)
    total = read.count(name)
    version = (
        digest(
            (
                repr(schema),
                backing.data.rows.payload_digest,
                tuple((part.name, part.rows.payload_digest) for part in backing.data.attachments),
            )
        )
        if isinstance(backing, StoredFrameBacking)
        else input_id
    )
    cursor_binding = digest(("frame-period-page-v1", input_id, version, target_key, total, limit))
    start = _position(cursor, cursor_binding, total, periods=True)
    part = read.range(name, start, limit, ordinal=ordinal)
    items: tuple[FramePeriodItem, ...]
    if isinstance(axis, PairedDataAxis):
        width = len(axis.left.columns)
        left_keys = frozen_key_rows(part.rows, part.schema.values[:width])
        right_keys = frozen_key_rows(part.rows, part.schema.values[width:])
        left = _endpoints(read, axis.left, left_keys)
        right = _endpoints(read, axis.right, right_keys)
        validate_paired_records(axis, part.schema, part.rows, left, right)
        a = {bucket.key: _period(axis.left, bucket) for bucket in left}
        b = {bucket.key: _period(axis.right, bucket) for bucket in right}
        items = tuple(
            FramePairedPeriod(frozen_key_values(key), a.get(left_key), b.get(right_key))
            for key, left_key, right_key in zip(
                frozen_key_rows(part.rows, axis.columns), left_keys, right_keys, strict=True
            )
        )
    elif isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
        assert isinstance(axis, (CalendarDataAxis, IntervalDataAxis))
        keys = frozen_key_rows(part.rows, axis.columns)
        current_records = _endpoints(read, axis, keys)
        current = {bucket.key: bucket for bucket in current_records}
        baseline_keys = frozen_key_rows(part.rows, part.schema.values[:-1])
        baseline_records = _endpoints(read, binding.baseline, baseline_keys)
        entries = read_comparison_mapping(
            binding,
            part.schema,
            part.rows,
            _directory(axis, tuple(current[key] for key in keys)),
            _directory(binding.baseline, baseline_records),
        )
        baseline = {bucket.key: _period(binding.baseline, bucket) for bucket in baseline_records}
        items = tuple(
            FrameComparisonPeriod(
                frozen_key_values(entry.current_key),
                _period(axis, current[entry.current_key]),
                None if isinstance(entry, FrozenComparisonAbsent) else baseline[entry.baseline_key],
                entry.reason if isinstance(entry, FrozenComparisonAbsent) else None,
            )
            for entry in entries
        )
    else:
        assert isinstance(axis, (CalendarDataAxis, IntervalDataAxis))
        items = tuple(_period(axis, bucket) for bucket in _records(axis, part))
    position = start + len(items)
    has_more = position < total
    return FramePeriodPage(
        input_id,
        target,
        items,
        limit,
        total,
        has_more,
        _cursor(cursor_binding, position) if has_more else None,
        read.stats,
    )
