"""Unity Catalog adapter — reads schema/table/column metadata via REST API."""

from __future__ import annotations

from typing import Any

import requests

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class UnityCatalogAdapter(CatalogAdapter):
    """Catalog adapter for Databricks Unity Catalog (REST API)."""

    def __init__(self, host: str, token: str, catalog_name: str = "main") -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._catalog = catalog_name

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self._host}/api/2.1/unity-catalog{path}"

    def source_type(self) -> str:
        return "unity_catalog"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=False,
            supports_partitions=False,
            supports_lineage=True,
            supports_tags=True,
            supports_access_control=True,
        )

    def test_connection(self) -> bool:
        try:
            resp = requests.get(self._url("/catalogs"), headers=self._headers(), timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        cat = catalog_name or self._catalog
        resp = requests.get(
            self._url(f"/schemas?catalog_name={cat}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for schema in data.get("schemas", []):
            full_name = schema.get("name", "")
            # Unity returns "catalog.schema" as name; extract the schema part
            short_name = full_name.split(".")[-1] if "." in full_name else full_name
            results.append(PhysicalObject(
                native_name=short_name,
                native_id=schema.get("schema_id"),
                object_type="schema",
                parent_path=cat,
                properties={"comment": schema.get("comment", "")},
            ))
        return results

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        resp = requests.get(
            self._url(f"/tables?catalog_name={self._catalog}&schema_name={schema_name}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for table in data.get("tables", []):
            columns = table.get("columns", [])
            results.append(PhysicalObject(
                native_name=table["name"],
                native_id=table.get("table_id"),
                object_type="table",
                parent_path=schema_name,
                properties={
                    "table_type": table.get("table_type", ""),
                    "column_count": len(columns),
                },
            ))
        return results

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        full_name = f"{self._catalog}.{schema_name}.{table_name}"
        resp = requests.get(
            self._url(f"/tables/{full_name}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        columns = [
            {"name": c["name"], "type": c.get("type_name", "UNKNOWN"), "position": c.get("position", i)}
            for i, c in enumerate(data.get("columns", []))
        ]
        return PhysicalObject(
            native_name=data["name"],
            native_id=data.get("table_id"),
            object_type="table",
            parent_path=schema_name,
            properties={
                "columns": columns,
                "column_count": len(columns),
                "table_type": data.get("table_type", ""),
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        full_name = f"{self._catalog}.{schema_name}.{table_name}"
        resp = requests.get(
            self._url(f"/tables/{full_name}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            PhysicalObject(
                native_name=c["name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": c.get("type_name", "UNKNOWN")},
            )
            for c in data.get("columns", [])
        ]
