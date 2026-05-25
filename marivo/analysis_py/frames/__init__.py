"""Frame wrappers for analysis_py."""

from marivo.analysis_py.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis_py.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis_py.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta

__all__ = [
    "AssociationResult",
    "AssociationResultMeta",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "CandidateSet",
    "CandidateSetMeta",
    "DeltaFrame",
    "DeltaFrameMeta",
    "MetricFrame",
    "MetricFrameMeta",
]
