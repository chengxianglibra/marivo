from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .base import BlockingRequirement, LifecycleStatus, ReadinessStatus
from .entity import TypedEntityResponse
from .metric import TypedMetricResponse


class SourceObjectResponse(BaseModel):
    object_id: str
    source_id: str
    object_type: str
    parent_id: str | None = None
    native_name: str
    native_id: str | None = None
    fqn: str
    properties: dict[str, object]
    sync_version: str | None = None
    synced_at: str | None = None


class CatalogSearchResultBase(BaseModel):
    object_kind: str
    object_id: str
    ref: str
    name: str
    display_name: str | None = None
    description: str | None = None
    status: str
    detail_path: str


class CatalogSemanticSearchResult(CatalogSearchResultBase):
    object_kind: Literal["entity", "metric", "process", "dimension", "time", "binding"]
    contract_version: str
    lifecycle_status: LifecycleStatus
    readiness_status: ReadinessStatus
    blocker_count: int = 0
    blocking_requirements_preview: list[BlockingRequirement] = Field(default_factory=list)
    capabilities_summary: dict[str, bool] = Field(default_factory=dict)
    additivity_summary: dict[str, object] = Field(default_factory=dict)
    revision: int
    created_at: str
    updated_at: str
    resolve_path: str


class CatalogCalendarPolicySearchResult(CatalogSearchResultBase):
    object_kind: Literal["calendar_policy"]
    lifecycle_status: LifecycleStatus
    readiness_status: ReadinessStatus
    blocker_count: int = 0
    blocking_requirements_preview: list[BlockingRequirement] = Field(default_factory=list)
    capabilities_summary: dict[str, object] = Field(default_factory=dict)
    revision: int
    created_at: str
    updated_at: str
    resolve_path: str
    comparison_basis: str
    resolved_alignment_mode: str
    system_managed: bool = True
    catalog_source: str


class CatalogAssetSearchResult(CatalogSearchResultBase):
    object_kind: Literal["asset"]
    object_type: str
    source_id: str
    synced_at: str | None = None
    source_object_path: str


CatalogSearchResult = Annotated[
    CatalogSemanticSearchResult | CatalogCalendarPolicySearchResult | CatalogAssetSearchResult,
    Field(discriminator="object_kind"),
]


class CatalogSemanticDetailBase(BaseModel):
    object_kind: str
    object_id: str
    ref: str
    status: str
    revision: int
    created_at: str
    updated_at: str


class CatalogEntityDetail(CatalogSemanticDetailBase):
    object_kind: Literal["entity"]
    semantic_object: TypedEntityResponse


class CatalogMetricDetail(CatalogSemanticDetailBase):
    object_kind: Literal["metric"]
    semantic_object: TypedMetricResponse


class CatalogGenericSemanticDetail(CatalogSemanticDetailBase):
    object_kind: Literal["process", "dimension", "time", "binding"]
    semantic_object: dict[str, object]


class CatalogCalendarPolicyDetail(CatalogSemanticDetailBase):
    object_kind: Literal["calendar_policy"]
    semantic_object: dict[str, object]


class CatalogAssetDetail(BaseModel):
    object_kind: Literal["asset"]
    object_id: str
    ref: str
    source_object: SourceObjectResponse


CatalogObjectDetail = Annotated[
    CatalogEntityDetail
    | CatalogMetricDetail
    | CatalogGenericSemanticDetail
    | CatalogCalendarPolicyDetail
    | CatalogAssetDetail,
    Field(discriminator="object_kind"),
]
