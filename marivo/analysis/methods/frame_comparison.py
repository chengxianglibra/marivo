"""Pure complete-key ordinary pairing, independent of semantic endpoints."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.compiler.query_program import FrameCombineProgram
from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    IntervalDataAxis,
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
from marivo.analysis.core.frame_expression import CompareFrameValues
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    build_partial_calendar_map,
    build_partial_interval_map,
    calendar_directory_rows,
    calendar_directory_schema,
    frozen_key_rows,
    frozen_key_table,
    interval_directory_rows,
    interval_directory_schema,
    merge_equal_calendar_directories,
    merge_equal_interval_directories,
    paired_mapping_schema,
    read_calendar_directory,
    read_interval_directory,
    validate_frozen_coordinates,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_float64,
    nullable_arithmetic,
)

Key: TypeAlias = tuple[tuple[DataType, object], ...]


def _fail(expected: str, received: str) -> None:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Use unique complete keys and compatible actual domains, or how='outer' for missing keys.",
        stage="frame_combine",
    )


def _index(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...]
) -> tuple[list[Key], dict[Key, int]]:
    components: list[tuple[DataType, list[object]]] = []
    for column in columns:
        values = pa.array(frame[column.slot_id], from_pandas=True)
        # Arrow validity distinguishes real minimum ticks from null. Temporal
        # Python/pandas scalars can instead become NaT or overflow their range.
        if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
            values = values.cast(pa.int64())
        elif column.dtype == ScalarDataType("date"):
            values = values.cast(pa.int32())
        components.append((column.dtype, values.to_pylist()))
    keys = [tuple((dtype, values[i]) for dtype, values in components) for i in range(len(frame))]
    index = {key: i for i, key in enumerate(keys)}
    if len(index) != len(keys):
        _fail("unique complete row keys", "duplicate complete keys")
    return keys, index


def _take(frame: pd.DataFrame, slot: str, indices: list[int | None]) -> pd.Series:
    values = pa.array(frame[slot], from_pandas=True)
    return pd.Series(pd.arrays.ArrowExtensionArray(values.take(pa.array(indices, type=pa.int64()))))


def combine_frame_data(
    program: FrameCombineProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...]]:
    """Resolve frozen coordinates, then reuse the complete joint-key/value kernel."""
    if len(inputs) != 2 or len(attachments) != 2:
        _fail("two complete Frame inputs with their attachments", str(len(inputs)))
    for schema, rows, tables in zip(
        (program.left_schema, program.right_schema), inputs, attachments, strict=True
    ):
        validate_frozen_coordinates(
            schema, rows, tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in tables)
        )
    sources = tuple({table.name: table for table in group} for group in attachments)
    output_attachments: list[OutputAttachment] = []
    left_axes, right_axes = list(program.left_schema.axes), list(program.right_schema.axes)
    frames = [frame.copy(deep=False) for frame in inputs]
    for i, j in program.operation.pairs:
        left, right, output = left_axes[i], right_axes[j], program.output_schema.axes[i]
        if isinstance(left, IntervalDataAxis):
            assert isinstance(right, IntervalDataAxis)
            a, b = sources[0][left.frozen.directory_name], sources[1][right.frozen.directory_name]
            li, ri = (
                read_interval_directory(left, a.schema, a.rows),
                read_interval_directory(right, b.schema, b.rows),
            )
            if isinstance(output, IntervalDataAxis):
                merged = merge_equal_interval_directories(li, ri, output)
                output_attachments.append(
                    OutputAttachment(
                        output.frozen.directory_name,
                        interval_directory_schema(output),
                        interval_directory_rows(merged),
                    )
                )
                identities = {
                    (bucket.start, bucket.end, bucket.timezone): bucket.key
                    for bucket in merged.buckets
                }
                for side, interval_directory in enumerate((li, ri)):
                    interval_lookup = {
                        bucket.key: identities[(bucket.start, bucket.end, bucket.timezone)]
                        for bucket in interval_directory.buckets
                    }
                    encoded = frozen_key_table(
                        output.columns,
                        tuple(
                            interval_lookup[key]
                            for key in frozen_key_rows(
                                frames[side], interval_directory.axis.columns
                            )
                        ),
                    )
                    for target, source in zip(
                        output.columns, interval_directory.axis.columns, strict=True
                    ):
                        frames[side][source.slot_id] = encoded[target.slot_id]
                continue
            assert isinstance(output, PairedDataAxis) and program.operation.match is not None
            mapping = build_partial_interval_map(li, ri, program.operation.match)
            for endpoint, interval_directory in ((output.left, li), (output.right, ri)):
                assert isinstance(endpoint, IntervalDataAxis)
                copied_interval = replace(interval_directory, axis=endpoint)
                output_attachments.append(
                    OutputAttachment(
                        endpoint.frozen.directory_name,
                        interval_directory_schema(endpoint),
                        interval_directory_rows(copied_interval),
                    )
                )
        elif isinstance(left, CalendarDataAxis):
            assert isinstance(right, CalendarDataAxis)
            assert left.frozen is not None and right.frozen is not None
            a, b = sources[0][left.frozen.directory_name], sources[1][right.frozen.directory_name]
            ldir, rdir = (
                read_calendar_directory(left, a.schema, a.rows),
                read_calendar_directory(right, b.schema, b.rows),
            )
            if isinstance(output, CalendarDataAxis):
                directory = merge_equal_calendar_directories(ldir, rdir, output)
                assert output.frozen is not None
                output_attachments.append(
                    OutputAttachment(
                        output.frozen.directory_name,
                        calendar_directory_schema(output),
                        calendar_directory_rows(directory),
                    )
                )
                continue
            assert isinstance(output, PairedDataAxis) and program.operation.match is not None
            mapping = build_partial_calendar_map(ldir, rdir, program.operation.match)
            for endpoint, directory in ((output.left, ldir), (output.right, rdir)):
                assert isinstance(endpoint, CalendarDataAxis) and endpoint.frozen is not None
                copied = replace(directory, axis=endpoint)
                output_attachments.append(
                    OutputAttachment(
                        endpoint.frozen.directory_name,
                        calendar_directory_schema(endpoint),
                        calendar_directory_rows(copied),
                    )
                )
        else:
            continue
        map_schema = paired_mapping_schema(output)
        rows = pd.DataFrame(
            {output.columns[0].slot_id: pd.Series(range(len(mapping)), dtype="int64[pyarrow]")}
        )
        width = len(output.left.columns)
        for columns, keys in (
            (map_schema.values[:width], tuple(pair.left_key for pair in mapping)),
            (map_schema.values[width:], tuple(pair.right_key for pair in mapping)),
        ):
            encoded = frozen_key_table(columns, keys)
            for column in columns:
                rows[column.slot_id] = encoded[column.slot_id]
        output_attachments.append(OutputAttachment(output.mapping_name, map_schema, rows))
        for side, endpoint in enumerate((left, right)):
            lookup = {
                key: index
                for index, pair in enumerate(mapping)
                if (key := pair.left_key if side == 0 else pair.right_key) is not None
            }
            frames[side][output.columns[0].slot_id] = pd.Series(
                [lookup[key] for key in frozen_key_rows(frames[side], endpoint.columns)],
                dtype="int64[pyarrow]",
            )
        # The existing numeric kernel receives an ordinary view of the mapped
        # calendar component. Every remaining complete ordinary component stays
        # in the same joint key; output metadata remains the real paired axis.
        virtual = DataAxis(output.token, output.columns, name=output.name)
        left_axes[i], right_axes[j] = virtual, virtual
    mapped = replace(
        program,
        left_schema=replace(program.left_schema, axes=tuple(left_axes)),
        right_schema=replace(program.right_schema, axes=tuple(right_axes)),
    )
    return combine_frames(mapped, tuple(frames)), tuple(output_attachments)


def combine_frames(program: FrameCombineProgram, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    """Pair full actual keys in left-first order and calculate every named value."""
    if len(inputs) != 2:
        _fail("two explicit complete inputs", str(len(inputs)))
    left, right = inputs
    a, b, output = program.left_schema, program.right_schema, program.output_schema
    partners = dict(program.operation.pairs)
    left_columns = tuple(c for axis in a.axes for c in axis.columns)
    right_columns = tuple(c for i in range(len(a.axes)) for c in b.axes[partners[i]].columns)
    left_keys, left_index = _index(left, left_columns)
    right_keys, right_index = _index(right, right_columns)
    if program.operation.how == "exact" and left_index.keys() != right_index.keys():
        _fail(
            "equal actual complete row-key domains",
            repr(
                (
                    tuple(key for key in left_keys if key not in right_index),
                    tuple(key for key in right_keys if key not in left_index),
                )
            ),
        )
    keys = left_keys + [key for key in right_keys if key not in left_index]
    left_rows = [left_index.get(key) for key in keys]
    right_rows = [right_index.get(key) for key in keys]
    columns: dict[str, pd.Series] = {}
    for target, source, other in zip(output.key_fields, left_columns, right_columns, strict=True):
        left_values = pa.array(_take(left, source.slot_id, left_rows))
        right_values = pa.array(_take(right, other.slot_id, right_rows))
        values = pc.if_else(
            pa.array([i is not None for i in left_rows], type=pa.bool_()), left_values, right_values
        )
        columns[target] = pd.Series(pd.arrays.ArrowExtensionArray(values))
    compare = isinstance(program.operation, CompareFrameValues)
    for i, path in enumerate(output.value_paths):
        side, original = path[0], path[1:]
        if side not in ("left", "right"):
            continue
        schema, frame, indices = (a, left, left_rows) if side == "left" else (b, right, right_rows)
        column = schema.values[schema.value_paths.index(original)]
        columns[output.values[i].slot_id] = _take(frame, column.slot_id, indices)
    if compare:
        assert isinstance(program.operation, CompareFrameValues)
        n = len(a.values)
        for i in range(len(a.values)):
            x, y = columns[output.values[i].slot_id], columns[output.values[n + i].slot_id]
            valid = x.notna() & y.notna()
            x, y = x.mask(~valid, pd.NA), y.mask(~valid, pd.NA)
            absolute_type = output.values[2 * n + i].dtype
            if isinstance(absolute_type, DurationDataType):
                # Convert once to original ticks; abs(min_int64) is a Python int.
                yt = pd.Series(
                    pa.array(y, type=pa.duration(absolute_type.unit), from_pandas=True)
                    .cast(pa.int64())
                    .to_pylist(),
                    dtype=object,
                )
                delta = duration_arithmetic(
                    "subtract",
                    x,
                    y,
                    left_type=absolute_type,
                    right_type=absolute_type,
                    output=absolute_type,
                    zero_denominator="null",
                )
                ticks = pd.Series(
                    pa.array(delta, type=pa.duration(absolute_type.unit), from_pandas=True)
                    .cast(pa.int64())
                    .to_pylist(),
                    dtype=object,
                ).mask(~valid, pd.NA)
                denominator = yt.map(lambda v: abs(v) if pd.notna(v) else pd.NA)
                relative = nullable_arithmetic(
                    "divide",
                    ticks,
                    denominator,
                    exact_duration=True,
                    zero_denominator=program.operation.zero_denominator,
                )
            else:
                delta = nullable_arithmetic("subtract", x, y)
                denominator = y.map(
                    lambda v: (
                        v.copy_abs() if isinstance(v, Decimal) else abs(v) if pd.notna(v) else pd.NA
                    )
                )
                relative = nullable_arithmetic(
                    "divide",
                    delta,
                    denominator,
                    zero_denominator=program.operation.zero_denominator,
                )
                if isinstance(absolute_type, DecimalDataType):
                    delta = delta.map(
                        lambda v: Decimal(v) if pd.notna(v) and not isinstance(v, Decimal) else v
                    )
                elif absolute_type == ScalarDataType("float64"):
                    delta = finish_float64(delta, "Frame.compare absolute")
            columns[output.values[2 * n + i].slot_id] = delta
            columns[output.values[3 * n + i].slot_id] = finish_float64(
                relative, "Frame.compare relative"
            )
    columns[output.presence[0].slot_id] = pd.Series([i is not None for i in left_rows], dtype=bool)
    columns[output.presence[1].slot_id] = pd.Series([i is not None for i in right_rows], dtype=bool)
    inherited = 2
    for schema, frame, indices in ((a, left, left_rows), (b, right, right_rows)):
        for column in schema.presence:
            columns[output.presence[inherited].slot_id] = pd.Series(
                [False if i is None else bool(frame[column.slot_id].iloc[i]) for i in indices],
                dtype=bool,
            )
            inherited += 1
    return pd.DataFrame(
        columns, columns=[c.slot_id for c in output.columns], index=pd.RangeIndex(len(keys))
    )
