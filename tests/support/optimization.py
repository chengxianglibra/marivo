"""Execute authored Frames with an explicit internal testing policy."""

from marivo.analysis.core.frame_data import ExpressionFrameBacking
from marivo.analysis.dsl._frame_lifecycle import complete_frame
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.model.optimization_policy import OptimizationPolicy


def compute_with_policy(query: AnalysisFrame, policy: OptimizationPolicy) -> AnalysisFrame:
    backing = query._backing
    assert isinstance(backing, ExpressionFrameBacking)
    result, declaration = complete_frame(query, backing, AnalysisFrame, policy=policy)
    return AnalysisFrame._from_memory(query._local, result, declaration)
