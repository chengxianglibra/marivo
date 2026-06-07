"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic as ms

    project = ms.find_project()        # or ms.SemanticProject(root)
    project.load()

    ms.model(name="sales", default=True)
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))
    ms.metric(name="revenue", datasets=[orders], decomposition=ms.sum())
"""

from __future__ import annotations

from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    ModelRef,
    dataset,
    derived_metric,
    field,
    file,
    metric,
    model,
    ratio,
    ref,
    relationship,
    snapshot,
    sum,
    table,
    time_field,
    validity,
    weighted_average,
)
from marivo.semantic.classifier import (
    DecisionKind,
)
from marivo.semantic.evidence import (
    AiContextInput,
    AssessmentIssue,
    AssessmentResult,
    AuthoringEvidenceInput,
    AuthoringQuestion,
    BoundedProfilePolicy,
    ColumnEvidence,
    ColumnProfile,
    DatasetSource,
    EvidenceFact,
    EvidenceRef,
    FileSource,
    MetadataOnlyPolicy,
    SamplePolicy,
    SelectedColumnsPolicy,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.help import help
from marivo.semantic.ledger import (
    DecisionRecord,
    RejectedCandidate,
)
from marivo.semantic.loader import find_project
from marivo.semantic.reader import (
    FieldSummary,
    RelationshipSummary,
    SemanticProject,
)
from marivo.semantic.readiness import (
    EvidenceSummary,
    ParitySummary,
    PreviewSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.richness import (
    DemandSignal,
    RichnessGap,
    RichnessReport,
)
from marivo.semantic.typing import AiContext

__all__ = [
    "AiContext",
    "AiContextInput",
    "AssessmentIssue",
    "AssessmentResult",
    "AuthoringEvidenceInput",
    "AuthoringQuestion",
    "BoundedProfilePolicy",
    "ColumnEvidence",
    "ColumnProfile",
    "DatasetSource",
    "DecisionKind",
    "DecisionRecord",
    "DemandSignal",
    "EvidenceFact",
    "EvidenceRef",
    "EvidenceSummary",
    "FieldSummary",
    "FileSource",
    "MetadataOnlyPolicy",
    "ModelRef",
    "ParitySummary",
    "PreviewSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RejectedCandidate",
    "RelationshipSummary",
    "RichnessGap",
    "RichnessReport",
    "SamplePolicy",
    "SelectedColumnsPolicy",
    "SemanticProject",
    "SourceEvidencePack",
    "TableSource",
    "dataset",
    "derived_metric",
    "errors",
    "field",
    "file",
    "find_project",
    "help",
    "metric",
    "model",
    "ratio",
    "ref",
    "relationship",
    "snapshot",
    "sum",
    "table",
    "time_field",
    "typing",
    "validity",
    "weighted_average",
]
