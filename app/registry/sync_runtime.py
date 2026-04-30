from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.adapters.base import CatalogAdapter, PhysicalObject
from app.registry.common import now_iso
from app.registry.factories import validate_datasource_type
from app.storage.metadata import MetadataStore


class RegistrySyncEngine:
    """Catalog sync runtime scoped to registry persistence."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def trigger_sync(
        self,
        datasource_id: str,
        adapter: CatalogAdapter,
        job_type: str = "full_sync",
        selections: list[dict[str, str]] | None = None,
    ) -> str:
        if selections is not None:
            job_type = "selective_sync"
        job_id = f"sync_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO sync_jobs (job_id, datasource_id, job_type, status, started_at, created_at)
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            [job_id, datasource_id, job_type, now, now],
        )
        try:
            if selections is not None:
                count = self._run_selective_sync(datasource_id, adapter, selections)
            else:
                count = self._run_full_sync(datasource_id, adapter)
            self.metadata.execute(
                "UPDATE sync_jobs SET status = 'succeeded', finished_at = ?, objects_synced = ? WHERE job_id = ?",
                [now_iso(), count, job_id],
            )
        except Exception as exc:
            self.metadata.execute(
                "UPDATE sync_jobs SET status = 'failed', finished_at = ?, error_message = ? WHERE job_id = ?",
                [now_iso(), str(exc), job_id],
            )
            raise
        return job_id

    def get_sync_status(self, job_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM sync_jobs WHERE job_id = ?", [job_id])
        if row is None:
            raise KeyError(f"Unknown sync job: {job_id}")
        return dict(row)

    def _run_full_sync(self, datasource_id: str, adapter: CatalogAdapter) -> int:
        now = now_iso()
        sync_version = f"v_{uuid4().hex[:8]}"
        connection_catalog = self._require_connection_catalog(datasource_id)
        count = 0

        schemas = adapter.list_schemas(connection_catalog)
        for schema_obj in schemas:
            schema_locator = self._build_connection_locator(
                connection_catalog=connection_catalog,
                schema_name=schema_obj.native_name,
            )
            schema_id = self._upsert_object(
                datasource_id=datasource_id,
                obj=schema_obj,
                parent_id=None,
                fqn=self._build_fqn(schema_locator),
                connection_locator=schema_locator,
                sync_version=sync_version,
                now=now,
            )
            count += 1

            tables = adapter.list_tables(schema_obj.native_name)
            for table_obj in tables:
                table_locator = self._build_connection_locator(
                    connection_catalog=connection_catalog,
                    schema_name=schema_obj.native_name,
                    table_name=table_obj.native_name,
                )
                table_id = self._upsert_object(
                    datasource_id=datasource_id,
                    obj=table_obj,
                    parent_id=schema_id,
                    fqn=self._build_fqn(table_locator),
                    connection_locator=table_locator,
                    sync_version=sync_version,
                    now=now,
                )
                count += 1

                columns = adapter.list_columns(schema_obj.native_name, table_obj.native_name)
                for column_obj in columns:
                    self._upsert_object(
                        datasource_id=datasource_id,
                        obj=column_obj,
                        parent_id=table_id,
                        fqn=self._build_child_fqn(table_locator, column_obj.native_name),
                        connection_locator=table_locator,
                        sync_version=sync_version,
                        now=now,
                    )
                    count += 1

                if adapter.capabilities().supports_partitions:
                    partitions = adapter.list_partitions(
                        schema_obj.native_name, table_obj.native_name
                    )
                    for partition_obj in partitions:
                        self._upsert_object(
                            datasource_id=datasource_id,
                            obj=partition_obj,
                            parent_id=table_id,
                            fqn=self._build_child_fqn(
                                table_locator, f"partition:{partition_obj.native_name}"
                            ),
                            connection_locator=table_locator,
                            sync_version=sync_version,
                            now=now,
                        )
                        count += 1

        self.metadata.execute(
            "DELETE FROM source_objects WHERE datasource_id = ? AND sync_version != ?",
            [datasource_id, sync_version],
        )
        return count

    def _run_selective_sync(
        self,
        datasource_id: str,
        adapter: CatalogAdapter,
        selections: list[dict[str, str]],
    ) -> int:
        now = now_iso()
        sync_version = f"v_{uuid4().hex[:8]}"
        connection_catalog = self._require_connection_catalog(datasource_id)
        count = 0
        by_schema: dict[str, list[str]] = {}
        for selection in selections:
            by_schema.setdefault(selection["schema_name"], []).append(selection["table_name"])

        for schema_name, table_names in by_schema.items():
            schema_obj = PhysicalObject(
                native_name=schema_name,
                native_id=None,
                object_type="schema",
                parent_path=connection_catalog,
            )
            schema_locator = self._build_connection_locator(
                connection_catalog=connection_catalog,
                schema_name=schema_name,
            )
            schema_id = self._upsert_object(
                datasource_id=datasource_id,
                obj=schema_obj,
                parent_id=None,
                fqn=self._build_fqn(schema_locator),
                connection_locator=schema_locator,
                sync_version=sync_version,
                now=now,
            )
            count += 1

            for table_name in table_names:
                try:
                    table_obj = adapter.get_table_detail(schema_name, table_name)
                except (KeyError, NotImplementedError):
                    table_obj = PhysicalObject(
                        native_name=table_name,
                        native_id=None,
                        object_type="table",
                        parent_path=schema_name,
                    )
                table_locator = self._build_connection_locator(
                    connection_catalog=connection_catalog,
                    schema_name=schema_name,
                    table_name=table_name,
                )
                table_id = self._upsert_object(
                    datasource_id=datasource_id,
                    obj=table_obj,
                    parent_id=schema_id,
                    fqn=self._build_fqn(table_locator),
                    connection_locator=table_locator,
                    sync_version=sync_version,
                    now=now,
                )
                count += 1

                columns = adapter.list_columns(schema_name, table_name)
                for column_obj in columns:
                    self._upsert_object(
                        datasource_id=datasource_id,
                        obj=column_obj,
                        parent_id=table_id,
                        fqn=self._build_child_fqn(table_locator, column_obj.native_name),
                        connection_locator=table_locator,
                        sync_version=sync_version,
                        now=now,
                    )
                    count += 1

                if adapter.capabilities().supports_partitions:
                    partitions = adapter.list_partitions(schema_name, table_name)
                    for partition_obj in partitions:
                        self._upsert_object(
                            datasource_id=datasource_id,
                            obj=partition_obj,
                            parent_id=table_id,
                            fqn=self._build_child_fqn(
                                table_locator, f"partition:{partition_obj.native_name}"
                            ),
                            connection_locator=table_locator,
                            sync_version=sync_version,
                            now=now,
                        )
                        count += 1

        self.metadata.execute(
            "DELETE FROM source_objects WHERE datasource_id = ? AND sync_version != ?",
            [datasource_id, sync_version],
        )
        return count

    def _upsert_object(
        self,
        datasource_id: str,
        obj: PhysicalObject,
        parent_id: str | None,
        fqn: str,
        connection_locator: dict[str, Any],
        sync_version: str,
        now: str,
    ) -> str:
        locator_json = json.dumps(connection_locator, sort_keys=True)
        catalog = connection_locator.get("catalog")
        schema = connection_locator.get("schema")
        table = connection_locator.get("table")
        adapter_properties = self._sync_properties(obj)
        existing = self.metadata.query_one(
            """
            SELECT object_id, properties_json
            FROM source_objects
            WHERE datasource_id = ? AND object_type = ? AND native_name = ?
              AND (
                (json_extract(authority_locator_json, '$.catalog') = ?)
                OR (json_extract(authority_locator_json, '$.catalog') IS NULL AND ? IS NULL)
              )
              AND (
                (json_extract(authority_locator_json, '$.schema') = ?)
                OR (json_extract(authority_locator_json, '$.schema') IS NULL AND ? IS NULL)
              )
              AND (
                (json_extract(authority_locator_json, '$.table') = ?)
                OR (json_extract(authority_locator_json, '$.table') IS NULL AND ? IS NULL)
              )
            """,
            [
                datasource_id,
                obj.object_type,
                obj.native_name,
                catalog,
                catalog,
                schema,
                schema,
                table,
                table,
            ],
        )
        if existing:
            object_id: str = str(existing["object_id"])
            # Preserve user-owned keys (anything not supplied by the adapter)
            existing_props = json.loads(existing["properties_json"] or "{}")
            adapter_keys = set(adapter_properties.keys())
            merged_props = dict(adapter_properties)
            for k, v in existing_props.items():
                if k == "columns" and obj.object_type == "table":
                    continue
                if k not in adapter_keys:
                    merged_props[k] = v
            self.metadata.execute(
                """
                UPDATE source_objects
                SET native_name = ?, native_id = ?, object_type = ?, parent_id = ?,
                    authority_locator_json = ?, properties_json = ?, sync_version = ?, synced_at = ?, updated_at = ?
                WHERE object_id = ?
                """,
                [
                    obj.native_name,
                    obj.native_id,
                    obj.object_type,
                    parent_id,
                    locator_json,
                    json.dumps(merged_props, default=str),
                    sync_version,
                    now,
                    now,
                    object_id,
                ],
            )
            return object_id

        object_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, datasource_id, object_type, parent_id, native_name, native_id,
                fqn, authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                object_id,
                datasource_id,
                obj.object_type,
                parent_id,
                obj.native_name,
                obj.native_id,
                fqn,
                locator_json,
                json.dumps(adapter_properties, default=str),
                sync_version,
                now,
                now,
                now,
            ],
        )
        return object_id

    @staticmethod
    def _sync_properties(obj: PhysicalObject) -> dict[str, Any]:
        properties = dict(obj.properties)
        if obj.object_type == "table":
            properties.pop("columns", None)
        return properties

    def _build_connection_locator(
        self,
        *,
        connection_catalog: str,
        schema_name: str,
        table_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "catalog": connection_catalog,
            "schema": schema_name,
            "table": table_name,
        }

    def _build_fqn(self, connection_locator: dict[str, Any]) -> str:
        return ".".join(
            str(value)
            for key in ("catalog", "schema", "table")
            for value in [connection_locator.get(key)]
            if isinstance(value, str) and value
        )

    def _build_child_fqn(self, connection_locator: dict[str, Any], child_name: str) -> str:
        base_fqn = self._build_fqn(connection_locator)
        if not base_fqn:
            return child_name
        return f"{base_fqn}.{child_name}"

    def _require_connection_catalog(self, datasource_id: str) -> str:
        """Derive the catalog name from the datasource's connection_json.

        The catalog is read directly from the connection parameters in
        ``connection_json`` rather than from a nested ``authority_json``
        structure.  For Trino datasources the ``catalog`` key in the
        connection dict is the authority catalog; for DuckDB it defaults
        to ``"analytics"`` when not specified.
        """
        row = self.metadata.query_one(
            "SELECT connection_json, datasource_type FROM datasources WHERE datasource_id = ?",
            [datasource_id],
        )
        if row is None:
            raise KeyError(f"Unknown datasource: {datasource_id}")
        connection = json.loads(str(row["connection_json"]))
        datasource_type = str(row["datasource_type"])
        # Validate the type to catch corrupted rows early
        validate_datasource_type(datasource_type)

        catalog: str | None = None
        if datasource_type == "trino":
            catalog = connection.get("catalog")
        elif datasource_type == "duckdb":
            catalog = connection.get("catalog") or connection.get("database")

        if not catalog:
            raise ValueError(f"Datasource '{datasource_id}' is missing catalog in connection_json")
        return catalog
