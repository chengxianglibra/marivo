"""Assemble immutable attribution reports under their owning Frame."""

from __future__ import annotations

from marivo._kernel.grouping import Grouping
from marivo.analysis.core.frame_data import OperationFrameBinding
from marivo.analysis.dsl.frame_attribute_binding import (
    AttributeFrameT,
    bind_attribute_reports,
    derived_attribute_frame,
)
from marivo.analysis.dsl.population import Population
from marivo.analysis.model.frame_attribute_merge import (
    AttributeMergeValues,
    infer_attribute_merge_schema,
)
from marivo.analysis.model.frame_contracts import frame_logical_schema
from marivo.semantic.spi import FieldExpression


def attribute(
    parent: AttributeFrameT,
    *,
    by: tuple[FieldExpression | Grouping, ...] | Population,
    permutations: int,
    seed: int,
) -> AttributeFrameT:
    return merge_attribute_reports(
        bind_attribute_reports(parent, by=by, permutations=permutations, seed=seed)
    )


def merge_attribute_reports(frames: tuple[AttributeFrameT, ...]) -> AttributeFrameT:
    if len(frames) == 1:
        return frames[0]
    schema = infer_attribute_merge_schema(tuple(frame_logical_schema(frame) for frame in frames))
    return derived_attribute_frame(
        frames[0], schema, OperationFrameBinding(AttributeMergeValues(), frames)
    )
