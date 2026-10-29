"""Pure complete-key ordinary pairing, independent of semantic endpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._temporal_grid import CivilShiftAbsent
from marivo.analysis.compiler.query_program import FrameCombineProgram, FrameCompareProgram
from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    FrozenComparisonAbsent,
    FrozenComparisonEntry,
    FrozenComparisonMatch,
    FrozenDirectMatch,
    FrozenIntervalDirectory,
    FrozenKey,
    build_partial_calendar_map,
    build_partial_interval_map,
    calendar_directory_rows,
    calendar_directory_schema,
    calendar_match_intents,
    comparison_mapping_schema,
    frozen_key_rows,
    frozen_key_table,
    interval_directory_rows,
    interval_directory_schema,
    merge_equal_calendar_directories,
    merge_equal_interval_directories,
    paired_mapping_schema,
    read_calendar_directory,
    read_direct_mapping,
    read_interval_directory,
    require_compatible_calendar_axes,
    validate_frozen_coordinates,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_float64,
    nullable_arithmetic,
)

Key: TypeAlias = tuple[tuple[DataType, object], ...]


def _fail(
    expected: str,
    received: str,
    *,
    repair: str = "Use unique complete keys and compatible complete axes.",
    received_limit: int = 320,
) -> None:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair=repair,
        stage="frame_combine",
        received_limit=received_limit,
    )


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


def _take(frame: pd.DataFrame, slot: str, indices: list[int | None]) -> pd.Series:
    values = pa.array(frame[slot], from_pandas=True)
    return pd.Series(pd.arrays.ArrowExtensionArray(values.take(pa.array(indices, type=pa.int64()))))


def combine_frame_data(
    program: FrameCombineProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...], DataSchema]:
    """Resolve frozen coordinates, then reuse the complete joint-key/value kernel."""
    if len(inputs) != 2 or len(attachments) != 2:
        _fail("two complete Frame inputs with their attachments", str(len(inputs)))
    validated: list[tuple[Key, ...] | None] = []
    for schema, rows, tables in zip(
        (program.left_schema, program.right_schema), inputs, attachments, strict=True
    ):
        validated.append(
            validate_frozen_coordinates(
                schema, rows, tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in tables)
            )
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
    for i, path in enumerate(output.value_paths):
        side, original = path[0], path[1:]
        if side not in ("left", "right"):
            continue
        schema, frame, indices = (a, left, left_rows) if side == "left" else (b, right, right_rows)
        column = schema.values[schema.value_paths.index(original)]
        columns[output.values[i].slot_id] = _take(frame, column.slot_id, indices)
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


def _comparison_values(
    x: pd.Series, y: pd.Series, absolute_type: DataType
) -> tuple[pd.Series, pd.Series]:
    """Apply the existing nullable numeric policies after coordinate presence."""
    from marivo.analysis.core.data_validation import typed_null_mask

    x_missing, y_missing = typed_null_mask(x), typed_null_mask(y)
    valid = ~(x_missing | y_missing)
    x, y = x.mask(~valid, pd.NA), y.mask(~valid, pd.NA)
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


def _calendar_relation(
    current: FrozenCalendarDirectory,
    baseline: FrozenCalendarDirectory,
    tables: dict[str, OutputAttachment],
    rule: CalendarCorrespondence | None,
) -> tuple[FrozenComparisonEntry, ...]:
    """Consume saved direct facts or the accepted matcher without reconstructing dates."""
    assert baseline.axis.frozen is not None
    direct = baseline.axis.frozen.direct
    saved: tuple[FrozenComparisonEntry, ...] | None = None
    if direct is not None:
        attachment = (
            tables[direct.axis.frozen.directory_name] if direct.axis.frozen is not None else None
        )
        assert attachment is not None
        origin = read_calendar_directory(direct.axis, attachment.schema, attachment.rows)
        require_compatible_calendar_axes(current.axis, origin.axis)
        table = tables[direct.mapping_name]
        entries = read_direct_mapping(baseline.axis, table.schema, table.rows, origin, baseline)
        saved = tuple(
            FrozenComparisonMatch(entry.origin_key, entry.target_key)
            if isinstance(entry, FrozenDirectMatch)
            else FrozenComparisonAbsent(entry.origin_key, entry.reason)
            for entry in entries
        )
        direct_rule = CalendarCorrespondence(direct.kind, direct.by)
        expected = _matched_calendar_relation(origin, baseline, direct_rule)
        if saved != expected:
            _fail(
                "direct map consistent with its frozen target intentions and boundaries",
                repr(saved),
            )
        if current.buckets != origin.buckets:
            if rule is not None:
                return _matched_calendar_relation(current, baseline, rule)
            _fail(
                "the complete frozen immediate current origin domain for inferred match",
                "baseline origin differs from current calendar",
                repair="Pass an explicit match from the current to baseline frozen domain, or supply a baseline shifted directly from this current domain.",
            )
        if (
            rule is not None
            and rule.kind != "ordinal"
            and tuple(intent.result for intent in calendar_match_intents(current, rule))
            != tuple(intent.result for intent in calendar_match_intents(current, direct_rule))
        ):
            _fail("explicit match consistent with the frozen direct target intentions", repr(rule))
    if rule is not None:
        result = _matched_calendar_relation(current, baseline, rule)
        if saved is not None and result != saved:
            _fail("explicit match consistent with the consumed direct relation", repr(rule))
        return result
    if saved is not None:
        return saved
    identities = {replace(bucket, key=(), ordinal=0): bucket.key for bucket in baseline.buckets}
    return tuple(
        FrozenComparisonMatch(bucket.key, target)
        if (target := identities.get(replace(bucket, key=(), ordinal=0))) is not None
        else FrozenComparisonAbsent(bucket.key, "unmatched")
        for bucket in current.buckets
    )


def _matched_calendar_relation(
    current: FrozenCalendarDirectory,
    baseline: FrozenCalendarDirectory,
    rule: CalendarCorrespondence,
) -> tuple[FrozenComparisonEntry, ...]:
    pairs = build_partial_calendar_map(current, baseline, rule)
    reasons = (
        {
            intent.key: intent.result.reason
            for intent in calendar_match_intents(current, rule)
            if isinstance(intent.result, CivilShiftAbsent)
        }
        if rule.kind != "ordinal"
        else {}
    )
    return tuple(
        FrozenComparisonMatch(pair.left_key, pair.right_key)
        if pair.right_key is not None
        else FrozenComparisonAbsent(pair.left_key, reasons.get(pair.left_key, "unmatched"))
        for pair in pairs
        if pair.left_key is not None
    )


def _time_domain_summary(directory: FrozenCalendarDirectory | FrozenIntervalDirectory) -> str:
    if not directory.buckets:
        return "empty"
    if isinstance(directory, FrozenCalendarDirectory):
        start = min(bucket.actual_start for bucket in directory.buckets)
        end = max(bucket.actual_end for bucket in directory.buckets)
    else:
        start = min(bucket.start for bucket in directory.buckets)
        end = max(bucket.end for bucket in directory.buckets)
    return f"[{start.isoformat().replace('+00:00', 'Z')},{end.isoformat().replace('+00:00', 'Z')})"


def _unmapped_time_details(
    alias: str,
    current: FrozenCalendarDirectory | FrozenIntervalDirectory,
    baseline: FrozenCalendarDirectory | FrozenIntervalDirectory,
    rule: CalendarCorrespondence | None,
    key: FrozenKey,
) -> str:
    effective = (
        f"{rule.kind}={rule.by}"
        if rule is not None and rule.kind != "ordinal"
        else "ordinal"
        if rule is not None
        else "equality"
    )
    if isinstance(baseline, FrozenCalendarDirectory):
        frozen = baseline.axis.frozen
        assert frozen is not None
        if rule is None and frozen.direct is not None:
            effective = f"frozen_direct({frozen.direct.kind}={frozen.direct.by})"
        definition = frozen.definition
        grain = (
            f"{definition.unit} step={definition.step}"
            if isinstance(definition, BuiltinCalendarData)
            else "named calendar"
        )
        timezone = baseline.axis.timezone
        calendar_bucket = next((bucket for bucket in baseline.buckets if bucket.key == key), None)
        bounds = (
            (
                calendar_bucket.actual_start.isoformat().replace("+00:00", "Z"),
                calendar_bucket.actual_end.isoformat().replace("+00:00", "Z"),
            )
            if calendar_bucket
            else None
        )
    else:
        grain = "interval"
        interval_bucket = next((bucket for bucket in baseline.buckets if bucket.key == key), None)
        timezone = interval_bucket.timezone if interval_bucket else "unknown"
        bounds = (
            (
                interval_bucket.start.isoformat().replace("+00:00", "Z"),
                interval_bucket.end.isoformat().replace("+00:00", "Z"),
            )
            if interval_bucket
            else None
        )
    return (
        f"alias={alias!r}; rule={effective}; grain={grain}; tz={timezone!r}; "
        f"current={_time_domain_summary(current)}; baseline={_time_domain_summary(baseline)}; "
        f"key={tuple(value for _, value in key)!r}; bounds={bounds!r}"
    )


def compare_frame_data(
    program: FrameCompareProgram,
    inputs: tuple[pd.DataFrame, ...],
    attachments: tuple[tuple[OutputAttachment, ...], ...],
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...], DataSchema]:
    """Compare all named baselines against one current actual full-key index."""
    if len(inputs) != len(program.input_schemas) or len(attachments) != len(inputs):
        _fail("one current input followed by every named baseline", str(len(inputs)))
    validated = tuple(
        validate_frozen_coordinates(
            schema, rows, tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in group)
        )
        for schema, rows, group in zip(program.input_schemas, inputs, attachments, strict=True)
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
    from marivo.analysis.core.frame_structure import coordinate_attachments

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
            position = next(
                i for i, axis in enumerate(current.axes) if axis.token == binding.current_axis_token
            )
            left, right = current.axes[position], schema.axes[partners[position]]
            assert isinstance(left, (CalendarDataAxis, IntervalDataAxis)) and isinstance(
                right, (CalendarDataAxis, IntervalDataAxis)
            )
            assert left.frozen is not None and right.frozen is not None
            left_tables, right_tables = (
                {a.name: a for a in tables} for tables in (attachments[0], group)
            )
            a, b = (
                left_tables[left.frozen.directory_name],
                right_tables[right.frozen.directory_name],
            )
            if isinstance(left, CalendarDataAxis):
                assert isinstance(right, CalendarDataAxis) and isinstance(
                    binding.baseline, CalendarDataAxis
                )
                ldir = read_calendar_directory(left, a.schema, a.rows)
                rdir = read_calendar_directory(right, b.schema, b.rows)
                left_directory: FrozenCalendarDirectory | FrozenIntervalDirectory = ldir
                right_directory: FrozenCalendarDirectory | FrozenIntervalDirectory = rdir
                relation = _calendar_relation(ldir, rdir, right_tables, item.match)
                copied = replace(rdir, axis=binding.baseline)
                endpoint_rows = calendar_directory_rows(copied)
                endpoint_schema = calendar_directory_schema(binding.baseline)
            else:
                assert isinstance(right, IntervalDataAxis) and isinstance(
                    binding.baseline, IntervalDataAxis
                )
                li, ri = (
                    read_interval_directory(left, a.schema, a.rows),
                    read_interval_directory(right, b.schema, b.rows),
                )
                left_directory, right_directory = li, ri
                if item.match is not None:
                    matches = build_partial_interval_map(li, ri, item.match)
                    relation = tuple(
                        FrozenComparisonMatch(pair.left_key, pair.right_key)
                        for pair in matches
                        if pair.left_key is not None and pair.right_key is not None
                    )
                else:
                    interval_keys = {
                        (bucket.start, bucket.end, bucket.timezone): bucket.key
                        for bucket in ri.buckets
                    }
                    relation = tuple(
                        FrozenComparisonMatch(bucket.key, target)
                        if (
                            target := interval_keys.get((bucket.start, bucket.end, bucket.timezone))
                        )
                        is not None
                        else FrozenComparisonAbsent(bucket.key, "unmatched")
                        for bucket in li.buckets
                    )
                copied_interval = replace(ri, axis=binding.baseline)
                endpoint_rows = interval_directory_rows(copied_interval)
                endpoint_schema = interval_directory_schema(binding.baseline)
            assert binding.baseline.frozen is not None
            output_attachments.append(
                OutputAttachment(
                    binding.baseline.frozen.directory_name, endpoint_schema, endpoint_rows
                )
            )
            map_schema = comparison_mapping_schema(left, binding)
            mapping_rows = frozen_key_table(
                left.columns, tuple(entry.current_key for entry in relation)
            )
            targets = frozen_key_table(
                map_schema.values[:-1],
                tuple(
                    entry.baseline_key if isinstance(entry, FrozenComparisonMatch) else None
                    for entry in relation
                ),
            )
            for column in map_schema.values[:-1]:
                mapping_rows[column.slot_id] = targets[column.slot_id]
            mapping_rows["reason"] = pd.Series(
                [
                    entry.reason if isinstance(entry, FrozenComparisonAbsent) else None
                    for entry in relation
                ],
                dtype="string[pyarrow]",
            )
            output_attachments.append(
                OutputAttachment(binding.mapping_name, map_schema, mapping_rows)
            )
            reverse = {
                entry.baseline_key: entry.current_key
                for entry in relation
                if isinstance(entry, FrozenComparisonMatch)
            }
            actual = frozen_key_rows(rows, right.columns)
            unmapped = next((key for key in actual if key not in reverse), None)
            if unmapped is not None:
                _fail(
                    "every actual baseline time row mapping into the declared current domain",
                    _unmapped_time_details(
                        item.alias, left_directory, right_directory, item.match, unmapped
                    ),
                    repair="Check the offset's grain unit and both time domains. Adjust the rule or inputs; filter the baseline only if intended, or use align for a general endpoint time union.",
                    received_limit=2048,
                )
            encoded = frozen_key_table(right.columns, tuple(reverse[key] for key in actual))
            for column in right.columns:
                frame[column.slot_id] = encoded[column.slot_id]
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
    takes = [[index.get(key) for key in all_keys] for index in indices]
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
            [index is not None for index in take], dtype="bool[pyarrow]"
        )
    offset = len(takes)
    for schema, frame, take in zip(program.input_schemas, frames, takes, strict=True):
        for column in schema.presence:
            result[output.presence[offset].slot_id] = pd.Series(
                [
                    False if index is None else bool(frame[column.slot_id].iloc[index])
                    for index in take
                ],
                dtype="bool[pyarrow]",
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


def _resolve_comparison_facts(
    program: FrameCompareProgram | FrameCombineProgram,
    inputs: tuple[tuple[OutputAttachment, ...], ...],
    produced: tuple[OutputAttachment, ...],
) -> tuple[DataSchema, tuple[OutputAttachment, ...]]:
    """Resolve retained certification from exact finite input/output domains."""
    from marivo.analysis.core.frame_expression import transport_comparisons
    from marivo.analysis.core.frame_structure import coordinate_attachments

    output = program.output_schema
    specs: tuple[
        tuple[
            DataSchema,
            tuple[str, ...],
            tuple[str, ...],
            str,
            tuple[tuple[int, int], ...],
            tuple[str, ...],
        ],
        ...,
    ]
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
    tables = {attachment.name: attachment for attachment in produced}
    retained = list(roots)
    additions: list[OutputAttachment] = []

    def directory(
        axis: CalendarDataAxis | IntervalDataAxis, group: dict[str, OutputAttachment]
    ) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
        assert axis.frozen is not None
        table = group[axis.frozen.directory_name]
        return (
            read_calendar_directory(axis, table.schema, table.rows)
            if isinstance(axis, CalendarDataAxis)
            else read_interval_directory(axis, table.schema, table.rows)
        )

    if isinstance(program, FrameCombineProgram):
        axes = list(output.axes)
        left_tables = {attachment.name: attachment for attachment in inputs[0]}
        for left_index, _ in program.operation.pairs:
            source, target = program.left_schema.axes[left_index], axes[left_index]
            if (
                isinstance(source, CalendarDataAxis)
                and isinstance(target, CalendarDataAxis)
                and target.frozen is not None
                and target.frozen.direct is not None
                and directory(source, left_tables).buckets != directory(target, tables).buckets
            ):
                axes[left_index] = replace(target, frozen=replace(target.frozen, direct=None))
        output = replace(output, axes=tuple(axes))

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
                if directory(source, source_tables).buckets == directory(target, tables).buckets:
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
        for original, copied in zip(schema.comparisons, facts, strict=True):
            old, new = original.binding, copied.binding
            if not isinstance(new, (CalendarComparisonBinding, IntervalComparisonBinding)):
                continue
            assert isinstance(old, (CalendarComparisonBinding, IntervalComparisonBinding))
            frozen = directory(old.baseline, source_tables)
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
    resolved = replace(output, comparisons=tuple(retained))
    from marivo.analysis.core.frame_interpretation import FrameValueContext, remap_context
    from marivo.analysis.core.frame_schema import comparison_id

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
    required = coordinate_attachments(resolved)
    owned: dict[str, OutputAttachment] = {}
    for attachment in (*produced, *additions):
        if attachment.name not in required:
            continue
        previous = owned.get(attachment.name)
        if previous is not None and (
            previous.schema != attachment.schema or not previous.rows.equals(attachment.rows)
        ):
            _fail("one exact payload for each required attachment owner", attachment.name)
        owned[attachment.name] = attachment
    # The output current axis comes from the first input; only its unchanged
    # direct ancestry can remain outside the newly produced/rebased attachments.
    for attachment in inputs[0]:
        if attachment.name not in required or attachment.name in tables:
            continue
        previous = owned.get(attachment.name)
        if previous is not None and (
            previous.schema != attachment.schema or not previous.rows.equals(attachment.rows)
        ):
            _fail("one exact payload for each required attachment owner", attachment.name)
        owned[attachment.name] = attachment
    if set(owned) != set(required):
        _fail("all required frozen coordinate attachments", repr(tuple(owned)))
    return resolved, tuple(owned[name] for name in required)
