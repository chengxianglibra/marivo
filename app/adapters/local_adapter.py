from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject

# Default local catalog definition — mirrors the hardcoded catalog from the
# original SemanticLayerService.discover_catalog().
DEFAULT_LOCAL_CATALOG: dict[str, Any] = {
    "schemas": [
        {
            "name": "analytics",
            "tables": [
                {
                    "name": "watch_events",
                    "columns": [
                        {"name": "event_date", "type": "DATE"},
                        {"name": "user_id", "type": "TEXT"},
                        {"name": "session_id", "type": "TEXT"},
                        {"name": "platform", "type": "TEXT"},
                        {"name": "app_version", "type": "TEXT"},
                        {"name": "network_type", "type": "TEXT"},
                        {"name": "content_type", "type": "TEXT"},
                        {"name": "play_duration_seconds", "type": "DOUBLE"},
                    ],
                },
                {
                    "name": "player_qoe",
                    "columns": [
                        {"name": "event_date", "type": "DATE"},
                        {"name": "session_id", "type": "TEXT"},
                        {"name": "platform", "type": "TEXT"},
                        {"name": "app_version", "type": "TEXT"},
                        {"name": "network_type", "type": "TEXT"},
                        {"name": "content_type", "type": "TEXT"},
                        {"name": "first_frame_time_ms", "type": "DOUBLE"},
                    ],
                },
                {
                    "name": "ad_events",
                    "columns": [
                        {"name": "event_date", "type": "DATE"},
                        {"name": "session_id", "type": "TEXT"},
                        {"name": "platform", "type": "TEXT"},
                        {"name": "app_version", "type": "TEXT"},
                        {"name": "network_type", "type": "TEXT"},
                        {"name": "content_type", "type": "TEXT"},
                        {"name": "preroll_timeout", "type": "INTEGER"},
                        {"name": "preroll_duration_seconds", "type": "DOUBLE"},
                    ],
                },
                {
                    "name": "recommendation_events",
                    "columns": [
                        {"name": "event_date", "type": "DATE"},
                        {"name": "session_id", "type": "TEXT"},
                        {"name": "platform", "type": "TEXT"},
                        {"name": "app_version", "type": "TEXT"},
                        {"name": "network_type", "type": "TEXT"},
                        {"name": "content_type", "type": "TEXT"},
                        {"name": "impressions", "type": "INTEGER"},
                        {"name": "clicks", "type": "INTEGER"},
                    ],
                },
            ],
        }
    ]
}


class LocalCatalogAdapter(CatalogAdapter):
    """Mock/local adapter that reads from an in-memory catalog definition."""

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        self._catalog = catalog or DEFAULT_LOCAL_CATALOG

    def source_type(self) -> str:
        return "local"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=False,
            supports_partitions=False,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=False,
        )

    def test_connection(self) -> bool:
        return True

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        return [
            PhysicalObject(
                native_name=schema["name"],
                native_id=None,
                object_type="schema",
                parent_path=catalog_name or "local",
            )
            for schema in self._catalog.get("schemas", [])
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        schema = self._find_schema(schema_name)
        if schema is None:
            return []
        return [
            PhysicalObject(
                native_name=table["name"],
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={"column_count": len(table.get("columns", []))},
            )
            for table in schema.get("tables", [])
        ]

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        table = self._find_table(schema_name, table_name)
        if table is None:
            raise KeyError(f"Table {schema_name}.{table_name} not found")
        return PhysicalObject(
            native_name=table["name"],
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties={
                "columns": table.get("columns", []),
                "column_count": len(table.get("columns", [])),
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        table = self._find_table(schema_name, table_name)
        if table is None:
            return []
        return [
            PhysicalObject(
                native_name=col["name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": col.get("type", "UNKNOWN")},
            )
            for col in table.get("columns", [])
        ]

    def _find_schema(self, schema_name: str) -> dict[str, Any] | None:
        return next((s for s in self._catalog.get("schemas", []) if s["name"] == schema_name), None)

    def _find_table(self, schema_name: str, table_name: str) -> dict[str, Any] | None:
        schema = self._find_schema(schema_name)
        if schema is None:
            return None
        return next((t for t in schema.get("tables", []) if t["name"] == table_name), None)
