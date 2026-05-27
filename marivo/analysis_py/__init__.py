"""Marivo Python-native analysis runtime (analysis_py)."""

from typing import Any

from marivo.analysis_py import errors as errors
from marivo.analysis_py import session
from marivo.analysis_py.calendar.model import CalendarPolicy
from marivo.analysis_py.errors import PromotionFailedError
from marivo.analysis_py.escape_hatch import (
    explore_ibis,
    from_pandas,
    promote_attribution_frame,
    promote_delta_frame,
    promote_metric_frame,
)
from marivo.analysis_py.evidence import (
    Assessment,
    ChangeFact,
    EvidenceTrace,
    Finding,
    OpenAnomaly,
    OpenQuestion,
    Proposition,
    QualitySummary,
    SessionKnowledge,
    Subject,
    TimeWindow,
    TriggeredByFollowup,
)
from marivo.analysis_py.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
)
from marivo.analysis_py.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis_py.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis_py.frames.candidate import (
    CandidateSet,
    CandidateSetMeta,
    CandidateShape,
)
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis_py.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis_py.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis_py.help import help
from marivo.analysis_py.intents.assess_quality import assess_quality
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.correlate import correlate
from marivo.analysis_py.intents.decompose import decompose
from marivo.analysis_py.intents.discover import discover
from marivo.analysis_py.intents.forecast import forecast
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.intents.select import select
from marivo.analysis_py.intents.test import hypothesis_test as test
from marivo.analysis_py.intents.transform import transform
from marivo.analysis_py.policies import (
    AlignmentKind,
    AlignmentPolicy,
    LagPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
)
from marivo.analysis_py.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef
from marivo.analysis_py.session._load import load_frame
from marivo.analysis_py.windows.spec import AbsoluteWindow, RelativeWindow, WindowInput


def __getattr__(name: str) -> Any:
    if name == "datasources":
        from importlib import import_module

        return import_module("marivo.analysis_py.datasources")
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactRef",
    "Assessment",
    "AssociationResult",
    "AssociationResultMeta",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateSet",
    "CandidateSetMeta",
    "CandidateShape",
    "ChangeFact",
    "ConfidenceScope",
    "DeltaFrame",
    "DeltaFrameMeta",
    "DimensionRef",
    "EvidenceTrace",
    "ExplorationResult",
    "ExplorationResultMeta",
    "Finding",
    "FollowupAction",
    "ForecastFrame",
    "ForecastFrameMeta",
    "FramePreview",
    "HypothesisTestResult",
    "HypothesisTestResultMeta",
    "LagPolicy",
    "MetricFrame",
    "MetricFrameMeta",
    "MetricRef",
    "OpenAnomaly",
    "OpenQuestion",
    "PromotionFailedError",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "Proposition",
    "QualityReport",
    "QualityReportMeta",
    "QualitySummary",
    "RelativeWindow",
    "SamplingPolicy",
    "SessionKnowledge",
    "Subject",
    "TimeWindow",
    "TriggeredByFollowup",
    "WindowInput",
    "assess_quality",
    "compare",
    "correlate",
    "datasources",
    "decompose",
    "discover",
    "errors",
    "explore_ibis",
    "forecast",
    "from_pandas",
    "help",
    "load_frame",
    "observe",
    "promote_attribution_frame",
    "promote_delta_frame",
    "promote_metric_frame",
    "select",
    "session",
    "test",
    "transform",
]
