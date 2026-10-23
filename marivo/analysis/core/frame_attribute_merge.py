"""Closed fixed-data union of independently checked Attribute target reports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import overload

from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    axis_role,
    column_kind,
)
from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True)
class AttributeMergeValues:
    """Union complete report keys without changing any target's diagnostics."""


@dataclass(frozen=True, slots=True)
class AttributeMergeProgram:
    operation: AttributeMergeValues
    input_schemas: tuple[DataSchema, ...]
    output_schema: DataSchema


@overload
def infer_attribute_merge_schema(schemas: tuple[DataSchema, ...]) -> DataSchema: ...
@overload
def infer_attribute_merge_schema(
    schemas: tuple[DataSchema | PendingDataSchema, ...],
) -> DataSchema | PendingDataSchema: ...


def infer_attribute_merge_schema(
    schemas: tuple[DataSchema | PendingDataSchema, ...],
) -> DataSchema | PendingDataSchema:
    if not schemas:
        raise AssertionError("Attribute reports require at least one target")
    first = schemas[0]
    for schema in schemas[1:]:
        if len(schema.axes) != len(first.axes) or any(
            (a.token, a.name, axis_role(a), len(a.columns))
            != (b.token, b.name, axis_role(b), len(b.columns))
            for a, b in zip(first.axes, schema.axes, strict=True)
        ):
            reject(
                "the same ordered complete parent and fine coordinates",
                repr(schema.axes),
                "Build every target from the same selected parent and fine classifications.",
                "frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
    if all(isinstance(schema, DataSchema) for schema in schemas):
        for schema in schemas[1:]:
            for a, b in zip(first.axes, schema.axes, strict=True):
                assert isinstance(a, DataAxis) and isinstance(b, DataAxis)
                if replace(a, columns=b.columns) != b or any(
                    (x.dtype, x.unit) != (y.dtype, y.unit)
                    for x, y in zip(a.columns, b.columns, strict=True)
                ):
                    reject(
                        "identical frozen coordinate identity and exact key types",
                        repr((a, b)),
                        "Use the same captured parent coordinates and classifications for every target.",
                        "frame.attribute",
                        canonical_id="analysis_frame.attribute",
                    )
    paths = tuple(path for schema in schemas for path in schema.value_paths)
    presence_paths = tuple(path for schema in schemas for path in schema.presence_paths)
    if len({*paths, *presence_paths}) != len(paths) + len(presence_paths):
        reject(
            "distinct target-namespaced report paths",
            repr(paths),
            "Select distinct target names before attribute.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    values = tuple(
        replace(column, slot_id=f"attribute.merge.value.{i}", nullable=True)
        for i, column in enumerate(column for schema in schemas for column in schema.values)
    )
    presence = tuple(
        replace(column, slot_id=f"attribute.merge.presence.{i}")
        for i, column in enumerate(column for schema in schemas for column in schema.presence)
    )
    if all(isinstance(schema, DataSchema) for schema in schemas):
        assert isinstance(first, DataSchema)
        merged_axes = tuple(
            replace(
                axis,
                columns=tuple(
                    replace(
                        column,
                        nullable=any(schema.axes[i].columns[j].nullable for schema in schemas),
                    )
                    for j, column in enumerate(axis.columns)
                ),
            )
            for i, axis in enumerate(first.axes)
        )
        return DataSchema(
            merged_axes,
            tuple(c for c in values if isinstance(c, DataColumn)),
            tuple(c for c in presence if isinstance(c, DataColumn)),
            value_paths=paths,
            presence_paths=presence_paths,
        )
    axes: list[PendingFrameAxis] = []
    for axis in first.axes:
        if isinstance(axis, (PendingDataAxis, CalendarDataAxis, IntervalDataAxis, PairedDataAxis)):
            axes.append(axis)
        else:
            role = axis_role(axis)
            assert role in ("field", "identity")
            axes.append(
                PendingDataAxis(
                    axis.token,
                    axis.name,
                    role,
                    tuple(
                        PendingDataColumn(c.slot_id, c.name, column_kind(c), c.nullable, c.unit)
                        for c in axis.columns
                    ),
                    axis.at,
                )
            )
    return PendingDataSchema(
        tuple(axes),
        tuple(
            c
            if isinstance(c, PendingDataColumn)
            else PendingDataColumn(c.slot_id, c.name, column_kind(c), True, c.unit)
            for c in values
        ),
        paths,
        tuple(
            c
            if isinstance(c, PendingDataColumn)
            else PendingDataColumn(c.slot_id, c.name, "boolean", False)
            for c in presence
        ),
        presence_paths,
    )
