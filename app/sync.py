from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.adapters.base import CatalogAdapter, PhysicalObject
from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    """Walks a CatalogAdapter and upserts source_objects in metadata."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def trigger_sync(
        self,
        source_id: str,
        adapter: CatalogAdapter,
        job_type: str = "full_sync",
        selections: list[dict[str, str]] | None = None,
    ) -> str:
        if selections is not None:
            job_type = "selective_sync"
        job_id = f"sync_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO sync_jobs (job_id, source_id, job_type, status, started_at, created_at)
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            [job_id, source_id, job_type, now, now],
        )
        try:
            if selections is not None:
                count = self._run_selective_sync(source_id, adapter, selections)
            else:
                count = self._run_full_sync(source_id, adapter)
            self.metadata.execute(
                "UPDATE sync_jobs SET status = 'succeeded', finished_at = ?, objects_synced = ? WHERE job_id = ?",
                [_now_iso(), count, job_id],
            )
        except Exception as exc:
            self.metadata.execute(
                "UPDATE sync_jobs SET status = 'failed', finished_at = ?, error_message = ? WHERE job_id = ?",
                [_now_iso(), str(exc), job_id],
            )
            raise
        return job_id

    def get_sync_status(self, job_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM sync_jobs WHERE job_id = ?", [job_id])
        if row is None:
            raise KeyError(f"Unknown sync job: {job_id}")
        return dict(row)

    def _run_full_sync(self, source_id: str, adapter: CatalogAdapter) -> int:
        now = _now_iso()
        sync_version = f"v_{uuid4().hex[:8]}"
        source_type = adapter.source_type()
        count = 0

        schemas = adapter.list_schemas()
        for schema_obj in schemas:
            schema_id = self._upsert_object(
                source_id=source_id,
                obj=schema_obj,
                parent_id=None,
                fqn=f"{source_type}.{schema_obj.native_name}",
                sync_version=sync_version,
                now=now,
            )
            count += 1

            tables = adapter.list_tables(schema_obj.native_name)
            for table_obj in tables:
                table_id = self._upsert_object(
                    source_id=source_id,
                    obj=table_obj,
                    parent_id=schema_id,
                    fqn=f"{source_type}.{schema_obj.native_name}.{table_obj.native_name}",
                    sync_version=sync_version,
                    now=now,
                )
                count += 1

                columns = adapter.list_columns(schema_obj.native_name, table_obj.native_name)
                for col_obj in columns:
                    self._upsert_object(
                        source_id=source_id,
                        obj=col_obj,
                        parent_id=table_id,
                        fqn=f"{source_type}.{schema_obj.native_name}.{table_obj.native_name}.{col_obj.native_name}",
                        sync_version=sync_version,
                        now=now,
                    )
                    count += 1

                if adapter.capabilities().supports_partitions:
                    partitions = adapter.list_partitions(schema_obj.native_name, table_obj.native_name)
                    for part_obj in partitions:
                        self._upsert_object(
                            source_id=source_id,
                            obj=part_obj,
                            parent_id=table_id,
                            fqn=f"{source_type}.{schema_obj.native_name}.{table_obj.native_name}.partition:{part_obj.native_name}",
                            sync_version=sync_version,
                            now=now,
                        )
                        count += 1

        # Remove stale objects from previous syncs
        self.metadata.execute(
            "DELETE FROM source_objects WHERE source_id = ? AND sync_version != ?",
            [source_id, sync_version],
        )

        return count

    def _run_selective_sync(
        self,
        source_id: str,
        adapter: CatalogAdapter,
        selections: list[dict[str, str]],
    ) -> int:
        """Sync only the selected schema.table pairs."""
        now = _now_iso()
        sync_version = f"v_{uuid4().hex[:8]}"
        source_type = adapter.source_type()
        count = 0

        # Group selections by schema
        by_schema: dict[str, list[str]] = {}
        for sel in selections:
            by_schema.setdefault(sel["schema_name"], []).append(sel["table_name"])

        for schema_name, table_names in by_schema.items():
            # Upsert schema object
            schema_obj = PhysicalObject(
                native_name=schema_name,
                native_id=None,
                object_type="schema",
                parent_path=source_type,
            )
            schema_id = self._upsert_object(
                source_id=source_id,
                obj=schema_obj,
                parent_id=None,
                fqn=f"{source_type}.{schema_name}",
                sync_version=sync_version,
                now=now,
            )
            count += 1

            for table_name in table_names:
                # Get table detail from adapter
                try:
                    table_obj = adapter.get_table_detail(schema_name, table_name)
                except (KeyError, NotImplementedError):
                    # Fallback: construct a minimal table object
                    table_obj = PhysicalObject(
                        native_name=table_name,
                        native_id=None,
                        object_type="table",
                        parent_path=schema_name,
                    )
                table_id = self._upsert_object(
                    source_id=source_id,
                    obj=table_obj,
                    parent_id=schema_id,
                    fqn=f"{source_type}.{schema_name}.{table_name}",
                    sync_version=sync_version,
                    now=now,
                )
                count += 1

                # Sync columns
                columns = adapter.list_columns(schema_name, table_name)
                for col_obj in columns:
                    self._upsert_object(
                        source_id=source_id,
                        obj=col_obj,
                        parent_id=table_id,
                        fqn=f"{source_type}.{schema_name}.{table_name}.{col_obj.native_name}",
                        sync_version=sync_version,
                        now=now,
                    )
                    count += 1

                # Sync partitions if supported
                if adapter.capabilities().supports_partitions:
                    partitions = adapter.list_partitions(schema_name, table_name)
                    for part_obj in partitions:
                        self._upsert_object(
                            source_id=source_id,
                            obj=part_obj,
                            parent_id=table_id,
                            fqn=f"{source_type}.{schema_name}.{table_name}.partition:{part_obj.native_name}",
                            sync_version=sync_version,
                            now=now,
                        )
                        count += 1

        # Remove stale objects from previous syncs
        self.metadata.execute(
            "DELETE FROM source_objects WHERE source_id = ? AND sync_version != ?",
            [source_id, sync_version],
        )

        return count

    def _upsert_object(
        self,
        source_id: str,
        obj: PhysicalObject,
        parent_id: str | None,
        fqn: str,
        sync_version: str,
        now: str,
    ) -> str:
        existing = self.metadata.query_one(
            "SELECT object_id FROM source_objects WHERE source_id = ? AND fqn = ?",
            [source_id, fqn],
        )
        if existing:
            object_id = existing["object_id"]
            self.metadata.execute(
                """
                UPDATE source_objects
                SET native_name = ?, native_id = ?, object_type = ?, parent_id = ?,
                    properties_json = ?, sync_version = ?, synced_at = ?, updated_at = ?
                WHERE object_id = ?
                """,
                [
                    obj.native_name, obj.native_id, obj.object_type, parent_id,
                    json.dumps(obj.properties, default=str), sync_version, now, now,
                    object_id,
                ],
            )
            return object_id

        object_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id,
                fqn, properties_json, sync_version, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                object_id, source_id, obj.object_type, parent_id, obj.native_name,
                obj.native_id, fqn, json.dumps(obj.properties, default=str),
                sync_version, now, now, now,
            ],
        )
        return object_id
