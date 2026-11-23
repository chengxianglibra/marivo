"""Schemas of immutable coordinate directories and their mapping attachments."""

from __future__ import annotations

from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
    calendar_failure,
    require_frozen_calendar,
)
from marivo.analysis.model.data_types import DataColumn, ScalarDataType, TimestampDataType
from marivo.analysis.model.frame_structure import coordinate_attachments


def direct_mapping_schema(axis: CalendarDataAxis) -> DataSchema:
    """Build one origin-key row with a target group or a closed no-match reason."""
    require_frozen_calendar(axis)
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


def calendar_directory_schema(axis: CalendarDataAxis) -> DataSchema:
    """Build the calendar.v3 directory layout used by writers and readers."""
    require_frozen_calendar(axis)
    values = (
        DataColumn("nominal_label", "nominal_label", ScalarDataType("string"), False),
        DataColumn("ordinal", "ordinal", ScalarDataType("int64"), False),
        DataColumn("nominal_ordinal", "nominal_ordinal", ScalarDataType("int64"), False),
        DataColumn("selection", "selection", ScalarDataType("string"), False),
        DataColumn("cutoff", "cutoff", ScalarDataType("string"), True),
    )
    return DataSchema(
        (DataAxis(axis.token, axis.columns, name=axis.name, at=axis.at),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )


def comparison_mapping_schema(
    current: CalendarDataAxis | IntervalDataAxis,
    binding: CalendarComparisonBinding | IntervalComparisonBinding,
) -> DataSchema:
    """Build one current-key row with a nullable complete baseline group or reason."""
    if current.token != binding.current_axis_token or type(current) is not type(binding.baseline):
        calendar_failure(
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


def interval_directory_schema(axis: IntervalDataAxis) -> DataSchema:
    """Build the exact interval.v1 finite directory layout."""
    if (
        len(axis.columns) != 1
        or axis.columns[0].dtype != ScalarDataType("int64")
        or axis.columns[0].nullable
    ):
        calendar_failure("one nonnullable int64 interval key", repr(axis.columns))
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


def paired_mapping_schema(axis: PairedDataAxis) -> DataSchema:
    """Build the complete pair key and nullable all-or-none endpoint key groups."""
    for endpoint in (axis.left, axis.right):
        if isinstance(endpoint, CalendarDataAxis):
            require_frozen_calendar(endpoint)
        else:
            interval_directory_schema(endpoint)
    if any(column.nullable for column in axis.columns):
        calendar_failure("nonnullable complete paired keys", repr(axis.columns))
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
            require_frozen_calendar(axis)
            assert axis.frozen is not None
            if axis.frozen.direct is not None:
                visit(axis.frozen.direct.axis)
            result.append((axis.frozen.directory_name, calendar_directory_schema(axis)))
            if axis.frozen.direct is not None:
                result.append((axis.frozen.direct.mapping_name, direct_mapping_schema(axis)))
        elif axis.kind == "correspondence":
            calendar_failure("a closed paired coordinate descriptor", repr(axis))

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
