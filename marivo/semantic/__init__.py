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

from typing import TYPE_CHECKING

from marivo.datasource.scan import ScanScope
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
    BriefStatus,
    ColumnProfile,
    ComponentFact,
    CrossEntityMetricBrief,
    DatasetSource,
    DerivedMetricBrief,
    DimensionBrief,
    DimensionValueFact,
    DomainBrief,
    EntityBrief,
    FileSource,
    FormatCandidate,
    JoinPathFact,
    MetricBrief,
    PrimaryKeyCandidate,
    RegisteredMatch,
    RelationshipBrief,
    TableSource,
    TimeDimensionBrief,
    VerifyResult,
    VersioningHints,
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
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.typing import AiContext

if TYPE_CHECKING:
    from marivo.datasource.ir import EntitySourceIR


def prepare_domain(*, name: str) -> DomainBrief:
    """Prepare a domain authoring brief from the current project."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.prepare_domain(name=name)


def prepare_derived_metric(
    *,
    numerator: str,
    denominator: str | None = None,
    weight: str | None = None,
) -> DerivedMetricBrief:
    """Prepare a derived metric brief from component metric refs."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.prepare_derived_metric(
        numerator=numerator, denominator=denominator, weight=weight
    )


def prepare_entity(
    *,
    datasource: str,
    source: EntitySourceIR,
    domain: str,
    scope: ScanScope | None = None,
) -> EntityBrief:
    """Prepare an entity authoring brief with datasource evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_entity(datasource=datasource, source=source, domain=domain, scope=scope)


def prepare_dimensions(
    *,
    entity: str,
    columns: tuple[str, ...] | list[str],
    scope: ScanScope | None = None,
) -> tuple[DimensionBrief, ...]:
    """Prepare dimension authoring briefs for the given entity columns."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_dimensions(entity=entity, columns=columns, scope=scope)


def prepare_time_dimension(
    *,
    entity: str,
    column: str,
    scope: ScanScope | None = None,
) -> TimeDimensionBrief:
    """Prepare a time dimension authoring brief with format detection."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_time_dimension(entity=entity, column=column, scope=scope)


def prepare_metric(
    *,
    entity: str,
    measure_columns: tuple[str, ...] | list[str] = (),
    filter_dimensions: tuple[str, ...] | list[str] = (),
    scope: ScanScope | None = None,
) -> MetricBrief:
    """Prepare a metric authoring brief with measure evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_metric(
        entity=entity,
        measure_columns=measure_columns,
        filter_dimensions=filter_dimensions,
        scope=scope,
    )


def prepare_relationship(
    *,
    from_entity: str,
    to_entity: str,
    from_dimensions: tuple[str, ...] | list[str],
    to_dimensions: tuple[str, ...] | list[str],
    scope: ScanScope | None = None,
) -> RelationshipBrief:
    """Prepare a relationship authoring brief with join-key probe evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_relationship(
        from_entity=from_entity,
        to_entity=to_entity,
        from_dimensions=from_dimensions,
        to_dimensions=to_dimensions,
        scope=scope,
    )


def prepare_cross_entity_metric(
    *,
    root_entity: str,
    entities: tuple[str, ...] | list[str],
    measure_columns: tuple[str, ...] | list[str] = (),
    scope: ScanScope | None = None,
) -> CrossEntityMetricBrief:
    """Prepare a cross-entity metric brief with relationship path evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_cross_entity_metric(
        root_entity=root_entity,
        entities=entities,
        measure_columns=measure_columns,
        scope=scope,
    )


__all__ = [
    "AiContext",
    "AiContextView",
    "AssessmentIssue",
    "AuthoringAssessment",
    "AuthoringQuestion",
    "BriefStatus",
    "ColumnProfile",
    "ComponentFact",
    "CrossEntityMetricBrief",
    "DatasetSource",
    "DatasourceDetails",
    "DerivedMetricBrief",
    "DimensionBrief",
    "DimensionDetails",
    "DimensionRef",
    "DimensionValueFact",
    "DomainBrief",
    "DomainDetails",
    "DomainRef",
    "EntityBrief",
    "EntityDetails",
    "EntityRef",
    "FileSource",
    "FormatCandidate",
    "JoinPathFact",
    "MetricBrief",
    "MetricDetails",
    "MetricRef",
    "PrimaryKeyCandidate",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RegisteredMatch",
    "RelationshipBrief",
    "RelationshipDetails",
    "RelationshipRef",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticKindInput",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SemanticRefInput",
    "SnapshotVersioning",
    "TableSource",
    "TimeDimensionBrief",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "ValidityVersioning",
    "VerifyResult",
    "VersioningHints",
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
    "prepare_cross_entity_metric",
    "prepare_derived_metric",
    "prepare_dimensions",
    "prepare_domain",
    "prepare_entity",
    "prepare_metric",
    "prepare_relationship",
    "prepare_time_dimension",
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
