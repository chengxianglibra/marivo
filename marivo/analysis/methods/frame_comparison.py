"""Pure complete-key ordinary pairing, independent of semantic endpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.compiler.query_program import FrameCombineProgram, FrameCompareProgram
from marivo.analysis.core.attachments import OutputAttachment
from marivo.analysis.core.frame_expression import transport_comparisons
from marivo.analysis.core.frame_interpretation import remap_context
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    FrozenIntervalDirectory,
    build_partial_calendar_map,
    build_partial_interval_map,
    calendar_directory_rows,
    frozen_key_rows,
    frozen_key_table,
    interval_directory_rows,
    merge_equal_calendar_directories,
    merge_equal_interval_directories,
    read_calendar_directory,
    read_interval_directory,
    validate_frozen_coordinates,
)
from marivo.analysis.methods.frame_comparison_time import comparison_failure as _fail
from marivo.analysis.methods.frame_comparison_time import map_baseline_time
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_float64,
    nullable_arithmetic,
    typed_null_mask,
)
from marivo.analysis.model.coordinate_schema import (
    calendar_directory_schema,
    comparison_mapping_schema,
    interval_directory_schema,
    paired_mapping_schema,
)
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataComparison,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.data_types import (
    DataColumn,
    DataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.frame_schema import comparison_id
from marivo.analysis.model.frame_structure import coordinate_attachments
from marivo.analysis.model.layout_facts import FrameValueContext
from marivo.analysis.model.unique_keys import UniqueKeys

Key: TypeAlias = tuple[tuple[DataType, object], ...]


def _index(
    frame: pd.DataFrame,
    columns: tuple[DataColumn, ...],
    validated_keys: tuple[Key, ...] | None = None,
) -> tuple[list[Key], dict[Key, int]]:
    if validated_keys is not None:
        keys = list(validated_keys)
        return keys, {key: index for index, key in enumerate(keys)}
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


def _ordered_keys(
    keys: tuple[Key, ...], schema: DataSchema, columns: tuple[DataColumn, ...]
) -> tuple[Key, ...]:
    """Reorder already validated complete keys without reading unchanged cells."""
    positions = {slot: index for index, slot in enumerate(schema.key_fields)}
    return tuple(tuple(key[positions[column.slot_id]] for column in columns) for key in keys)


def _take(frame: pd.DataFrame, slot: str, indices: pa.Int64Array | list[int | None]) -> pd.Series:
    values = pa.array(frame[slot], from_pandas=True)
    if isinstance(indices, list):
        indices = pa.array(indices, type=pa.int64())
    return pd.Series(pd.arrays.ArrowExtensionArray(values.take(indices)))


def _validate_combine_sources(
    program: FrameCombineProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
    unique_keys: tuple[UniqueKeys, ...],
) -> list[tuple[Key, ...] | None]:
    validated: list[tuple[Key, ...] | None] = []
    for index, (schema, rows, tables) in enumerate(
        zip((program.left_schema, program.right_schema), inputs, attachments, strict=True)
    ):
        validated.append(
            validate_frozen_coordinates(
                schema,
                rows,
                tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in tables),
                unique_keys=unique_keys[index] if unique_keys else UniqueKeys(),
            )
        )
    return validated


def combine_frame_data(
    program: FrameCombineProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
    *,
    unique_keys: tuple[UniqueKeys, ...] = (),
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...], DataSchema]:
    """Resolve frozen coordinates, then reuse the complete joint-key/value kernel."""
    if len(inputs) != 2 or len(attachments) != 2:
        _fail("two complete Frame inputs with their attachments", str(len(inputs)))
    validated = _validate_combine_sources(program, inputs, attachments, unique_keys)
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
                    validated[side] = None
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
            validated[side] = None
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

    def numeric_schema(schema: DataSchema, axes: tuple[DataAxis, ...]) -> DataSchema:
        time_tokens = {
            axis.token for axis in axes if isinstance(axis, (CalendarDataAxis, IntervalDataAxis))
        }
        return replace(
            schema,
            axes=axes,
            comparisons=tuple(
                fact
                for fact in schema.comparisons
                if not isinstance(
                    fact.binding, (CalendarComparisonBinding, IntervalComparisonBinding)
                )
                or fact.binding.current_axis_token in time_tokens
            ),
        )

    mapped = replace(
        program,
        left_schema=numeric_schema(program.left_schema, tuple(left_axes)),
        right_schema=numeric_schema(program.right_schema, tuple(right_axes)),
    )
    resolved, carried = _resolve_comparison_facts(program, attachments, tuple(output_attachments))
    return combine_frames(mapped, tuple(frames), tuple(validated)), carried, resolved


def combine_frames(
    program: FrameCombineProgram,
    inputs: tuple[pd.DataFrame, ...],
    validated_keys: tuple[tuple[Key, ...] | None, ...] | None = None,
) -> pd.DataFrame:
    """Pair full actual keys in left-first order and calculate every named value."""
    if len(inputs) != 2:
        _fail("two explicit complete inputs", str(len(inputs)))
    left, right = inputs
    a, b, output = program.left_schema, program.right_schema, program.output_schema
    partners = dict(program.operation.pairs)
    left_columns = tuple(c for axis in a.axes for c in axis.columns)
    right_columns = tuple(c for i in range(len(a.axes)) for c in b.axes[partners[i]].columns)
    left_known = validated_keys[0] if validated_keys is not None else None
    right_known = validated_keys[1] if validated_keys is not None else None
    left_keys, left_index = _index(left, left_columns, left_known)
    right_keys, right_index = _index(
        right,
        right_columns,
        _ordered_keys(right_known, b, right_columns) if right_known is not None else None,
    )
    keys = left_keys + [key for key in right_keys if key not in left_index]
    left_rows = pa.array([left_index.get(key) for key in keys], type=pa.int64())
    right_rows = pa.array([right_index.get(key) for key in keys], type=pa.int64())
    left_present, right_present = left_rows.is_valid(), right_rows.is_valid()
    columns: dict[str, pd.Series] = {}
    for target, source, other in zip(output.key_fields, left_columns, right_columns, strict=True):
        left_values = pa.array(_take(left, source.slot_id, left_rows))
        right_values = pa.array(_take(right, other.slot_id, right_rows))
        values = pc.if_else(left_present, left_values, right_values)
        columns[target] = pd.Series(pd.arrays.ArrowExtensionArray(values))
    for i, path in enumerate(output.value_paths):
        side, original = path[0], path[1:]
        if side not in ("left", "right"):
            continue
        schema, frame, indices = (a, left, left_rows) if side == "left" else (b, right, right_rows)
        column = schema.values[schema.value_paths.index(original)]
        columns[output.values[i].slot_id] = _take(frame, column.slot_id, indices)
    columns[output.presence[0].slot_id] = pd.Series(left_present, dtype=bool)
    columns[output.presence[1].slot_id] = pd.Series(right_present, dtype=bool)
    inherited = 2
    for schema, frame, indices in ((a, left, left_rows), (b, right, right_rows)):
        for column in schema.presence:
            columns[output.presence[inherited].slot_id] = (
                _take(frame, column.slot_id, indices).fillna(False).astype(bool)
            )
            inherited += 1
    return pd.DataFrame(
        columns, columns=[c.slot_id for c in output.columns], index=pd.RangeIndex(len(keys))
    )


def _comparison_values(
    x: pd.Series, y: pd.Series, absolute_type: DataType
) -> tuple[pd.Series, pd.Series]:
    """Apply the existing nullable numeric policies after coordinate presence."""

    x_missing, y_missing = typed_null_mask(x), typed_null_mask(y)
    valid = ~(x_missing | y_missing)
    x, y = x.mask(~valid, pd.NA), y.mask(~valid, pd.NA)
    if isinstance(absolute_type, DurationDataType):
        # Convert once to original ticks; abs(min_int64) is a Python int.
        baseline_ticks: list[int | None] = (
            pa.array(y, type=pa.duration(absolute_type.unit), from_pandas=True)
            .cast(pa.int64())
            .to_pylist()
        )
        delta = duration_arithmetic(
            "subtract",
            x,
            y,
            left_type=absolute_type,
            right_type=absolute_type,
            output=absolute_type,
        )
        ticks = pd.Series(
            pa.array(delta, type=pa.duration(absolute_type.unit), from_pandas=True)
            .cast(pa.int64())
            .to_pylist(),
            dtype=object,
        ).mask(~valid, pd.NA)
        denominator = pd.Series(
            [pd.NA if tick is None else abs(tick) for tick in baseline_ticks], dtype=object
        )
        relative = nullable_arithmetic(
            "divide",
            ticks,
            denominator,
            exact_duration=True,
        )
    else:
        delta = nullable_arithmetic("subtract", x, y)
        if absolute_type == ScalarDataType("float64"):
            delta = finish_float64(delta, "Frame.compare absolute")
        denominator = y.abs()
        relative = nullable_arithmetic(
            "divide",
            delta,
            denominator,
        )
    return delta, finish_float64(relative, "Frame.compare relative")


def compare_frame_data(
    program: FrameCompareProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
    *,
    unique_keys: tuple[UniqueKeys, ...] = (),
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...], DataSchema]:
    """Compare all named baselines against one current actual full-key index."""
    if len(inputs) != len(program.input_schemas) or len(attachments) != len(inputs):
        _fail("one current input followed by every named baseline", str(len(inputs)))
    validated = tuple(
        validate_frozen_coordinates(
            schema,
            rows,
            tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in group),
            unique_keys=unique_keys[index] if unique_keys else UniqueKeys(),
        )
        for index, (schema, rows, group) in enumerate(
            zip(program.input_schemas, inputs, attachments, strict=True)
        )
    )
    current = program.input_schemas[0]
    output = program.output_schema
    current_columns = tuple(column for axis in current.axes for column in axis.columns)
    current_keys, current_index = _index(inputs[0], current_columns, validated[0])
    all_keys = list(current_keys)
    known_keys = set(all_keys)
    indices = [current_index]
    frames = [inputs[0]]
    key_columns = [current_columns]
    current_only = replace(
        current,
        values=(),
        presence=(),
        projection_columns=(),
        value_paths=(),
        presence_paths=(),
        comparisons=(),
    )
    current_required = coordinate_attachments(current_only)
    output_attachments = [
        attachment for attachment in attachments[0] if attachment.name in current_required
    ]
    for item, schema, rows, group, fact in zip(
        program.operation.baselines,
        program.input_schemas[1:],
        inputs[1:],
        attachments[1:],
        output.comparisons[: len(program.operation.baselines)],
        strict=True,
    ):
        frame = rows.copy(deep=False)
        partners = dict(item.pairs)
        columns = tuple(
            column for i in range(len(current.axes)) for column in schema.axes[partners[i]].columns
        )
        binding = fact.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            map_baseline_time(
                frame,
                rows,
                current,
                schema,
                item,
                binding,
                partners,
                attachments[0],
                group,
                output_attachments,
            )
        keys, baseline_index = _index(
            frame,
            columns,
            _ordered_keys(validated[len(indices)], schema, columns)
            if not isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding))
            else None,
        )
        for key in keys:
            if key not in known_keys:
                all_keys.append(key)
                known_keys.add(key)
        indices.append(baseline_index)
        frames.append(frame)
        key_columns.append(columns)
    takes = [pa.array([index.get(key) for key in all_keys], type=pa.int64()) for index in indices]
    result: dict[str, pd.Series] = {}
    for offset, column in enumerate(output.columns[: len(current_columns)]):
        coordinates = pa.array(_take(frames[0], key_columns[0][offset].slot_id, takes[0]))
        for frame, columns, take in zip(frames[1:], key_columns[1:], takes[1:], strict=True):
            other = pa.array(_take(frame, columns[offset].slot_id, take))
            coordinates = pc.coalesce(coordinates, other)
        result[column.slot_id] = pd.Series(pd.arrays.ArrowExtensionArray(coordinates))
    count = len(current.values)
    for index, column in enumerate(current.values):
        result[output.values[index].slot_id] = _take(frames[0], column.slot_id, takes[0])
    for side, schema in enumerate(program.input_schemas[1:], 1):
        start = count + (side - 1) * 3 * count
        for index, path in enumerate(current.value_paths):
            baseline_value = schema.values[schema.value_paths.index(path)]
            y = _take(frames[side], baseline_value.slot_id, takes[side])
            result[output.values[start + index].slot_id] = y
            x = result[output.values[index].slot_id]
            absolute = output.values[start + count + index]
            delta, relative = _comparison_values(x, y, absolute.dtype)
            result[absolute.slot_id] = delta
            result[output.values[start + 2 * count + index].slot_id] = relative
    for side, take in enumerate(takes):
        result[output.presence[side].slot_id] = pd.Series(
            pd.arrays.ArrowExtensionArray(take.is_valid())
        )
    offset = len(takes)
    for schema, frame, take in zip(program.input_schemas, frames, takes, strict=True):
        for column in schema.presence:
            result[output.presence[offset].slot_id] = _take(frame, column.slot_id, take).fillna(
                False
            )
            offset += 1
    resolved, carried = _resolve_comparison_facts(program, attachments, tuple(output_attachments))
    return (
        pd.DataFrame(
            result,
            columns=[column.slot_id for column in output.columns],
            index=pd.RangeIndex(len(all_keys)),
        ),
        carried,
        resolved,
    )


def _resolve_value_contexts(resolved: DataSchema) -> DataSchema:
    # Compare/combine outputs have no decompositions; only value-bearing relations
    # produce a public comparison group in this completed schema.
    public_comparisons = {comparison_id(fact) for fact in resolved.comparisons if fact.values}
    comparison_ids = {
        comparison_id(fact): comparison_id(fact)
        for fact in resolved.comparisons
        if comparison_id(fact) in public_comparisons
    }
    context_tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {
        axis.token: (axis.token, "self") for axis in resolved.axes
    }

    def current_context(context: FrameValueContext | None) -> FrameValueContext | None:
        return remap_context(context, context_tokens, comparison_ids)

    resolved = replace(
        resolved,
        comparisons=tuple(
            replace(
                fact,
                contexts=tuple(
                    (path, current_context(current), current_context(baseline))
                    for path, current, baseline in fact.contexts
                ),
            )
            for fact in resolved.comparisons
        ),
        value_contexts=tuple(
            (name, rebound)
            for name, context in resolved.value_contexts
            if (rebound := current_context(context)) is not None
        ),
    )
    return resolved


def _record_attachment(owned: dict[str, OutputAttachment], attachment: OutputAttachment) -> None:
    previous = owned.get(attachment.name)
    if previous is not None and (
        previous.schema != attachment.schema or not previous.rows.equals(attachment.rows)
    ):
        _fail("one exact payload for each required attachment owner", attachment.name)
    owned[attachment.name] = attachment


def _attachment_directory(
    axis: CalendarDataAxis | IntervalDataAxis, group: dict[str, OutputAttachment]
) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
    assert axis.frozen is not None
    table = group[axis.frozen.directory_name]
    return (
        read_calendar_directory(axis, table.schema, table.rows)
        if isinstance(axis, CalendarDataAxis)
        else read_interval_directory(axis, table.schema, table.rows)
    )


def _trim_changed_dirs(
    program: FrameCombineProgram,
    output: DataSchema,
    left: tuple[OutputAttachment, ...],
    tables: dict[str, OutputAttachment],
) -> DataSchema:
    axes = list(output.axes)
    left_tables = {attachment.name: attachment for attachment in left}
    for left_index, _ in program.operation.pairs:
        source, target = program.left_schema.axes[left_index], axes[left_index]
        if (
            isinstance(source, CalendarDataAxis)
            and isinstance(target, CalendarDataAxis)
            and target.frozen is not None
            and target.frozen.direct is not None
            and _attachment_directory(source, left_tables).buckets
            != _attachment_directory(target, tables).buckets
        ):
            axes[left_index] = replace(target, frozen=replace(target.frozen, direct=None))
    return replace(output, axes=tuple(axes))


def _copy_comparison_attachments(
    schema: DataSchema,
    output: DataSchema,
    source_tables: dict[str, OutputAttachment],
    facts: tuple[DataComparison, ...],
) -> list[OutputAttachment]:
    additions: list[OutputAttachment] = []
    for original, copied in zip(schema.comparisons, facts, strict=True):
        old, new = original.binding, copied.binding
        if not isinstance(new, (CalendarComparisonBinding, IntervalComparisonBinding)):
            continue
        assert isinstance(old, (CalendarComparisonBinding, IntervalComparisonBinding))
        frozen = _attachment_directory(old.baseline, source_tables)
        if isinstance(new.baseline, CalendarDataAxis):
            assert isinstance(frozen, FrozenCalendarDirectory)
            copied_directory = replace(frozen, axis=new.baseline)
            layout, rows = (
                calendar_directory_schema(new.baseline),
                calendar_directory_rows(copied_directory),
            )
        else:
            assert isinstance(frozen, FrozenIntervalDirectory)
            copied_interval = replace(frozen, axis=new.baseline)
            layout, rows = (
                interval_directory_schema(new.baseline),
                interval_directory_rows(copied_interval),
            )
        assert new.baseline.frozen is not None
        additions.append(OutputAttachment(new.baseline.frozen.directory_name, layout, rows))
        old_axis = next(axis for axis in schema.axes if axis.token == old.current_axis_token)
        new_axis = next(axis for axis in output.axes if axis.token == new.current_axis_token)
        assert isinstance(old_axis, (CalendarDataAxis, IntervalDataAxis)) and isinstance(
            new_axis, (CalendarDataAxis, IntervalDataAxis)
        )
        original_map = source_tables[old.mapping_name]
        map_schema = comparison_mapping_schema(new_axis, new)
        mapping = frozen_key_table(
            new_axis.columns, frozen_key_rows(original_map.rows, old_axis.columns)
        )
        for column in map_schema.values:
            mapping[column.slot_id] = (
                original_map.rows[column.slot_id].reset_index(drop=True).copy()
            )
        additions.append(OutputAttachment(new.mapping_name, map_schema, mapping))
    return additions


def _resolve_comparison_facts(
    program: FrameCompareProgram | FrameCombineProgram,
    inputs: tuple[tuple[OutputAttachment, ...], ...],
    produced: tuple[OutputAttachment, ...],
) -> tuple[DataSchema, tuple[OutputAttachment, ...]]:
    """Resolve retained certification from exact finite input/output domains."""

    output = program.output_schema
    roots, specs = _comparison_attachment_sources(program)
    tables = {attachment.name: attachment for attachment in produced}
    retained = list(roots)
    additions: list[OutputAttachment] = []

    if isinstance(program, FrameCombineProgram):
        output = _trim_changed_dirs(program, output, inputs[0], tables)

    for (schema, value_prefix, presence_prefix, namespace, axis_pairs, endpoint_path), group in zip(
        specs, inputs, strict=True
    ):
        source_tables = {attachment.name: attachment for attachment in group}
        same: set[str] = set()
        for source_index, output_index in axis_pairs:
            source, target = schema.axes[source_index], output.axes[output_index]
            if isinstance(source, (CalendarDataAxis, IntervalDataAxis)) and type(source) is type(
                target
            ):
                assert isinstance(target, (CalendarDataAxis, IntervalDataAxis))
                if (
                    _attachment_directory(source, source_tables).buckets
                    == _attachment_directory(target, tables).buckets
                ):
                    same.add(source.token)
        facts = transport_comparisons(
            schema,
            output.axes,
            value_prefix=value_prefix,
            presence_prefix=presence_prefix,
            namespace=namespace,
            axis_pairs=axis_pairs,
            endpoint_path=endpoint_path,
            same_domains=frozenset(same),
        )
        retained.extend(facts)
        additions.extend(_copy_comparison_attachments(schema, output, source_tables, facts))
    resolved = replace(output, comparisons=tuple(retained))
    resolved = _resolve_value_contexts(resolved)
    required = coordinate_attachments(resolved)
    owned: dict[str, OutputAttachment] = {}
    for attachment in (*produced, *additions):
        if attachment.name not in required:
            continue
        _record_attachment(owned, attachment)
    # The output current axis comes from the first input; only its unchanged
    # direct ancestry can remain outside the newly produced/rebased attachments.
    for attachment in inputs[0]:
        if attachment.name not in required or attachment.name in tables:
            continue
        _record_attachment(owned, attachment)
    if set(owned) != set(required):
        _fail("all required frozen coordinate attachments", repr(tuple(owned)))
    return resolved, tuple(owned[name] for name in required)


_AttachmentSource: TypeAlias = tuple[
    DataSchema, tuple[str, ...], tuple[str, ...], str, tuple[tuple[int, int], ...], tuple[str, ...]
]


def _comparison_attachment_sources(
    program: FrameCompareProgram | FrameCombineProgram,
) -> tuple[tuple[DataComparison, ...], tuple[_AttachmentSource, ...]]:
    output = program.output_schema
    specs: tuple[_AttachmentSource, ...]
    if isinstance(program, FrameCompareProgram):
        roots = output.comparisons[: len(program.operation.baselines)]
        specs = (
            (
                program.input_schemas[0],
                (),
                ("current", "presence"),
                "current",
                tuple((i, i) for i in range(len(output.axes))),
                ("current", "present"),
            ),
            *(
                (
                    schema,
                    (item.alias, "baseline"),
                    (item.alias, "presence"),
                    item.alias,
                    tuple((j, i) for i, j in item.pairs),
                    (item.alias, "present"),
                )
                for item, schema in zip(
                    program.operation.baselines, program.input_schemas[1:], strict=True
                )
            ),
        )
    else:
        roots = ()
        specs = (
            (
                program.left_schema,
                ("left",),
                ("left",),
                "left",
                tuple((i, i) for i in range(len(output.axes))),
                ("left_present",),
            ),
            (
                program.right_schema,
                ("right",),
                ("right",),
                "right",
                tuple((j, i) for i, j in program.operation.pairs),
                ("right_present",),
            ),
        )
    return roots, specs
