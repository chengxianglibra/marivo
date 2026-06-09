"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog.list().show()

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
from marivo.semantic.catalog import (
    AiContextView,
    DatasetDetails,
    DatasourceDetails,
    FieldDetails,
    MetricDetails,
    ModelDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticObject,
    SemanticObjectDetails,
    SemanticObjectList,
    SemanticRef,
    SemanticRefInput,
    SnapshotVersioning,
    TimeFieldDetails,
    ValidityVersioning,
    load,
)
from marivo.semantic.classifier import (
    DecisionKind,
)
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    AuthoringSourceInput,
    AuthoringSourceRole,
    BoundedProfilePolicy,
    ColumnEvidence,
    ColumnProfile,
    DatasetSource,
    EvidenceFact,
    FileSource,
    MetadataOnlyPolicy,
    SamplePolicy,
    SelectedColumnsPolicy,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.help import help
from marivo.semantic.ir import FieldKind
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
    ParitySummary,
    PreviewSummary,
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
    RichnessSummary,
)
from marivo.semantic.richness import (
    DemandSignal,
    RichnessGap,
    RichnessReport,
)
from marivo.semantic.typing import AiContext

__all__ = [
    "AiContext",
    "AiContextView",
    "AssessmentIssue",
    "AuthoringAssessment",
    "AuthoringQuestion",
    "AuthoringSourceInput",
    "AuthoringSourceRole",
    "BoundedProfilePolicy",
    "ColumnEvidence",
    "ColumnProfile",
    "DatasetDetails",
    "DatasetSource",
    "DatasourceDetails",
    "DecisionKind",
    "DecisionRecord",
    "DemandSignal",
    "EvidenceFact",
    "FieldDetails",
    "FieldKind",
    "FieldSummary",
    "FileSource",
    "MetadataOnlyPolicy",
    "MetricDetails",
    "ModelDetails",
    "ModelRef",
    "ParitySummary",
    "PreviewSummary",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RejectedCandidate",
    "RelationshipDetails",
    "RelationshipSummary",
    "RichnessGap",
    "RichnessReport",
    "RichnessSummary",
    "SamplePolicy",
    "SelectedColumnsPolicy",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticProject",
    "SemanticRef",
    "SemanticRefInput",
    "SnapshotVersioning",
    "SourceEvidencePack",
    "TableSource",
    "TimeFieldDetails",
    "ValidityVersioning",
    "dataset",
    "derived_metric",
    "errors",
    "field",
    "file",
    "find_project",
    "help",
    "load",
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
