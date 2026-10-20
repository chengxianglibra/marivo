"""Default Attribute kernels over checked complete Frame stage inputs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

import pandas as pd

from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.attribute import (
    AttributeInputProgram,
    AttributeMatrixSource,
    AttributeProgram,
    AttributeProjectProgram,
)
from marivo.analysis.core.data_schema import CalendarDataAxis, DataSchema, PairedDataAxis
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_expression import AttributeProjectValues
from marivo.analysis.core.frame_structure import coordinate_attachments
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarBucket,
    FrozenKey,
    FrozenScalar,
    frozen_key_rows,
    frozen_key_table,
    read_calendar_directory,
    require_compatible_calendar_axes,
    require_matchable_calendar_directory,
    validate_frozen_coordinates,
)
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.query_components import AttributeRootInputs
from marivo.analysis.methods.frame_comparison import _take
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_float64,
    nullable_arithmetic,
)


def _validated(
    schema: DataSchema, rows: pd.DataFrame, attachments: tuple[OutputAttachment, ...]
) -> None:
    validate_frozen_coordinates(
        schema, rows, tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in attachments)
    )


def _calendar_keys(
    operation: AttributeProjectValues,
    schema: DataSchema,
    attachments: tuple[OutputAttachment, ...],
) -> dict[FrozenKey, FrozenKey | None]:
    assert operation.calendar is not None and operation.target_calendar is not None
    axis = schema.axes[operation.calendar]
    assert isinstance(axis, (CalendarDataAxis, PairedDataAxis))
    endpoint = (
        axis
        if isinstance(axis, CalendarDataAxis)
        else axis.left
        if operation.side == "left"
        else axis.right
    )
    if not isinstance(endpoint, CalendarDataAxis):
        reject(
            "a calendar endpoint for calendar parent projection",
            repr(endpoint),
            "Use a saved calendar parent with the original complete endpoint calendar.",
            "session.attribute",
        )
    target = freeze_calendar(
        operation.target_calendar,
        token="attribute.calendar",
        name="calendar",
        key=DataColumn("calendar", "calendar", ScalarDataType("int64"), False),
    )
    require_compatible_calendar_axes(endpoint, target.axis)
    tables = {a.name: a for a in attachments}
    assert endpoint.frozen is not None
    original = tables[endpoint.frozen.directory_name]
    saved = read_calendar_directory(endpoint, original.schema, original.rows)
    fresh = read_calendar_directory(target.axis, target.directory.schema, target.directory.rows)

    def facts(
        bucket: FrozenCalendarBucket,
    ) -> tuple[str, int, datetime, datetime, datetime, datetime]:
        return (
            bucket.nominal_label,
            bucket.ordinal,
            bucket.nominal_start,
            bucket.nominal_end,
            bucket.actual_start,
            bucket.actual_end,
        )

    if tuple(map(facts, saved.buckets)) != tuple(map(facts, fresh.buckets)):
        reject(
            "the complete saved endpoint calendar directory",
            repr(target.axis),
            "Provide an explicit CalendarAxis with the original finite bucket boundaries.",
            "session.attribute",
        )
    mapping = {a.key: b.key for a, b in zip(saved.buckets, fresh.buckets, strict=True)}
    if isinstance(axis, CalendarDataAxis):
        return dict(mapping)
    # Full-bucket admission belongs to the original complete paired directories,
    # including rows removed by the current selected parent filter.
    for end in (axis.left, axis.right):
        if not isinstance(end, CalendarDataAxis):
            reject(
                "calendar endpoints for a paired calendar parent",
                repr(end),
                "Use paired calendar endpoints with the original finite calendars.",
                "session.attribute",
            )
        assert end.frozen is not None
        table = tables[end.frozen.directory_name]
        directory = read_calendar_directory(end, table.schema, table.rows)
        require_matchable_calendar_directory(directory)
    table = tables[axis.mapping_name]
    width = len(axis.left.columns)
    columns = (
        table.schema.values[:width] if operation.side == "left" else table.schema.values[width:]
    )
    result: dict[FrozenKey, FrozenKey | None] = {}
    for key, value in zip(
        frozen_key_rows(table.rows, axis.columns), frozen_key_rows(table.rows, columns), strict=True
    ):
        result[key] = None if all(v is None for _, v in value) else mapping[value]
    return result


def _projected(
    operation: AttributeProjectValues,
    schema: DataSchema,
    rows: pd.DataFrame,
    attachments: tuple[OutputAttachment, ...],
) -> tuple[tuple[FrozenKey, tuple[FrozenScalar | None, ...] | None], ...]:
    _validated(schema, rows, attachments)
    parent_keys, _ = _index(rows, tuple(c for a in schema.axes for c in a.columns))
    calendar = (
        _calendar_keys(operation, schema, attachments) if operation.calendar is not None else {}
    )
    groups = tuple(frozen_key_rows(rows, schema.axes[i].columns) for i in operation.ordinary)
    calendar_rows = (
        frozen_key_rows(rows, schema.axes[operation.calendar].columns)
        if operation.calendar is not None
        else ()
    )
    result: list[tuple[FrozenKey, tuple[FrozenScalar | None, ...] | None]] = []
    for i, key in enumerate(parent_keys):
        values = tuple(v for group in groups for _, v in group[i])
        if operation.calendar is not None:
            mapped = calendar[calendar_rows[i]]
            if mapped is None:
                if operation.how == "exact":
                    reject(
                        "two structurally projected endpoints for every selected parent",
                        repr(key),
                        "Use parent_how='outer' for a saved structurally missing side.",
                        "session.attribute",
                    )
                result.append((key, None))
                continue
            values += tuple(v for _, v in mapped)
        result.append((key, values))
    return tuple(result)


def project_attribute_parents(
    program: AttributeProjectProgram, rows: pd.DataFrame, attachments: tuple[OutputAttachment, ...]
) -> pd.DataFrame:
    projected = _projected(program.operation, program.input_schema, rows, attachments)
    selected = [(i, value) for i, (_, value) in enumerate(projected) if value is not None]
    columns = tuple(c for axis in program.output_schema.axes for c in axis.columns)
    result = frozen_key_table(
        columns,
        tuple(
            tuple((column.dtype, cell) for column, cell in zip(columns, value, strict=True))
            for _, value in selected
        ),
    )
    parent_columns = tuple(c for axis in program.input_schema.axes for c in axis.columns)
    for target, source in zip(program.output_schema.values, parent_columns, strict=True):
        result[target.slot_id] = _take(rows, source.slot_id, [i for i, _ in selected])
    return result


def attribute_frame_data(
    program: AttributeProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...]]:
    parent, left_domain, right_domain, left, right, left_projection, right_projection = inputs
    schemas = (
        program.parent_schema,
        program.left_domain_schema,
        program.right_domain_schema,
        program.left_schema,
        program.right_schema,
    )
    for schema, source in zip(schemas, inputs[:5], strict=True):
        _validated(schema, source.table, source.frame_attachments)
    parent_schema = program.parent_schema
    calendar = next(
        (
            i
            for i, a in enumerate(parent_schema.axes)
            if isinstance(a, (CalendarDataAxis, PairedDataAxis))
        ),
        None,
    )
    ordinary = tuple(i for i in range(len(parent_schema.axes)) if i != calendar)
    original_order = (*ordinary, *((calendar,) if calendar is not None else ()))
    maps: list[dict[FrozenKey, FrozenKey]] = []
    structural: list[set[FrozenKey]] = []
    parent_keys, _ = _index(parent.table, tuple(c for a in parent_schema.axes for c in a.columns))
    for projection in (left_projection, right_projection):
        columns = {c.slot_id: c for c in projection.columns}
        groups = projection.key_groups
        endpoint_columns = tuple(
            columns[slot]
            for i in range(len(parent_schema.axes))
            for slot in groups[original_order.index(i)]
        )
        endpoint_keys = frozen_key_rows(projection.table, endpoint_columns)
        original_columns = tuple(c for c in projection.columns if c.slot_id not in projection.keys)
        selected_keys = frozen_key_rows(projection.table, original_columns)
        side_map: dict[FrozenKey, FrozenKey] = dict(zip(endpoint_keys, selected_keys, strict=True))
        maps.append(side_map)
        structural.append(set(parent_keys) - set(selected_keys))
    if program.operation.how == "exact" and (structural[0] or structural[1]):
        reject(
            "two structurally projected endpoints for every selected parent",
            repr(structural),
            "Use parent_how='outer' for a saved structurally missing side.",
            "session.attribute",
        )
    parent_domains: list[set[FrozenKey]] = []
    fine_rows: list[dict[FrozenKey, int]] = []
    full_keys: list[list[FrozenKey]] = []
    for side_index, (domain, source, schema, positions) in enumerate(
        (
            (left_domain, left, program.left_schema, program.operation.left_parents),
            (right_domain, right, program.right_schema, program.operation.right_parents),
        )
    ):
        side_map = maps[side_index]
        domain_schema = schemas[1 + side_index]
        domain_groups = tuple(domain_schema.axes[i] for i in positions)
        domain_keys, _ = _index(domain.table, tuple(c for a in domain_groups for c in a.columns))
        parent_domains.append({side_map[key] for key in domain_keys if key in side_map})
        parent_columns = tuple(c for i in positions for c in schema.axes[i].columns)
        source_parents, _ = _index_allow_duplicate(source.table, parent_columns)
        fine_columns = tuple(
            c for i, a in enumerate(schema.axes) if i not in positions for c in a.columns
        )
        fine_keys, _ = _index_allow_duplicate(source.table, fine_columns)
        index: dict[FrozenKey, int] = {}
        for row, (pkey, fine_key) in enumerate(zip(source_parents, fine_keys, strict=True)):
            if pkey in side_map:
                index[(*side_map[pkey], *fine_key)] = row
        fine_rows.append(index)
        full_keys.append(list(index))
    if program.operation.how == "exact" and parent_domains[0] != parent_domains[1]:
        reject(
            "equal fresh complete parent domains",
            repr(parent_domains),
            "Provide matching endpoint parent domains or parent_how='outer'.",
            "session.attribute",
        )
    keys = full_keys[0] + [k for k in full_keys[1] if k not in fine_rows[0]]
    output = program.output_schema
    encoded_keys = frozen_key_table(tuple(c for a in output.axes for c in a.columns), tuple(keys))
    rows: dict[str, pd.Series] = {name: encoded_keys[name] for name in encoded_keys.columns}
    indices = ([fine_rows[0].get(k) for k in keys], [fine_rows[1].get(k) for k in keys])
    for i, path in enumerate(program.left_schema.value_paths):
        a = program.left_schema.values[i]
        b = program.right_schema.values[program.right_schema.value_paths.index(path)]
        x, y = _take(left.table, a.slot_id, indices[0]), _take(right.table, b.slot_id, indices[1])
        dtype = output.values[3 * i + 2].dtype
        delta = (
            duration_arithmetic(
                "subtract",
                x,
                y,
                left_type=a.dtype,
                right_type=b.dtype,
                output=dtype,
                zero_denominator="null",
            )
            if isinstance(dtype, DurationDataType)
            else nullable_arithmetic("subtract", x, y)
        )
        if isinstance(dtype, DecimalDataType):
            delta = delta.map(
                lambda v: Decimal(v) if pd.notna(v) and not isinstance(v, Decimal) else v
            )
        elif dtype == ScalarDataType("float64"):
            delta = finish_float64(delta, "Attribute absolute")
        for column, values in zip(output.values[3 * i : 3 * i + 3], (x, y, delta), strict=True):
            rows[column.slot_id] = values
    for column, selected in zip(output.presence, indices, strict=True):
        rows[column.slot_id] = pd.Series([i is not None for i in selected], dtype=bool)
    result = pd.DataFrame(rows, columns=[c.slot_id for c in output.columns])
    for key in reversed(output.key_fields):
        result = result.sort_values(key, na_position="first", kind="stable")
    required = coordinate_attachments(output)
    return result.reset_index(drop=True), tuple(
        a for a in parent.frame_attachments if a.name in required
    )


def _index_allow_duplicate(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...]
) -> tuple[list[FrozenKey], dict[FrozenKey, int]]:
    keys: list[FrozenKey] = list(frozen_key_rows(frame, columns))
    return keys, {key: i for i, key in enumerate(keys)}


def _index(
    frame: pd.DataFrame, columns: tuple[DataColumn, ...]
) -> tuple[list[FrozenKey], dict[FrozenKey, int]]:
    keys = list(frozen_key_rows(frame, columns))
    result = {key: i for i, key in enumerate(keys)}
    if len(result) != len(keys):
        reject(
            "unique complete coordinate tuples",
            "duplicate complete keys",
            "Keep each complete parent or group key once.",
            "session.attribute",
        )
    return keys, result


def attribute_parent_mapping(
    parent_schema: DataSchema,
    projection: StageOutput,
    source_parent_columns: tuple[DataColumn, ...],
) -> tuple[tuple[FrozenKey, FrozenKey], ...]:
    """Retain P1's exact source-parent to original-parent full-key correspondence."""
    calendar = next(
        (
            i
            for i, axis in enumerate(parent_schema.axes)
            if isinstance(axis, (CalendarDataAxis, PairedDataAxis))
        ),
        None,
    )
    order = (
        *tuple(i for i in range(len(parent_schema.axes)) if i != calendar),
        *((calendar,) if calendar is not None else ()),
    )
    columns = {column.slot_id: column for column in projection.columns}
    projected_columns = tuple(
        columns[slot] for i in range(len(order)) for slot in projection.key_groups[order.index(i)]
    )
    original_columns = tuple(
        column for column in projection.columns if column.slot_id not in projection.keys
    )
    if tuple(column.dtype for column in projected_columns) != tuple(
        column.dtype for column in source_parent_columns
    ):
        reject(
            "exact full parent projection key types",
            repr(source_parent_columns),
            "Retain the original parent axis order and complete projection groups.",
            "session.attribute",
        )
    pairs = tuple(
        zip(
            frozen_key_rows(projection.table, projected_columns),
            frozen_key_rows(projection.table, original_columns),
            strict=True,
        )
    )
    mapped: dict[FrozenKey, FrozenKey] = {}
    for source, original in pairs:
        if source in mapped and mapped[source] != original:
            reject(
                "one original full parent for each complete source parent",
                repr((source, mapped[source], original)),
                "Retain the exact checked complete parent projection without conflicting mappings.",
                "session.attribute",
            )
        mapped[source] = original
    return tuple(mapped.items())


