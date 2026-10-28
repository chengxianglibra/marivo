"""Private fixed-state replacement game programs and their report schema."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from marivo.analysis.binding.attribute_game import AttributeGame
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, DataType, ScalarDataType
from marivo.analysis.core.frame_structure import PendingDataColumn, PendingDataSchema

if TYPE_CHECKING:
    from marivo.analysis.core.frame_expression import AttributeFrameValues, FrameAttributeTarget


@dataclass(frozen=True, slots=True)
class AttributeGameValues:
    mapping: AttributeFrameValues
    games: tuple[AttributeGame, AttributeGame]
    target: FrameAttributeTarget
    permutations: int
    seed: int


@dataclass(frozen=True, slots=True)
class AttributeGameProgram:
    operation: AttributeGameValues
    parent_schema: DataSchema
    left_schema: DataSchema
    right_schema: DataSchema
    output_schema: DataSchema
    original_value_slots: tuple[
        tuple[tuple[tuple[int, ...], str], ...], tuple[tuple[tuple[int, ...], str], ...]
    ]
    state_value_slots: tuple[tuple[tuple[int, str], ...], tuple[tuple[int, str], ...]]


def game_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    parent: DataSchema | PendingDataSchema,
    operation: AttributeGameValues,
) -> DataSchema | PendingDataSchema:
    from marivo.analysis.core.attribute import _common_report_type, _type, attribute_schema
    from marivo.analysis.core.frame_expression import _pending_column

    if isinstance(left, DataSchema):
        left_one: DataSchema | PendingDataSchema = replace(
            left,
            values=(replace(left.values[operation.games[0].target_slot], name="target"),),
            value_paths=(("target",),),
        )
    else:
        left_one = replace(
            left,
            values=(replace(left.values[operation.games[0].target_slot], name="target"),),
            value_paths=(("target",),),
        )
    if isinstance(right, DataSchema):
        right_one: DataSchema | PendingDataSchema = replace(
            right,
            values=(replace(right.values[operation.games[1].target_slot], name="target"),),
            value_paths=(("target",),),
        )
    else:
        right_one = replace(
            right,
            values=(replace(right.values[operation.games[1].target_slot], name="target"),),
            value_paths=(("target",),),
        )
    base = attribute_schema(
        left_one,
        right_one,
        parent,
        operation.mapping,
    )
    path = operation.target.path
    prefixes = (
        ("contribution",),
        ("total",),
        ("observed_total",),
        ("contribution_share",),
        ("contribution_std_error",),
        ("detail", "current"),
        ("detail", "baseline"),
        ("detail", "absolute"),
    )
    values: list[DataColumn | PendingDataColumn] = []
    original = parent.values[operation.target.observed]
    common: DataType | None = None
    if isinstance(base, DataSchema) and isinstance(original, DataColumn):
        fresh = base.values[2]
        if operation.target.mode == "relative":
            fresh = replace(fresh, dtype=_type("div", fresh, fresh))
        common = _common_report_type((fresh, original))
    for index, prefix in enumerate(prefixes):
        source = (
            original if index == 2 else base.values[0 if index == 5 else 1 if index == 6 else 2]
        )
        value = replace(
            source,
            slot_id=f"attribute.game.{index}",
            name=".".join((*prefix, *path)),
            nullable=True,
        )
        if isinstance(value, DataColumn) and index in (0, 3, 4):
            value = replace(value, dtype=ScalarDataType("float64"))
        if index == 3:
            value = replace(value, unit=None if original.unit is None else "1")
        if operation.target.mode == "relative" and index in (0, 1, 4):
            value = replace(value, unit=original.unit)
            if index == 1 and isinstance(value, DataColumn):
                value = replace(value, dtype=_type("div", value, value))
        if common is not None and index in (1, 2) and isinstance(value, DataColumn):
            value = replace(value, dtype=common)
        if isinstance(base, PendingDataSchema) and isinstance(value, DataColumn):
            value = _pending_column(value)
        values.append(value)
    paths = tuple((*prefix, *path) for prefix in prefixes)
    presence_paths = tuple(("detail", side, "present", *path) for side in ("current", "baseline"))
    if isinstance(base, DataSchema):
        return replace(
            base,
            values=tuple(v for v in values if isinstance(v, DataColumn)),
            value_paths=paths,
            presence=tuple(
                replace(column, name=".".join(p))
                for column, p in zip(base.presence, presence_paths, strict=True)
            ),
            presence_paths=presence_paths,
        )
    return replace(
        base,
        values=tuple(v for v in values if isinstance(v, PendingDataColumn)),
        value_paths=paths,
        presence=tuple(
            replace(column, name=".".join(p))
            for column, p in zip(base.presence, presence_paths, strict=True)
        ),
        presence_paths=presence_paths,
    )
