"""Capture a checked multi-target report union on the existing Frame schedule."""

from marivo.analysis.core.frame_attribute_merge import (
    AttributeMergeValues,
    infer_attribute_merge_schema,
)
from marivo.analysis.core.frame_data import OperationFrameBinding
from marivo.analysis.dsl.frame import AnalysisFrame


def merge_attribute_reports(frames: tuple[AnalysisFrame, ...]) -> AnalysisFrame:
    if len(frames) == 1:
        return frames[0]
    schema = infer_attribute_merge_schema(tuple(frame._schema for frame in frames))
    return AnalysisFrame._from_expression(
        frames[0]._local, schema, OperationFrameBinding(AttributeMergeValues(), frames)
    )
