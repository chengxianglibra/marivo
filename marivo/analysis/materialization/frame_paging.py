"""Projection, cursor binding and bounded reads for fixed Frame data."""

from __future__ import annotations

import base64
import json
from hashlib import sha256

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.artifact_data import DataSchema, OutputAttachment
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
from marivo.analysis.core.frame_page import FrameKeyScalar, FramePage, FrameReadStats, detached_rows
from marivo.analysis.core.frame_schema import export_layout, selected_columns
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows
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


def _position(cursor: str | None, binding: str, total: int) -> int:
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
            "the preceding cursor for this exact input, projection, axis names and limit",
            str(error),
            "Repeat the same read_page parameters or begin again with cursor=None.",
            "frame.read_page",
            canonical_id="analysis_frame.read_page",
        )
    result: int = value[1]
    return result


def _memory_range(
    data: CompletedFrameData, start: int, limit: int, columns: tuple[DataColumn, ...]
) -> tuple[FramePreview, FrameReadStats]:
    names = [column.slot_id for column in columns]
    rows = detached_rows(data._rows.iloc[start : start + limit].loc[:, names])
    rows = normalize_complete_frame(rows, columns, data.schema.key_fields)
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
