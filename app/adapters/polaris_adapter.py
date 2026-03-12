"""Polaris/Iceberg REST catalog adapter — reads namespace/table metadata via the
Iceberg REST Catalog API spec."""

from __future__ import annotations

from typing import Any

import requests

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class PolarisAdapter(CatalogAdapter):
    """Catalog adapter for Apache Polaris / Iceberg REST Catalog."""

    def __init__(self, host: str, token: str = "", warehouse: str = "default") -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._warehouse = warehouse

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self._host}/v1/{self._warehouse}{path}"

    def source_type(self) -> str:
        return "polaris"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=False,
            supports_partitions=True,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=True,
        )

    def test_connection(self) -> bool:
        try:
            resp = requests.get(
                self._url("/namespaces"),
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        resp = requests.get(
            self._url("/namespaces"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for ns in data.get("namespaces", []):
            # namespaces are returned as arrays of strings, e.g. [["analytics"], ["raw"]]
            name = ns[-1] if isinstance(ns, list) else ns
            results.append(PhysicalObject(
                native_name=name,
                native_id=None,
                object_type="schema",
                parent_path=catalog_name or self._warehouse,
            ))
        return results

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        resp = requests.get(
            self._url(f"/namespaces/{schema_name}/tables"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for ident in data.get("identifiers", []):
            results.append(PhysicalObject(
                native_name=ident["name"],
                native_id=None,
                object_type="table",
                parent_path=schema_name,
            ))
        return results

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        resp = requests.get(
            self._url(f"/namespaces/{schema_name}/tables/{table_name}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("metadata", {})
        schema_def = metadata.get("schema", metadata.get("current-schema", {}))
        fields = schema_def.get("fields", [])
        columns = [
            {"name": f["name"], "type": str(f.get("type", "UNKNOWN"))}
            for f in fields
        ]
        partition_spec = metadata.get("partition-spec", metadata.get("default-partition-spec", []))
        return PhysicalObject(
            native_name=table_name,
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties={
                "columns": columns,
                "column_count": len(columns),
                "partition_spec": partition_spec,
                "metadata_location": data.get("metadata-location", ""),
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        resp = requests.get(
            self._url(f"/namespaces/{schema_name}/tables/{table_name}"),
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("metadata", {})
        schema_def = metadata.get("schema", metadata.get("current-schema", {}))
        fields = schema_def.get("fields", [])
        return [
            PhysicalObject(
                native_name=f["name"],
                native_id=str(f.get("id", "")),
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": str(f.get("type", "UNKNOWN"))},
            )
            for f in fields
        ]

    def list_partitions(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        detail = self.get_table_detail(schema_name, table_name)
        spec = detail.properties.get("partition_spec", [])
        return [
            PhysicalObject(
                native_name=p.get("name", f"partition_{i}"),
                native_id=None,
                object_type="partition",
                parent_path=f"{schema_name}.{table_name}",
                properties={"transform": p.get("transform", "identity")},
            )
            for i, p in enumerate(spec)
        ]
