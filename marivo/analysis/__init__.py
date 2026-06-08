"""Marivo Python-native analysis runtime (analysis)."""

from typing import Any

from marivo.analysis import errors as errors
from marivo.analysis import session
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.datasources.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)
from marivo.analysis.errors import DiscoverInsufficientDataError, PromotionFailedError
from marivo.analysis.evidence import (
    Assessment,
    AssociationSummary,
    AttributedDriver,
    BlockedFollowup,
    ChangeFact,
    EvidenceTrace,
    Finding,
    ForecastSummary,
    OpenAnomaly,
    OpenQuestion,
    Proposition,
    QualitySummary,
    SessionKnowledge,
    Subject,
    TestedHypothesis,
    TimeWindow,
    TriggeredByFollowup,
)
from marivo.analysis.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
)
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
    CandidateSetMeta,
    CandidateShape,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import (
    CheckResult,
    QualityReport,
    QualityReportMeta,
    QualityReportSummary,
)
from marivo.analysis.help import help, help_text
from marivo.analysis.intents._types import (
    DiscoverSensitivity,
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    LagPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
)
from marivo.analysis.publish import (
    DataPolicy,
    Dataset,
    DatasetMetadata,
    Flow,
    FlowStep,
    GroundedClaim,
    Grounding,
    LocalFilesystemTarget,
    MarivoReportArtifact,
    McpAdapterMetadata,
    PublishConfig,
    PublishReportResult,
    PublishTarget,
    ReportBlock,
    ReportChartSpec,
    ReportColumn,
    ReportManifest,
    ReportMetric,
    ReportPackageValidationIssue,
    ReportPackageValidationResult,
    ReportSection,
    ReportSpec,
    SourceProvenance,
    export_report_json_schema,
    load_report_artifact,
    materialize_html_adapter,
    materialize_mcp_adapter,
    publish_report_package,
    render_report_html,
    to_html_report_payload,
    to_mcp_artifact_payload,
    validate_report_artifact,
    write_report_artifact,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef
from marivo.analysis.session._introspection import install_intent_docstrings
from marivo.analysis.session._load import load_frame
from marivo.analysis.validation import ValidationIssue
from marivo.analysis.windows import GrainUnit, ensure_grain_supported
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    Grain,
    GrainInput,
    TimeGrain,
    TimeScope,
    TimeScopeInput,
)
from marivo.preview import PreviewResult, PreviewSamplePolicy, PreviewWarning


def __getattr__(name: str) -> Any:
    if name == "datasources":
        from importlib import import_module

        return import_module("marivo.analysis.datasources")
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactRef",
    "Assessment",
    "AssociationResult",
    "AssociationResultMeta",
    "AssociationSummary",
    "AttributedDriver",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockedFollowup",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "CandidateSetMeta",
    "CandidateShape",
    "ChangeFact",
    "CheckResult",
    "ColumnMetadata",
    "ComponentFrame",
    "ComponentFrameMeta",
    "ConfidenceScope",
    "DataPolicy",
    "Dataset",
    "DatasetMetadata",
    "DeltaFrame",
    "DeltaFrameMeta",
    "DimensionRef",
    "DiscoverInsufficientDataError",
    "DiscoverSensitivity",
    "EvidenceTrace",
    "ExplorationResult",
    "ExplorationResultMeta",
    "Finding",
    "Flow",
    "FlowStep",
    "FollowupAction",
    "ForecastFrame",
    "ForecastFrameMeta",
    "ForecastSummary",
    "FramePreview",
    "Grain",
    "GrainInput",
    "GrainUnit",
    "GroundedClaim",
    "Grounding",
    "HypothesisTestResult",
    "HypothesisTestResultMeta",
    "LagPolicy",
    "LocalFilesystemTarget",
    "MarivoReportArtifact",
    "McpAdapterMetadata",
    "MetadataWarning",
    "MetricFrame",
    "MetricFrameMeta",
    "MetricRef",
    "OpenAnomaly",
    "OpenQuestion",
    "PartitionMetadata",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "PromotionFailedError",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "Proposition",
    "PublishConfig",
    "PublishReportResult",
    "PublishTarget",
    "QualityReport",
    "QualityReportMeta",
    "QualityReportSummary",
    "QualitySummary",
    "ReportBlock",
    "ReportChartSpec",
    "ReportColumn",
    "ReportManifest",
    "ReportMetric",
    "ReportPackageValidationIssue",
    "ReportPackageValidationResult",
    "ReportSection",
    "ReportSpec",
    "SamplingPolicy",
    "SessionKnowledge",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "SourceProvenance",
    "Subject",
    "TableMetadata",
    "TestedHypothesis",
    "TimeGrain",
    "TimeScope",
    "TimeScopeInput",
    "TimeWindow",
    "TriggeredByFollowup",
    "ValidationIssue",
    "datasources",
    "ensure_grain_supported",
    "errors",
    "export_report_json_schema",
    "help",
    "help_text",
    "load_frame",
    "load_report_artifact",
    "materialize_html_adapter",
    "materialize_mcp_adapter",
    "publish_report_package",
    "render_report_html",
    "session",
    "to_html_report_payload",
    "to_mcp_artifact_payload",
    "validate_report_artifact",
    "write_report_artifact",
]


# Mirror intent docstrings onto Session.observe/compare/... so help() and IPython
# `?` surface them. Real type annotations live in core.py source; only the
# docstring text is copied here (authored once on the intent functions).
install_intent_docstrings()
