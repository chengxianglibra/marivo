from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .base import DomainCatalogStatus, LifecycleStatus, ReadinessStatus, validate_ref_prefix


class DomainCatalogCreateRequest(BaseModel):
    """Request to create a catalog discovery domain."""

    domain_ref: str = Field(description="Stable discovery domain ref, e.g. 'domain.growth'.")
    display_name: str = Field(description="Human-readable domain name.")
    description: str = Field(default="", description="Discovery-only domain description.")
    aliases: list[str] = Field(default_factory=list, description="Alternative search names.")

    @field_validator("domain_ref")
    @classmethod
    def validate_domain_ref(cls, value: str) -> str:
        return validate_ref_prefix(value.strip(), "domain", "domain_ref")

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class DomainCatalogUpdateRequest(BaseModel):
    """Request to update catalog discovery metadata for a domain."""

    display_name: str | None = Field(default=None, description="Updated display name.")
    description: str | None = Field(default=None, description="Updated description.")
    aliases: list[str] | None = Field(default=None, description="Updated search aliases.")

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return [value.strip() for value in values if value.strip()]


class DomainCatalogResponse(BaseModel):
    """Catalog discovery domain response.

    Domain status is a discovery lifecycle only. It is not semantic object
    lifecycle status and is not used for authorization decisions.
    """

    domain_ref: str
    display_name: str
    description: str = ""
    status: DomainCatalogStatus
    aliases: list[str] = Field(default_factory=list)


class DomainCatalogListResponse(BaseModel):
    items: list[DomainCatalogResponse] = Field(default_factory=list)
    total: int = Field(ge=0)


class DomainSemanticObjectSearchItem(BaseModel):
    object_type: str
    object_id: str
    ref: str
    display_name: str
    description: str = ""
    status: str
    lifecycle_status: LifecycleStatus
    readiness_status: ReadinessStatus
    blocker_count: int = Field(default=0, ge=0)
    catalog_metadata: dict[str, object]
    detail_path: str


class DomainSemanticObjectSearchResponse(BaseModel):
    items: list[DomainSemanticObjectSearchItem] = Field(default_factory=list)
    total: int = Field(ge=0)
