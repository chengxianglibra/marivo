"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog.list().show()

    ms.domain(name="sales", default=True)
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))
    @ms.metric(name="revenue", entities=[orders], decomposition=ms.sum())
    def revenue(orders):
        return orders.amount.sum()
"""

from __future__ import annotations

from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    DomainRef,
    derived_metric,
    dimension,
    domain,
    entity,
    file,
    metric,
    ratio,
    ref,
    relationship,
    snapshot,
    sum,
    table,
    time_dimension,
    validity,
    weighted_average,
)
from marivo.semantic.catalog import (
    AiContextView,
    DatasourceDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticKindInput,
    SemanticObject,
    SemanticObjectDetails,
    SemanticObjectList,
    SemanticRef,
    SemanticRefInput,
    SnapshotVersioning,
    TimeDimensionDetails,
    ValidityVersioning,
    load,
)
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    AuthoringSourceInput,
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
from marivo.semantic.help import help, help_text
from marivo.semantic.ir import (
    DimensionRef,
    EntityRef,
    MetricRef,
    RelationshipRef,
    TimeDimensionRef,
)
from marivo.semantic.loader import find_project
from marivo.semantic.readiness import (
    ParitySummary,
    PreviewSummary,
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
    RichnessSummary,
)
from marivo.semantic.typing import AiContext

__all__ = [
    "AiContext",
    "AiContextView",
    "AssessmentIssue",
    "AuthoringAssessment",
    "AuthoringQuestion",
    "AuthoringSourceInput",
    "BoundedProfilePolicy",
    "ColumnEvidence",
    "ColumnProfile",
    "DatasetSource",
    "DatasourceDetails",
    "DimensionDetails",
    "DimensionRef",
    "DomainDetails",
    "DomainRef",
    "EntityDetails",
    "EntityRef",
    "EvidenceFact",
    "FileSource",
    "MetadataOnlyPolicy",
    "MetricDetails",
    "MetricRef",
    "ParitySummary",
    "PreviewSummary",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RelationshipDetails",
    "RelationshipRef",
    "RichnessSummary",
    "SamplePolicy",
    "SelectedColumnsPolicy",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticKindInput",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SemanticRefInput",
    "SnapshotVersioning",
    "SourceEvidencePack",
    "TableSource",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "ValidityVersioning",
    "derived_metric",
    "dimension",
    "domain",
    "entity",
    "errors",
    "file",
    "find_project",
    "help",
    "help_text",
    "load",
    "metric",
    "ratio",
    "ref",
    "relationship",
    "snapshot",
    "sum",
    "table",
    "time_dimension",
    "typing",
    "validity",
    "weighted_average",
]
