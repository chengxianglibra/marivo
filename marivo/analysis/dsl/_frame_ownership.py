"""Validate exact Frame ownership at expression and operand boundaries."""

from __future__ import annotations

from marivo._definition.types import Owner
from marivo.analysis.core.frame_data import (
    FrameExpressionRoot,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.dsl.column import Column, column_frame, column_input_id
from marivo.analysis.dsl.frame_axis import FrameAxis, axis_frame, axis_input_id
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import frame_local
from marivo.analysis.model.frame_schema import LogicalSchema
from marivo.analysis.model.frame_structure import PendingDataSchema, require_complete_structure


def validate_expression_owner(
    owner: Owner, schema: LogicalSchema, root: FrameExpressionRoot
) -> None:
    if (
        isinstance(root, (SourceFrameBinding, LiteralFrameBinding))
        and root.graph.signature.owner != owner
    ):
        reject(
            "a source graph from this Store and Session",
            repr(root.graph.signature.owner),
            "Use the owning Session.",
            "frame.expression",
        )
    if isinstance(schema, DataSchema):
        require_complete_structure(schema)
    elif type(schema) is not PendingDataSchema:
        reject(
            "a current complete or pending schema",
            type(schema).__name__,
            "Capture the current structure.",
            "frame.expression",
        )


def axis_index(
    axis: object,
    axes: tuple[FrameAxis, ...],
    input_id: str,
    owner: Owner,
    *,
    location: str,
    canonical_id: str | None,
) -> int:
    if not isinstance(axis, FrameAxis) or (
        axis_input_id(axis) != input_id or frame_local(axis_frame(axis)).owner != owner
    ):
        reject(
            "an axis from this exact Frame input",
            repr(axis),
            "Acquire the axis from this Frame's axes.",
            location,
            canonical_id=canonical_id,
        )
    for index, current in enumerate(axes):
        if (current.token, current.name, current.role, current.key_names) == (
            axis.token,
            axis.name,
            axis.role,
            axis.key_names,
        ):
            return index
    reject(
        "a complete current axis handle",
        repr(axis),
        "Acquire the axis from this Frame's axes.",
        location,
        canonical_id=canonical_id,
    )


def require_column(
    column: object,
    input_id: str,
    owner: Owner,
    *,
    location: str,
    canonical_id: str | None,
) -> None:
    bound_frame = column_frame(column) if isinstance(column, Column) else None
    if not isinstance(column, Column) or (
        column_input_id(column) != input_id
        or bound_frame is None
        or frame_local(bound_frame).owner != owner
    ):
        reject(
            "a Column from this exact Store/Session/Frame input",
            repr(column),
            "Locate the Column on this Frame, or align the two Frames first.",
            location,
            canonical_id=canonical_id,
        )
