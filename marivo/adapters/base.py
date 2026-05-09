from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

MAX_PREVIEW_ROWS = 1000
DEFAULT_PREVIEW_ROWS = 100
PreviewFilters = dict[str, str | int | float | bool | None]


@dataclass
class CatalogCapabilities:
    supports_schemas: bool = True
    supports_column_stats: bool = False
    supports_partitions: bool = True
    supports_lineage: bool = False
    supports_tags: bool = False
    supports_access_control: bool = False
    supports_column_comments: bool = False
    supports_table_properties: bool = False
    supports_table_preview: bool = True


@dataclass
class PhysicalObject:
    native_name: str
    native_id: str | None
    object_type: str  # 'catalog', 'schema', 'table', 'column', 'partition'
    parent_path: str | None  # dot-separated parent path
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreviewResult:
    """Data preview result from a table query."""

    columns: list[dict[str, Any]]  # [{"name": str, "type": str}]
    rows: list[dict[str, Any]]  # Each row as dict keyed by column name
    row_count: int
    truncated: bool  # True if result hit the limit cap


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

    def preview_table(
        self,
        schema_name: str,
        table_name: str,
        limit: int = DEFAULT_PREVIEW_ROWS,
        columns: list[str] | None = None,
        filters: PreviewFilters | None = None,
    ) -> PreviewResult:
        """Preview sample rows from a table.

        Args:
            schema_name: Schema containing the table
            table_name: Table to preview
            limit: Maximum rows to return (capped at MAX_PREVIEW_ROWS)
            columns: Optional list of column names to select; None = all columns
            filters: Optional equality filters keyed by column name

        Returns:
            PreviewResult with columns metadata and sample rows

        Raises:
            KeyError: Table not found
            ValueError: Invalid column names
        """
        raise NotImplementedError("This adapter does not support table preview.")
