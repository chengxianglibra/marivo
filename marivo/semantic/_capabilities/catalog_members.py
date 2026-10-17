"""Closed member contract for the typed semantic catalog."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.refs import SemanticKind


@dataclass(frozen=True)
class CatalogMemberContract:
    """One exact collection exposed by ``SemanticCatalog`` and the kinds it holds."""

    property_name: str
    kinds: tuple[SemanticKind, ...]
    item_type_name: str


CATALOG_MEMBER_CONTRACTS: tuple[CatalogMemberContract, ...] = (
    CatalogMemberContract("domains", (SemanticKind.DOMAIN,), "DomainEntry"),
    CatalogMemberContract("datasources", (SemanticKind.DATASOURCE,), "DatasourceEntry"),
    CatalogMemberContract("entities", (SemanticKind.ENTITY,), "Entity"),
    CatalogMemberContract(
        "fields",
        (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE),
        "Deferred",
    ),
    CatalogMemberContract("metrics", (SemanticKind.METRIC,), "Deferred"),
    CatalogMemberContract("relationships", (SemanticKind.RELATIONSHIP,), "Relationship"),
    CatalogMemberContract(
        "period_calendars", (SemanticKind.PERIOD_CALENDAR,), "PeriodCalendarEntry"
    ),
    CatalogMemberContract("temporal_sets", (SemanticKind.TEMPORAL_SET,), "TemporalSetEntry"),
    CatalogMemberContract("work_schedules", (SemanticKind.WORK_SCHEDULE,), "WorkScheduleEntry"),
)

CATALOG_COLLECTION_PROPERTIES: tuple[str, ...] = tuple(
    member.property_name for member in CATALOG_MEMBER_CONTRACTS
)

CATALOG_COLLECTION_BY_KIND: dict[SemanticKind, str] = {
    kind: member.property_name for member in CATALOG_MEMBER_CONTRACTS for kind in member.kinds
}