def attribute_source_values(
    program: AttributeInputProgram,
    root: AttributeRootInputs,
    projection: StageOutput,
    source: AttributeMatrixSource,
    scope: Literal["parent", "fine"],
) -> pd.DataFrame:
    """Map one exact active source fiber to canonical original-parent keys."""
    schema = program.left_schema if source.use.endpoint == "left" else program.right_schema
    mapping = program.operation.mapping
    positions = mapping.left_parents if source.use.endpoint == "left" else mapping.right_parents
    source_parent = tuple(
        column for position in positions for column in schema.axes[position].columns
    )
    parent_map = dict(attribute_parent_mapping(program.parent_schema, projection, source_parent))
    source_fine = tuple(
        column
        for i, axis in enumerate(schema.axes)
        if i not in positions
        for column in axis.columns
    )
    output_parent = tuple(
        column for axis in program.output_schema.axes[: len(positions)] for column in axis.columns
    )
    output_fine = (
        tuple(
            column
            for axis in program.output_schema.axes[len(positions) :]
            for column in axis.columns
        )
        if scope == "fine"
        else ()
    )
    table = root.parent if scope == "parent" else root.fine
    if source.window is not None:
        table = table.loc[table[source.window.calendar_slot] == source.window.window_key]
    pkeys = frozen_key_rows(table, source_parent)
    fkeys = frozen_key_rows(table, source_fine) if scope == "fine" else ((),) * len(table)
    selected = [
        (i, (*parent_map[parent], *fine))
        for i, (parent, fine) in enumerate(zip(pkeys, fkeys, strict=True))
        if parent in parent_map
    ]
    keys = tuple(key for _, key in selected)
    if len(set(keys)) != len(keys):
        reject(
            "one actual source value per complete active fiber",
            repr(source.use),
            "Retain unique complete parent and fine source keys.",
            "session.attribute",
        )
    result = frozen_key_table((*output_parent, *output_fine), keys)
    result[source.source] = _take(table, source.source, [i for i, _ in selected])
    return result


