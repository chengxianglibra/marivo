"""AWS Glue catalog adapter — reads schema/table/column metadata via boto3."""

from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class GlueCatalogAdapter(CatalogAdapter):
    """Catalog adapter for AWS Glue Data Catalog (boto3)."""

    def __init__(
        self,
        region: str = "us-east-1",
        catalog_id: str | None = None,
    ) -> None:
        self._region = region
        self._catalog_id = catalog_id

    def _get_client(self) -> Any:
        import boto3
        return boto3.client("glue", region_name=self._region)

    def source_type(self) -> str:
        return "aws_glue"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=True,
            supports_partitions=True,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=True,
        )

    def test_connection(self) -> bool:
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = {}
            if self._catalog_id:
                kwargs["CatalogId"] = self._catalog_id
            client.get_databases(**kwargs)
            return True
        except Exception:
            return False

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        client = self._get_client()
        kwargs: dict[str, Any] = {}
        if self._catalog_id:
            kwargs["CatalogId"] = self._catalog_id
        resp = client.get_databases(**kwargs)
        return [
            PhysicalObject(
                native_name=db["Name"],
                native_id=None,
                object_type="schema",
                parent_path=catalog_name or "glue",
                properties={"description": db.get("Description", "")},
            )
            for db in resp.get("DatabaseList", [])
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        client = self._get_client()
        kwargs: dict[str, Any] = {"DatabaseName": schema_name}
        if self._catalog_id:
            kwargs["CatalogId"] = self._catalog_id
        resp = client.get_tables(**kwargs)
        results = []
        for table in resp.get("TableList", []):
            sd = table.get("StorageDescriptor", {})
            columns = sd.get("Columns", [])
            results.append(PhysicalObject(
                native_name=table["Name"],
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={
                    "table_type": table.get("TableType", ""),
                    "column_count": len(columns),
                },
            ))
        return results

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        client = self._get_client()
        kwargs: dict[str, Any] = {"DatabaseName": schema_name, "Name": table_name}
        if self._catalog_id:
            kwargs["CatalogId"] = self._catalog_id
        resp = client.get_table(**kwargs)
        table = resp["Table"]
        sd = table.get("StorageDescriptor", {})
        columns = [
            {"name": c["Name"], "type": c["Type"]}
            for c in sd.get("Columns", [])
        ]
        partition_keys = [
            {"name": p["Name"], "type": p["Type"]}
            for p in table.get("PartitionKeys", [])
        ]
        return PhysicalObject(
            native_name=table["Name"],
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties={
                "columns": columns,
                "column_count": len(columns),
                "partition_keys": partition_keys,
                "location": sd.get("Location", ""),
                "table_type": table.get("TableType", ""),
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        client = self._get_client()
        kwargs: dict[str, Any] = {"DatabaseName": schema_name, "Name": table_name}
        if self._catalog_id:
            kwargs["CatalogId"] = self._catalog_id
        resp = client.get_table(**kwargs)
        table = resp["Table"]
        sd = table.get("StorageDescriptor", {})
        columns = sd.get("Columns", [])
        partition_keys = table.get("PartitionKeys", [])
        all_cols = columns + partition_keys
        return [
            PhysicalObject(
                native_name=c["Name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": c["Type"]},
            )
            for c in all_cols
        ]

    def list_partitions(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        client = self._get_client()
        kwargs: dict[str, Any] = {"DatabaseName": schema_name, "Name": table_name}
        if self._catalog_id:
            kwargs["CatalogId"] = self._catalog_id
        resp = client.get_table(**kwargs)
        partition_keys = resp["Table"].get("PartitionKeys", [])
        return [
            PhysicalObject(
                native_name=p["Name"],
                native_id=None,
                object_type="partition",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": p["Type"]},
            )
            for p in partition_keys
        ]
