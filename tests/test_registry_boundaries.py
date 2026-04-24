from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.engines import EngineService
from app.mappings import MappingService
from app.registry import EngineRegistry, MappingRegistry, RegistrySyncEngine, SourceRegistry
from app.sources import SourceService
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.sync import SyncEngine


class RegistryBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.meta_path = Path(self.temp_dir.name) / "registry-boundaries.meta.sqlite"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.metadata.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_legacy_facades_subclass_registry_layer(self) -> None:
        self.assertIsInstance(SourceService(self.metadata), SourceRegistry)
        self.assertIsInstance(EngineService(self.metadata), EngineRegistry)
        self.assertIsInstance(MappingService(self.metadata), MappingRegistry)
        self.assertIsInstance(SyncEngine(self.metadata), RegistrySyncEngine)

    def test_duckdb_source_requires_explicit_synthetic_catalog(self) -> None:
        source_registry = SourceRegistry(self.metadata)

        with self.assertRaisesRegex(ValueError, "synthetic_catalog is required"):
            source_registry.register_source(
                "duckdb",
                "Missing Synthetic Catalog",
                authority={"catalog_system": "duckdb", "connection": {"path": "data.duckdb"}},
            )

    def test_mapping_rejects_duplicate_authority_catalog(self) -> None:
        source_registry = SourceRegistry(self.metadata)
        engine_registry = EngineRegistry(self.metadata)
        mapping_registry = MappingRegistry(self.metadata)
        source = source_registry.register_source(
            "duckdb",
            "DuckDB Source",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": "data.duckdb"},
                "synthetic_catalog": "main",
            },
        )
        engine = engine_registry.register_engine(
            "duckdb",
            "DuckDB Engine",
            connection={"path": "data.duckdb"},
        )

        with self.assertRaisesRegex(ValueError, "duplicate authority_catalog"):
            mapping_registry.create_mapping(
                source["source_id"],
                engine["engine_id"],
                catalog_mappings=[
                    {"authority_catalog": "main", "execution_catalog": "main"},
                    {"authority_catalog": "main", "execution_catalog": "duckdb_runtime"},
                ],
            )

    def test_mapping_invalid_engine_source_combo_fails_closed(self) -> None:
        source_registry = SourceRegistry(self.metadata)
        engine_registry = EngineRegistry(self.metadata)
        mapping_registry = MappingRegistry(self.metadata)
        source = source_registry.register_source(
            "duckdb",
            "DuckDB Source",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": "data.duckdb"},
                "synthetic_catalog": "main",
            },
        )
        engine = engine_registry.register_engine(
            "trino",
            "Trino Engine",
            connection={"host": "localhost"},
        )

        mapping = mapping_registry.create_mapping(
            source["source_id"],
            engine["engine_id"],
            catalog_mappings=[{"authority_catalog": "main", "execution_catalog": "lakehouse"}],
        )

        self.assertEqual(mapping["readiness_status"], "not_ready")
        self.assertEqual(mapping["failure_code"], "mapping_invalid_type_combo")

    def test_mapping_incomplete_fails_closed(self) -> None:
        source_registry = SourceRegistry(self.metadata)
        engine_registry = EngineRegistry(self.metadata)
        mapping_registry = MappingRegistry(self.metadata)
        source = source_registry.register_source(
            "duckdb",
            "DuckDB Source",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": "data.duckdb"},
                "synthetic_catalog": "main",
            },
        )
        engine = engine_registry.register_engine(
            "duckdb",
            "DuckDB Engine",
            connection={"path": "data.duckdb"},
        )
        now = "2026-04-24T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_events",
                source["source_id"],
                "events",
                "main.analytics.events",
                json.dumps({"catalog": "main", "schema": "analytics", "table": "events"}),
                now,
                now,
                now,
            ],
        )

        mapping = mapping_registry.create_mapping(
            source["source_id"],
            engine["engine_id"],
            catalog_mappings=[{"authority_catalog": "other", "execution_catalog": "main"}],
        )

        self.assertEqual(mapping["readiness_status"], "not_ready")
        self.assertEqual(mapping["failure_code"], "mapping_incomplete")


if __name__ == "__main__":
    unittest.main()