def attribute_input_frame_data(
    program: AttributeInputProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...]]:
    """Map checked actual root components to the existing complete child domain."""
    from dataclasses import replace

    parent, left, right, left_projection, right_projection = inputs
    sources = {"left": left, "right": right}
    from marivo.analysis.core.attribute import (
        AttributeAggregateWitness,
        AttributeFailureContext,
        AttributeInputError,
        AttributeParentFiber,
    )

    selected_columns = tuple(
        column for axis in program.parent_schema.axes for column in axis.columns
    )
    selected_keys = tuple(frozen_key_rows(parent.table, selected_columns))
    for side, projection in (("left", left_projection), ("right", right_projection)):
        original_columns = tuple(
            column for column in projection.columns if column.slot_id not in projection.keys
        )
        available = set(frozen_key_rows(projection.table, original_columns))
        missing = next((key for key in selected_keys if key not in available), None)
        if missing is not None:
            uses = tuple(
                dict.fromkeys(
                    source.use
                    for item in program.matrix
                    if item.side == side
                    for source in item.sources
                )
            )
            raise AttributeInputError(
                reason="undefined_input",
                received="a saved paired parent has no structural endpoint scope",
                context=AttributeFailureContext(
                    uses,
                    AttributeParentFiber(missing, selected_columns),
                    AttributeAggregateWitness("parent", missing, selected_columns),
                ),
            )

    mapping = program.operation.mapping
    empty_schemas = tuple(
        replace(schema, values=(), value_paths=())
        for schema in (program.left_schema, program.right_schema)
    )
    domains: list[StageOutput] = []
    domain_schemas: list[DataSchema] = []
    for source, schema, positions in zip(
        (left, right), empty_schemas, (mapping.left_parents, mapping.right_parents), strict=True
    ):
        axes = tuple(schema.axes[position] for position in positions)
        domain_schema = DataSchema(axes, (), ())
        domain_schemas.append(domain_schema)
        names = list(domain_schema.key_fields)
        actual = (
            pd.concat(
                [root.parent.loc[:, names] for root in source.attribute_inputs], ignore_index=True
            ).drop_duplicates()
            if source.attribute_inputs
            else pd.DataFrame(columns=names)
        )
        if not names:
            actual = pd.DataFrame(index=pd.RangeIndex(1 if len(actual) else 0))
        domains.append(
            StageOutput.from_frame(
                actual,
                domain_schema.columns,
                domain_schema.key_fields,
                frame_attachments=tuple(
                    a
                    for a in source.frame_attachments
                    if a.name in coordinate_attachments(domain_schema)
                ),
            )
        )
    skeleton_schema = replace(program.output_schema, values=(), value_paths=())
    skeleton = AttributeProgram(
        mapping,
        empty_schemas[0],
        empty_schemas[1],
        program.parent_schema,
        domain_schemas[0],
        domain_schemas[1],
        skeleton_schema,
    )
    key_sources = tuple(
        StageOutput.from_frame(
            source.table.loc[:, list(schema.key_fields)],
            schema.columns,
            schema.key_fields,
            frame_attachments=tuple(
                a for a in source.frame_attachments if a.name in coordinate_attachments(schema)
            ),
        )
        for source, schema in zip((left, right), empty_schemas, strict=True)
    )
    rows, attachments = attribute_frame_data(
        skeleton,
        (
            parent,
            domains[0],
            domains[1],
            key_sources[0],
            key_sources[1],
            left_projection,
            right_projection,
        ),
    )
    output_parent = tuple(
        column
        for axis in program.output_schema.axes[: len(program.parent_schema.axes)]
        for column in axis.columns
    )
    output_fine = tuple(
        column
        for axis in program.output_schema.axes[len(program.parent_schema.axes) :]
        for column in axis.columns
    )
    for item in program.matrix:
        projection = left_projection if item.side == "left" else right_projection
        mapped: list[pd.DataFrame] = []
        for entry in item.sources:
            root = next(
                root
                for root in sources[item.side].attribute_inputs
                if root.layout.leaves[0].read.source_id == entry.use.root
            )
            active = attribute_source_values(program, root, projection, entry, item.scope)
            mapped.append(active.rename(columns={entry.source: item.target.slot_id}))
        table = pd.concat(mapped, ignore_index=True)
        key_columns = output_parent if item.scope == "parent" else (*output_parent, *output_fine)
        actual_keys = tuple(frozen_key_rows(table, key_columns))
        if len(set(actual_keys)) != len(actual_keys):
            reject(
                "exactly one expanded source for each active complete fiber",
                repr(item.occurrence),
                "Retain disjoint captured window fibers.",
                "session.attribute",
            )
        by_key = {key: index for index, key in enumerate(actual_keys)}
        wanted = tuple(frozen_key_rows(rows, key_columns))
        indices = [by_key.get(key) for key in wanted]
        if (
            item.scope == "parent"
            and item.role not in ("value", "native_value")
            and any(index is None for index in indices)
        ):
            reject(
                "one checked total for each complete selected parent",
                repr(item.occurrence),
                "Retain the complete parent mapping and checked empty-input policy.",
                "session.attribute",
            )
        values = _take(table, item.target.slot_id, indices)
        if item.scope == "fine" and item.role not in ("value", "native_value"):
            values = values.mask(pd.Series([index is None for index in indices]), 0)
        rows[item.target.slot_id] = values
    return rows.loc[:, [column.slot_id for column in program.output_schema.columns]], attachments
