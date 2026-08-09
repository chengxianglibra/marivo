"""Closed member contract for the typed semantic catalog."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.refs import SemanticKind


@dataclass(frozen=True)
class CatalogMemberContract:
    """One exact typed collection exposed by ``SemanticCatalog``."""

    kind: SemanticKind
    property_name: str
    entry_type_name: str


CATALOG_MEMBER_CONTRACTS: tuple[CatalogMemberContract, ...] = (
    CatalogMemberContract(SemanticKind.DOMAIN, "domains", "DomainEntry"),
    CatalogMemberContract(SemanticKind.DATASOURCE, "datasources", "DatasourceEntry"),
    CatalogMemberContract(SemanticKind.ENTITY, "entities", "EntityEntry"),
    CatalogMemberContract(SemanticKind.DIMENSION, "dimensions", "DimensionEntry"),
    CatalogMemberContract(
        SemanticKind.TIME_DIMENSION,
        "time_dimensions",
        "TimeDimensionEntry",
    ),
    CatalogMemberContract(SemanticKind.MEASURE, "measures", "MeasureEntry"),
    CatalogMemberContract(SemanticKind.METRIC, "metrics", "MetricEntry"),
    CatalogMemberContract(
        SemanticKind.RELATIONSHIP,
        "relationships",
        "RelationshipEntry",
    ),
    CatalogMemberContract(SemanticKind.EVENT, "events", "EventEntry"),
    CatalogMemberContract(
        SemanticKind.STATE_MODEL,
        "state_models",
        "StateModelEntry",
    ),
    CatalogMemberContract(
        SemanticKind.PERIOD_CALENDAR,
        "period_calendars",
        "PeriodCalendarEntry",
    ),
    CatalogMemberContract(
        SemanticKind.TEMPORAL_SET,
        "temporal_sets",
        "TemporalSetEntry",
    ),
)

CATALOG_COLLECTION_PROPERTIES: tuple[str, ...] = tuple(
    member.property_name for member in CATALOG_MEMBER_CONTRACTS
)
