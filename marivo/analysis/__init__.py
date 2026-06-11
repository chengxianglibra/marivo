"""Marivo Python-native analysis runtime (analysis)."""

from typing import Any as _Any

from marivo.analysis import errors as errors
from marivo.analysis import session
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FramePreview, FrameSummary
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
)
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.exploration import ExplorationResult
from marivo.analysis.frames.forecast import ForecastFrame
from marivo.analysis.frames.hypothesis import HypothesisTestResult
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.quality import QualityReport
from marivo.analysis.help import help, help_text
from marivo.analysis.intents._types import (
    DiscoverSensitivity,
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef
from marivo.analysis.session._introspection import (
    install_intent_docstrings as _install_intent_docstrings,
)
from marivo.analysis.session.attach import SessionSummary
from marivo.analysis.session.core import FrameSummaryEntry, JobSummary, Session
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    TimeScope,
    TimeScopeInput,
)


def __getattr__(name: str) -> _Any:
    if name == "datasources":
        from importlib import import_module

        return import_module("marivo.analysis.datasources")
    if name == "evidence":
        from importlib import import_module

        return import_module("marivo.analysis.evidence")
    if name == "frames":
        from importlib import import_module

        return import_module("marivo.analysis.frames")
    if name == "publish":
        from importlib import import_module

        return import_module("marivo.analysis.publish")
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactRef",
    "AssociationResult",
    "AttributionFrame",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "ComponentFrame",
    "ConfidenceScope",
    "DeltaFrame",
    "DimensionRef",
    "DiscoverSensitivity",
    "ExplorationResult",
    "FollowupAction",
    "ForecastFrame",
    "FramePreview",
    "FrameSummary",
    "FrameSummaryEntry",
    "HypothesisTestResult",
    "JobSummary",
    "Lineage",
    "LineageStep",
    "MetricFrame",
    "MetricRef",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "QualityReport",
    "SamplingPolicy",
    "Session",
    "SessionSummary",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "TimeScope",
    "TimeScopeInput",
    "datasources",
    "errors",
    "evidence",
    "frames",
    "help",
    "help_text",
    "publish",
    "session",
]


# Mirror intent docstrings onto Session.observe/compare/... so help() and IPython
# `?` surface them. Real type annotations live in core.py source; only the
# docstring text is copied here (authored once on the intent functions).
_install_intent_docstrings()
