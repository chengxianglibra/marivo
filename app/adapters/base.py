from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CatalogCapabilities:
    supports_schemas: bool = True
    supports_column_stats: bool = False
    supports_partitions: bool = True
    supports_lineage: bool = False
    supports_tags: bool = False
    supports_access_control: bool = False


@dataclass
class PhysicalObject:
    native_name: str
    native_id: str | None
    object_type: str  # 'catalog', 'schema', 'table', 'column', 'partition'
    parent_path: str  # dot-separated parent path
    properties: dict[str, Any] = field(default_factory=dict)


class CatalogAdapter(ABC):
    @abstractmethod
    def source_type(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> CatalogCapabilities: ...

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]: ...

    @abstractmethod
    def list_tables(self, schema_name: str) -> list[PhysicalObject]: ...

    @abstractmethod
    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject: ...

    @abstractmethod
    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]: ...

    def get_table_stats(self, schema_name: str, table_name: str) -> dict[str, Any]:
        raise NotImplementedError("This adapter does not support table stats.")

    def list_partitions(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        raise NotImplementedError("This adapter does not support partitions.")
