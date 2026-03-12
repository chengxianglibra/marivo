from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class HiveMetastoreAdapter(CatalogAdapter):
    """Hive Metastore adapter — requires pyhive or thrift client.

    This is a structural implementation that defines the contract.  Full
    functionality requires a running Hive Metastore and the ``pyhive``
    dependency (declared as an optional extra in pyproject.toml).
    """

    def __init__(self, host: str, port: int = 9083, **kwargs: Any) -> None:
        self._host = host
        self._port = port
        self._kwargs = kwargs
        self._client: Any = None

    def source_type(self) -> str:
        return "hive_metastore"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=False,
            supports_partitions=True,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=False,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from hmsclient import HMSClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "hmsclient is required for the Hive Metastore adapter. "
                "Install it with: pip install hmsclient"
            ) from exc
        self._client = HMSClient(host=self._host, port=self._port)
        self._client.open()
        return self._client

    def test_connection(self) -> bool:
        try:
            client = self._get_client()
            client.get_all_databases()
            return True
        except Exception:
            return False

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        client = self._get_client()
        databases = client.get_all_databases()
        return [
            PhysicalObject(
                native_name=db,
                native_id=db,
                object_type="schema",
                parent_path=catalog_name or "hive",
            )
            for db in databases
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        client = self._get_client()
        tables = client.get_all_tables(schema_name)
        return [
            PhysicalObject(
                native_name=table,
                native_id=f"{schema_name}.{table}",
                object_type="table",
                parent_path=schema_name,
            )
            for table in tables
        ]

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        client = self._get_client()
        table = client.get_table(schema_name, table_name)
        cols = [
            {"name": col.name, "type": col.type, "comment": col.comment or ""}
            for col in (table.sd.cols if table.sd else [])
        ]
        return PhysicalObject(
            native_name=table_name,
            native_id=f"{schema_name}.{table_name}",
            object_type="table",
            parent_path=schema_name,
            properties={
                "location": table.sd.location if table.sd else None,
                "input_format": table.sd.inputFormat if table.sd else None,
                "output_format": table.sd.outputFormat if table.sd else None,
                "columns": cols,
                "column_count": len(cols),
                "owner": table.owner,
                "create_time": table.createTime,
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        detail = self.get_table_detail(schema_name, table_name)
        return [
            PhysicalObject(
                native_name=col["name"],
                native_id=f"{schema_name}.{table_name}.{col['name']}",
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={"data_type": col["type"], "comment": col.get("comment", "")},
            )
            for col in detail.properties.get("columns", [])
        ]

    def list_partitions(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        client = self._get_client()
        partitions = client.get_partitions(schema_name, table_name, max_parts=1000)
        return [
            PhysicalObject(
                native_name="/".join(p.values),
                native_id=None,
                object_type="partition",
                parent_path=f"{schema_name}.{table_name}",
                properties={"values": p.values, "location": p.sd.location if p.sd else None},
            )
            for p in partitions
        ]
