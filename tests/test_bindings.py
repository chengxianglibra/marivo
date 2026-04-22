from __future__ import annotations

import tempfile
import unittest
from datetime import UTC
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.bindings import BindingService
from app.engines import EngineService
from app.main import create_app
from app.routing import QueryRouter, ResolvedRoute, RoutingIntent
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.sync import SyncEngine
from tests.shared_fixtures import get_seeded_duckdb_path


def build_duckdb_source_payload(path: str, display_name: str) -> dict:
    return {
        "source_type": "duckdb",
        "display_name": display_name,
        "authority": {
            "catalog_system": "duckdb",
            "connection": {"path": path},
            "synthetic_catalog": "main",
        },
        "sync": {"mode": "selected"},
    }


def build_duckdb_engine_payload(path: str, display_name: str) -> dict:
    return {
        "engine_type": "duckdb",
        "display_name": display_name,
        "connection": {"path": path},
    }


def build_trino_engine_payload(
    display_name: str,
    connection: dict[str, object],
    deployment_capabilities: dict[str, object] | None = None,
) -> dict:
    payload: dict[str, object] = {
        "engine_type": "trino",
        "display_name": display_name,
        "connection": connection,
    }
    if deployment_capabilities is not None:
        payload["deployment_capabilities"] = deployment_capabilities
    return payload


class BindingServiceTests(unittest.TestCase):
    """Unit tests for BindingService using a real SQLiteMetadataStore."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "test_bindings.meta.sqlite"
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()

        cls.source_service = SourceService(cls.metadata)
        cls.engine_service = EngineService(cls.metadata)
        cls.binding_service = BindingService(cls.metadata)

        # Create a DuckDB for the local source adapter
        cls.duckdb_path = Path(cls.temp_dir.name) / "test_local.duckdb"
        get_seeded_duckdb_path(cls.duckdb_path)
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        _ae = DuckDBAnalyticsEngine(cls.duckdb_path)
        _ae.initialize()

        # Register a source and two engines for tests
        cls.source = cls.source_service.register_source(
            "duckdb", "Test Source", {"path": str(cls.duckdb_path)}
        )
        cls.engine1 = cls.engine_service.register_engine(
            "duckdb", "Engine A", {"path": "/tmp/a.duckdb"}
        )
        cls.engine2 = cls.engine_service.register_engine(
            "duckdb", "Engine B", {"path": "/tmp/b.duckdb"}
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_create_and_list_bindings(self) -> None:
        binding = self.binding_service.create_binding(
            self.source["source_id"],
            self.engine1["engine_id"],
            priority=5,
        )
        self.assertTrue(binding["binding_id"].startswith("bind_"))
        self.assertEqual(binding["source_id"], self.source["source_id"])
        self.assertEqual(binding["engine_id"], self.engine1["engine_id"])
        self.assertEqual(binding["priority"], 5)
        self.assertEqual(binding["status"], "active")

        bindings = self.binding_service.list_bindings(source_id=self.source["source_id"])
        self.assertTrue(any(b["binding_id"] == binding["binding_id"] for b in bindings))

    def test_get_binding(self) -> None:
        binding = self.binding_service.create_binding(
            self.source["source_id"],
            self.engine2["engine_id"],
            priority=3,
        )
        fetched = self.binding_service.get_binding(binding["binding_id"])
        self.assertEqual(fetched["binding_id"], binding["binding_id"])
        self.assertEqual(fetched["priority"], 3)

    def test_get_binding_404(self) -> None:
        with self.assertRaises(KeyError):
            self.binding_service.get_binding("bind_nonexistent")

    def test_ensure_binding_idempotent(self) -> None:
        src = self.source_service.register_source("duckdb", "Idempotent Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "Idempotent Eng", {"path": "/tmp/idm.duckdb"}
        )

        b1 = self.binding_service.ensure_binding(src["source_id"], eng["engine_id"], priority=7)
        b2 = self.binding_service.ensure_binding(src["source_id"], eng["engine_id"], priority=99)
        self.assertEqual(b1["binding_id"], b2["binding_id"])
        # Priority stays at original value (idempotent, not upsert)
        self.assertEqual(b2["priority"], 7)

    def test_delete_binding(self) -> None:
        src = self.source_service.register_source("duckdb", "Del Src", {})
        eng = self.engine_service.register_engine("duckdb", "Del Eng", {"path": "/tmp/del.duckdb"})
        binding = self.binding_service.create_binding(src["source_id"], eng["engine_id"])

        self.binding_service.delete_binding(binding["binding_id"])
        with self.assertRaises(KeyError):
            self.binding_service.get_binding(binding["binding_id"])

    def test_create_binding_invalid_source(self) -> None:
        with self.assertRaises(KeyError):
            self.binding_service.create_binding("src_nonexistent", self.engine1["engine_id"])

    def test_create_binding_invalid_engine(self) -> None:
        with self.assertRaises(KeyError):
            self.binding_service.create_binding(self.source["source_id"], "eng_nonexistent")

    def test_get_engines_for_source(self) -> None:
        src = self.source_service.register_source("duckdb", "Multi-Eng Src", {})
        eng_lo = self.engine_service.register_engine(
            "duckdb", "Low Prio Eng", {"path": "/tmp/lo.duckdb"}
        )
        eng_hi = self.engine_service.register_engine(
            "duckdb", "High Prio Eng", {"path": "/tmp/hi.duckdb"}
        )

        self.binding_service.create_binding(src["source_id"], eng_lo["engine_id"], priority=1)
        self.binding_service.create_binding(src["source_id"], eng_hi["engine_id"], priority=10)

        engines = self.binding_service.get_engines_for_source(src["source_id"])
        self.assertEqual(len(engines), 2)
        # Highest priority first
        self.assertEqual(engines[0]["engine_id"], eng_hi["engine_id"])
        self.assertEqual(engines[0]["priority"], 10)
        self.assertEqual(engines[1]["engine_id"], eng_lo["engine_id"])

    def test_unique_constraint(self) -> None:
        """Same (source_id, engine_id) can't be double-inserted; ensure handles it."""
        src = self.source_service.register_source("duckdb", "Unique Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "Unique Eng", {"path": "/tmp/uq.duckdb"}
        )

        self.binding_service.create_binding(src["source_id"], eng["engine_id"])
        # Direct insert of same pair should fail (UNIQUE constraint)
        with self.assertRaises(Exception):
            self.binding_service.create_binding(src["source_id"], eng["engine_id"])

        # But ensure_binding is fine
        b = self.binding_service.ensure_binding(src["source_id"], eng["engine_id"])
        self.assertIsNotNone(b["binding_id"])

    # ── Namespace tests ───────────────────────────────────────────

    def test_create_binding_with_namespace(self) -> None:
        src = self.source_service.register_source("duckdb", "NS Create Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "NS Create Eng", {"path": "/tmp/ns_create.duckdb"}
        )

        binding = self.binding_service.create_binding(
            src["source_id"],
            eng["engine_id"],
            priority=5,
            namespace={"catalog": "hive"},
        )
        self.assertEqual(binding["namespace"], {"catalog": "hive"})

        # Verify persisted via get_binding
        fetched = self.binding_service.get_binding(binding["binding_id"])
        self.assertEqual(fetched["namespace"], {"catalog": "hive"})

    def test_ensure_binding_with_namespace(self) -> None:
        src = self.source_service.register_source("duckdb", "NS Ensure Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "NS Ensure Eng", {"path": "/tmp/ns_ensure.duckdb"}
        )

        b1 = self.binding_service.ensure_binding(
            src["source_id"],
            eng["engine_id"],
            priority=5,
            namespace={"catalog": "spark_catalog"},
        )
        self.assertEqual(b1["namespace"], {"catalog": "spark_catalog"})

        # Idempotent: second call returns existing
        b2 = self.binding_service.ensure_binding(
            src["source_id"],
            eng["engine_id"],
            priority=99,
            namespace={"catalog": "different"},
        )
        self.assertEqual(b1["binding_id"], b2["binding_id"])
        # Namespace stays at original value
        self.assertEqual(b2["namespace"], {"catalog": "spark_catalog"})

    def test_namespace_default_empty(self) -> None:
        src = self.source_service.register_source("duckdb", "NS Default Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "NS Default Eng", {"path": "/tmp/ns_default.duckdb"}
        )

        binding = self.binding_service.create_binding(
            src["source_id"],
            eng["engine_id"],
        )
        self.assertEqual(binding["namespace"], {})

    def test_get_engines_for_source_includes_namespace(self) -> None:
        src = self.source_service.register_source("duckdb", "NS Engines Src", {})
        eng = self.engine_service.register_engine(
            "duckdb", "NS Engines Eng", {"path": "/tmp/ns_engines.duckdb"}
        )

        self.binding_service.create_binding(
            src["source_id"],
            eng["engine_id"],
            priority=5,
            namespace={"catalog": "hive", "schema": "prod"},
        )

        engines = self.binding_service.get_engines_for_source(src["source_id"])
        self.assertEqual(len(engines), 1)
        self.assertEqual(engines[0]["namespace"], {"catalog": "hive", "schema": "prod"})


class QueryRouterTests(unittest.TestCase):
    """Unit tests for QueryRouter."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_router.duckdb"
        meta_path = db_path.with_suffix(".meta.sqlite")
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()

        # Initialize DuckDB so the local adapter can read its catalog
        cls.local_duckdb_path = Path(cls.temp_dir.name) / "test_local_catalog.duckdb"
        get_seeded_duckdb_path(cls.local_duckdb_path)
        _ae = DuckDBAnalyticsEngine(cls.local_duckdb_path)
        _ae.initialize()

        cls.source_service = SourceService(cls.metadata)
        cls.engine_service = EngineService(cls.metadata)
        cls.binding_service = BindingService(cls.metadata)
        cls.sync_engine = SyncEngine(cls.metadata)

        # Register source, sync it (creates source_objects for local demo tables)
        cls.source = cls.source_service.register_source(
            "duckdb", "Router Source", {"path": str(cls.local_duckdb_path)}
        )
        adapter = cls.source_service.get_adapter(cls.source["source_id"])
        cls.sync_engine.trigger_sync(
            cls.source["source_id"],
            adapter,
            selections=[
                {"schema_name": "analytics", "table_name": "watch_events"},
                {"schema_name": "analytics", "table_name": "player_qoe"},
                {"schema_name": "analytics", "table_name": "ad_events"},
                {"schema_name": "analytics", "table_name": "recommendation_events"},
            ],
        )

        # Register an engine and bind it
        cls.engine = cls.engine_service.register_engine(
            "duckdb",
            "Router Engine",
            {"path": str(db_path)},
        )
        cls.binding_service.create_binding(
            cls.source["source_id"],
            cls.engine["engine_id"],
            priority=5,
        )

        cls.router = QueryRouter(cls.metadata, cls.engine_service)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_resolve_single_table(self) -> None:
        engine = self.router.resolve_engine_for_tables(["watch_events"])
        self.assertIsNotNone(engine)

    def test_resolve_multiple_tables_same_source(self) -> None:
        engine = self.router.resolve_engine_for_tables(["watch_events", "player_qoe"])
        self.assertIsNotNone(engine)

    def test_resolve_tables_different_sources_common_engine(self) -> None:
        # Create a second source, sync it, and bind it to the same engine
        src2 = self.source_service.register_source(
            "duckdb", "Router Source 2", {"path": str(self.local_duckdb_path)}
        )
        adapter2 = self.source_service.get_adapter(src2["source_id"])
        self.sync_engine.trigger_sync(
            src2["source_id"],
            adapter2,
            selections=[
                {"schema_name": "analytics", "table_name": "watch_events"},
                {"schema_name": "analytics", "table_name": "player_qoe"},
                {"schema_name": "analytics", "table_name": "ad_events"},
                {"schema_name": "analytics", "table_name": "recommendation_events"},
            ],
        )

        self.binding_service.create_binding(
            src2["source_id"],
            self.engine["engine_id"],
            priority=3,
        )

        # Tables from local demo exist in both sources — pick one from each
        # Both synced from the same local adapter, so same table names exist in both
        # The router should find the common engine
        # Since both sources have the same table names, let's use source_objects directly
        objs_src1 = self.source_service.list_objects(self.source["source_id"], object_type="table")
        objs_src2 = self.source_service.list_objects(src2["source_id"], object_type="table")

        # Verify tables exist
        self.assertGreater(len(objs_src1), 0)
        self.assertGreater(len(objs_src2), 0)

        # The router queries by native_name, so if both sources have "watch_events",
        # it'll pick the first one found. We just need to verify no error is raised.
        engine = self.router.resolve_engine_for_tables(["watch_events"])
        self.assertIsNotNone(engine)

    def test_resolve_tables_no_common_engine(self) -> None:
        # Create two sources with different engines, no overlap
        src_a = self.source_service.register_source("duckdb", "No Common A", {})
        src_b = self.source_service.register_source("duckdb", "No Common B", {})

        eng_a = self.engine_service.register_engine(
            "duckdb", "Eng Only A", {"path": "/tmp/eng_a.duckdb"}
        )
        eng_b = self.engine_service.register_engine(
            "duckdb", "Eng Only B", {"path": "/tmp/eng_b.duckdb"}
        )

        self.binding_service.create_binding(src_a["source_id"], eng_a["engine_id"])
        self.binding_service.create_binding(src_b["source_id"], eng_b["engine_id"])

        # Insert synthetic source_objects with unique table names
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        for src, tbl_name in [(src_a, "unique_table_a"), (src_b, "unique_table_b")]:
            obj_id = f"obj_{uuid4().hex[:12]}"
            self.metadata.execute(
                """
                INSERT INTO source_objects
                    (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
                VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
                """,
                [obj_id, src["source_id"], tbl_name, f"demo.{tbl_name}", now, now],
            )

        with self.assertRaises(ValueError):
            self.router.resolve_engine_for_tables(["unique_table_a", "unique_table_b"])

    def test_resolve_unknown_table(self) -> None:
        with self.assertRaises(KeyError):
            self.router.resolve_engine_for_tables(["totally_nonexistent_table_xyz"])

    def test_resolve_no_bindings(self) -> None:
        # Create a source with synced objects but no bindings
        src = self.source_service.register_source("duckdb", "No Bindings Src", {})

        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        obj_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
            """,
            [obj_id, src["source_id"], "no_binding_table", "demo.no_binding_table", now, now],
        )

        with self.assertRaises(ValueError):
            self.router.resolve_engine_for_tables(["no_binding_table"])

    # ── Namespace qualification tests ─────────────────────────────

    def test_qualify_table_name_no_namespace(self) -> None:
        """Empty namespace → bare table name (no schema prefix because
        _get_table_schema looks up parent hierarchy which exists for synced tables)."""
        # Tables synced from the local adapter have a parent schema 'analytics'.
        # With empty namespace, qualify_table_name should return 'analytics.watch_events'.
        table_source_object = self.router._resolve_table_source_object("watch_events")
        qualified = self.router.qualify_table_name(
            table_source_object,
            {"namespace": {}},
        )
        self.assertEqual(qualified, "analytics.watch_events")

    def test_qualify_table_name_with_catalog(self) -> None:
        """Namespace with catalog → catalog.schema.table."""
        table_source_object = self.router._resolve_table_source_object("watch_events")
        qualified = self.router.qualify_table_name(
            table_source_object,
            {"namespace": {"catalog": "hive"}},
        )
        self.assertEqual(qualified, "hive.analytics.watch_events")

    def test_qualify_table_name_with_catalog_and_schema_override(self) -> None:
        """Namespace with catalog and schema override → catalog.override.table."""
        table_source_object = self.router._resolve_table_source_object("watch_events")
        qualified = self.router.qualify_table_name(
            table_source_object,
            {"namespace": {"catalog": "hive", "schema": "prod"}},
        )
        self.assertEqual(qualified, "hive.prod.watch_events")

    def test_qualify_table_name_no_parent_no_namespace(self) -> None:
        """Table with no parent schema and empty namespace → bare name."""
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        obj_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
            """,
            [obj_id, self.source["source_id"], "orphan_table", "demo.orphan_table", now, now],
        )

        table_source_object = self.router._resolve_table_source_object("orphan_table")
        qualified = self.router.qualify_table_name(table_source_object, {"namespace": {}})
        self.assertEqual(qualified, "orphan_table")

    def test_resolve_tables_returns_resolved_route(self) -> None:
        """resolve_tables() returns a ResolvedRoute with qualified_names."""
        route = self.router.resolve_tables(["watch_events"])
        self.assertIsInstance(route, ResolvedRoute)
        self.assertIsNotNone(route.engine)
        self.assertEqual(route.engine_id, self.engine["engine_id"])
        # With empty namespace, tables get schema from parent
        self.assertIn("watch_events", route.qualified_names)
        self.assertEqual(route.qualified_names["watch_events"], "analytics.watch_events")

    def test_resolve_tables_accepts_full_fqn(self) -> None:
        route = self.router.resolve_tables(["duckdb.analytics.watch_events"])
        self.assertEqual(route.engine_id, self.engine["engine_id"])
        self.assertEqual(
            route.qualified_names["duckdb.analytics.watch_events"],
            "analytics.watch_events",
        )

    def test_resolve_tables_short_name_is_ambiguous_across_sources(self) -> None:
        src2 = self.source_service.register_source("duckdb", "Router Source Ambiguous", {})
        self.binding_service.create_binding(
            src2["source_id"],
            self.engine["engine_id"],
            priority=3,
        )

        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'other_schema', 'duckdb.other_schema', '{}', ?, ?)
            """,
            [schema_id, src2["source_id"], now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'watch_events', 'duckdb.other_schema.watch_events', '{}', ?, ?)
            """,
            [table_id, src2["source_id"], schema_id, now, now],
        )

        with self.assertRaisesRegex(ValueError, "Ambiguous table name"):
            self.router.resolve_tables(["watch_events"])

    def test_resolve_tables_full_fqn_disambiguates_duplicate_short_name(self) -> None:
        src2 = self.source_service.register_source(
            "duckdb", "Router Source Duplicate", {"path": str(self.local_duckdb_path)}
        )
        adapter2 = self.source_service.get_adapter(src2["source_id"])
        self.sync_engine.trigger_sync(
            src2["source_id"],
            adapter2,
            selections=[
                {"schema_name": "analytics", "table_name": "watch_events"},
                {"schema_name": "analytics", "table_name": "player_qoe"},
                {"schema_name": "analytics", "table_name": "ad_events"},
                {"schema_name": "analytics", "table_name": "recommendation_events"},
            ],
        )
        self.binding_service.create_binding(
            src2["source_id"],
            self.engine["engine_id"],
            priority=3,
        )

        route = self.router.resolve_tables(["duckdb.analytics.watch_events"])
        self.assertEqual(
            route.qualified_names["duckdb.analytics.watch_events"],
            "analytics.watch_events",
        )

    def test_resolve_tables_with_namespace_binding(self) -> None:
        """resolve_tables() uses the binding's namespace for qualification."""
        # Create a new source+engine+binding with namespace
        src = self.source_service.register_source(
            "duckdb", "NS Resolve Src", {"path": str(self.local_duckdb_path)}
        )
        adapter = self.source_service.get_adapter(src["source_id"])
        self.sync_engine.trigger_sync(
            src["source_id"],
            adapter,
            selections=[
                {"schema_name": "analytics", "table_name": "watch_events"},
                {"schema_name": "analytics", "table_name": "player_qoe"},
                {"schema_name": "analytics", "table_name": "ad_events"},
                {"schema_name": "analytics", "table_name": "recommendation_events"},
            ],
        )

        eng = self.engine_service.register_engine(
            "duckdb", "NS Resolve Eng", {"path": "/tmp/ns_resolve.duckdb"}
        )
        self.binding_service.create_binding(
            src["source_id"],
            eng["engine_id"],
            priority=100,
            namespace={"catalog": "hive"},
        )

        # Insert a unique table for this source so resolution is unambiguous
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        # Create schema object
        schema_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'myschema', 'demo.myschema', '{}', ?, ?)
            """,
            [schema_id, src["source_id"], now, now],
        )
        # Create table with parent
        table_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'ns_resolve_tbl', 'demo.myschema.ns_resolve_tbl', '{}', ?, ?)
            """,
            [table_id, src["source_id"], schema_id, now, now],
        )

        router = QueryRouter(self.metadata, self.engine_service)
        route = router.resolve_tables(["ns_resolve_tbl"])
        self.assertEqual(route.engine_id, eng["engine_id"])
        self.assertEqual(route.qualified_names["ns_resolve_tbl"], "hive.myschema.ns_resolve_tbl")

    def test_resolve_tables_uses_capability_tiebreaker_for_equal_priority(self) -> None:
        from datetime import datetime
        from uuid import uuid4

        src = self.source_service.register_source(
            "duckdb", "Capability Tie Src", {"path": str(self.local_duckdb_path)}
        )
        adapter = self.source_service.get_adapter(src["source_id"])
        self.sync_engine.trigger_sync(
            src["source_id"],
            adapter,
            selections=[
                {"schema_name": "analytics", "table_name": "watch_events"},
                {"schema_name": "analytics", "table_name": "player_qoe"},
                {"schema_name": "analytics", "table_name": "ad_events"},
                {"schema_name": "analytics", "table_name": "recommendation_events"},
            ],
        )

        duck = self.engine_service.register_engine(
            "duckdb",
            "Capability Tie Duck",
            {"path": "/tmp/cap_tie.duckdb"},
        )
        trino = self.engine_service.register_engine(
            "trino",
            "Capability Tie Trino",
            {
                "host": "localhost",
                "port": 8080,
                "user": "test",
                "catalog": "hive",
                "schema": "default",
            },
        )

        self.binding_service.create_binding(src["source_id"], duck["engine_id"], priority=7)
        self.binding_service.create_binding(src["source_id"], trino["engine_id"], priority=7)

        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'cap_tie_schema', 'demo.cap_tie_schema', '{}', ?, ?)
            """,
            [schema_id, src["source_id"], now, now],
        )
        for table_name in ("cap_tie_watch", "cap_tie_qoe"):
            table_id = f"obj_{uuid4().hex[:12]}"
            self.metadata.execute(
                """
                INSERT INTO source_objects
                    (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
                VALUES (?, ?, 'table', ?, ?, ?, '{}', ?, ?)
                """,
                [
                    table_id,
                    src["source_id"],
                    schema_id,
                    table_name,
                    f"demo.cap_tie_schema.{table_name}",
                    now,
                    now,
                ],
            )

        route = QueryRouter(self.metadata, self.engine_service).resolve_tables(
            ["cap_tie_watch", "cap_tie_qoe"]
        )

        self.assertEqual(route.engine_id, trino["engine_id"])
        self.assertEqual(route.capability_profile.performance_class, "distributed")
        self.assertGreater(route.capability_score, 0)

    def test_resolve_tables_uses_semantic_intent_to_override_priority(self) -> None:
        from datetime import datetime
        from uuid import uuid4

        src = self.source_service.register_source("duckdb", "Semantic Route Src", {})
        duck = self.engine_service.register_engine(
            "duckdb",
            "Semantic Route Duck",
            {"path": "/tmp/semantic_route.duckdb"},
            deployment_capabilities={
                "supported_step_types": ["sample_rows", "profile_table"],
            },
        )
        trino = self.engine_service.register_engine(
            "trino",
            "Semantic Route Trino",
            {
                "host": "localhost",
                "port": 8080,
                "user": "test",
                "catalog": "hive",
                "schema": "default",
            },
        )

        self.binding_service.create_binding(src["source_id"], duck["engine_id"], priority=9)
        self.binding_service.create_binding(src["source_id"], trino["engine_id"], priority=7)

        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'semantic_route_schema', 'demo.semantic_route_schema', '{}', ?, ?)
            """,
            [schema_id, src["source_id"], now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'semantic_route_table', 'demo.semantic_route_schema.semantic_route_table', '{}', ?, ?)
            """,
            [table_id, src["source_id"], schema_id, now, now],
        )

        route = QueryRouter(self.metadata, self.engine_service).resolve_tables(
            ["semantic_route_table"],
            routing_intent=RoutingIntent(
                step_type="metric_query",
                metric_names=["watch_time"],
                requested_dimensions=["platform", "app_version", "network_type"],
                compatible_dimensions=["platform", "app_version", "network_type"],
                policy_hints=["aggregate_only"],
            ),
        )

        self.assertEqual(route.engine_id, trino["engine_id"])
        self.assertEqual(route.routing_detail["strategy"], "semantic_intent_and_capability")
        self.assertIsNotNone(route.selection_reason)
        duck_candidate = next(
            candidate
            for candidate in route.routing_detail["candidates"]
            if candidate["engine_id"] == duck["engine_id"]
        )
        self.assertFalse(duck_candidate["step_type_supported"])
        self.assertEqual(duck_candidate["missing_policy_support"], [])


class BindingAPITests(unittest.TestCase):
    """Integration tests for binding and routing API endpoints via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_binding_api.duckdb"
        cls.metadata = SQLiteMetadataStore(Path(cls.temp_dir.name) / "test_binding_api.meta.sqlite")
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(
                cls.db_path,
                metadata_store=cls.metadata,
                config_path=Path(cls.temp_dir.name) / "none.yaml",
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _register_source_and_engine(self) -> tuple[str, str]:
        """Helper: register a unique source and engine, return their IDs."""
        from uuid import uuid4

        suffix = uuid4().hex[:6]

        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), f"API Src {suffix}"),
        )
        source_id = resp.json()["source_id"]

        resp = self.client.post(
            "/engines",
            json=build_duckdb_engine_payload(f"/tmp/api_{suffix}.duckdb", f"API Eng {suffix}"),
        )
        engine_id = resp.json()["engine_id"]

        return source_id, engine_id

    def test_post_and_get_binding(self) -> None:
        source_id, engine_id = self._register_source_and_engine()

        resp = self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 5,
            },
        )
        self.assertEqual(resp.status_code, 200)
        binding = resp.json()
        self.assertTrue(binding["binding_id"].startswith("bind_"))
        self.assertEqual(binding["priority"], 5)

        # GET by ID
        resp = self.client.get(f"/bindings/{binding['binding_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["binding_id"], binding["binding_id"])

    def test_list_bindings_filter(self) -> None:
        source_id, engine_id = self._register_source_and_engine()
        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
            },
        )

        resp = self.client.get(f"/bindings?source_id={source_id}")
        self.assertEqual(resp.status_code, 200)
        bindings = resp.json()
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["source_id"], source_id)

    def test_delete_binding(self) -> None:
        source_id, engine_id = self._register_source_and_engine()
        resp = self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
            },
        )
        binding_id = resp.json()["binding_id"]

        resp = self.client.delete(f"/bindings/{binding_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")

        # Verify gone
        resp = self.client.get(f"/bindings/{binding_id}")
        self.assertEqual(resp.status_code, 404)

    def test_source_engines_endpoint(self) -> None:
        source_id, engine_id = self._register_source_and_engine()
        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 10,
            },
        )

        resp = self.client.get(f"/sources/{source_id}/engines")
        self.assertEqual(resp.status_code, 200)
        engines = resp.json()
        self.assertEqual(len(engines), 1)
        self.assertEqual(engines[0]["engine_id"], engine_id)
        self.assertEqual(engines[0]["priority"], 10)

    def test_routing_resolve(self) -> None:
        # Register source, sync it, register engine, bind, then resolve
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Routing Src"),
        )
        source_id = resp.json()["source_id"]

        # Add sync selections before syncing
        self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        # Sync to populate source_objects
        self.client.post(f"/sources/{source_id}/sync")

        resp = self.client.post(
            "/engines",
            json=build_duckdb_engine_payload("/tmp/routing.duckdb", "Routing Eng"),
        )
        engine_id = resp.json()["engine_id"]

        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 5,
            },
        )

        resp = self.client.post(
            "/routing/resolve",
            json={
                "table_names": ["watch_events", "player_qoe"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertTrue(result["resolved"])
        self.assertIsNotNone(result["engine"])
        self.assertEqual(result["engine"]["engine_id"], engine_id)

    def test_routing_resolve_unknown_table(self) -> None:
        resp = self.client.post(
            "/routing/resolve",
            json={
                "table_names": ["completely_unknown_table_xyz"],
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_binding_create_invalid_source(self) -> None:
        resp = self.client.post(
            "/bindings",
            json={
                "source_id": "src_nonexistent",
                "engine_id": "eng_nonexistent",
            },
        )
        self.assertEqual(resp.status_code, 404)

    # ── Namespace API tests ───────────────────────────────────────

    def test_post_binding_with_namespace(self) -> None:
        source_id, engine_id = self._register_source_and_engine()

        resp = self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 5,
                "namespace": {"catalog": "hive"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        binding = resp.json()
        self.assertEqual(binding["namespace"], {"catalog": "hive"})

        # GET round-trip
        resp = self.client.get(f"/bindings/{binding['binding_id']}")
        self.assertEqual(resp.json()["namespace"], {"catalog": "hive"})

    def test_routing_resolve_qualified_names(self) -> None:
        """POST /routing/resolve returns qualified_names map."""
        from datetime import datetime
        from uuid import uuid4

        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "QN Routing Src"),
        )
        source_id = resp.json()["source_id"]

        resp = self.client.post(
            "/engines",
            json=build_duckdb_engine_payload("/tmp/qn_routing.duckdb", "QN Routing Eng"),
        )
        engine_id = resp.json()["engine_id"]

        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 5,
                "namespace": {"catalog": "hive"},
            },
        )

        # Insert a unique table (with parent schema) so resolution is unambiguous
        metadata_store = self.client.app.state.metadata_store
        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'qn_schema', 'demo.qn_schema', '{}', ?, ?)""",
            [schema_id, source_id, now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'qn_unique_table', 'demo.qn_schema.qn_unique_table', '{}', ?, ?)""",
            [table_id, source_id, schema_id, now, now],
        )

        resp = self.client.post(
            "/routing/resolve",
            json={
                "table_names": ["qn_unique_table"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertIn("qualified_names", result)
        self.assertIn("qn_unique_table", result["qualified_names"])
        self.assertEqual(
            result["qualified_names"]["qn_unique_table"], "hive.qn_schema.qn_unique_table"
        )

    def test_routing_resolve_accepts_full_fqn(self) -> None:
        from datetime import datetime
        from uuid import uuid4

        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "FQN Routing Src"),
        )
        source_id = resp.json()["source_id"]

        resp = self.client.post(
            "/engines",
            json=build_duckdb_engine_payload("/tmp/fqn_routing.duckdb", "FQN Routing Eng"),
        )
        engine_id = resp.json()["engine_id"]

        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "priority": 5,
                "namespace": {"catalog": "hive"},
            },
        )

        metadata_store = self.client.app.state.metadata_store
        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'fqn_schema', 'duckdb.fqn_schema', '{}', ?, ?)""",
            [schema_id, source_id, now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        table_fqn = "duckdb.fqn_schema.fqn_unique_table"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'fqn_unique_table', ?, '{}', ?, ?)""",
            [table_id, source_id, schema_id, table_fqn, now, now],
        )

        resp = self.client.post(
            "/routing/resolve",
            json={"table_names": [table_fqn]},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["engine"]["engine_id"], engine_id)
        self.assertEqual(
            result["qualified_names"][table_fqn],
            "hive.fqn_schema.fqn_unique_table",
        )

    def test_routing_resolve_short_name_reports_ambiguity(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Ambiguous Routing Src A"),
        )
        source_a_id = resp.json()["source_id"]
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Ambiguous Routing Src B"),
        )
        source_b_id = resp.json()["source_id"]

        for source_id, engine_path in (
            (source_a_id, "/tmp/ambiguous_routing_a.duckdb"),
            (source_b_id, "/tmp/ambiguous_routing_b.duckdb"),
        ):
            resp = self.client.post(
                "/engines",
                json=build_duckdb_engine_payload(engine_path, f"Engine {source_id}"),
            )
            engine_id = resp.json()["engine_id"]
            self.client.post(
                "/bindings",
                json={
                    "source_id": source_id,
                    "engine_id": engine_id,
                    "priority": 5,
                },
            )

        metadata_store = self.client.app.state.metadata_store
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC).isoformat()
        for source_id, schema_name in (
            (source_a_id, "schema_a"),
            (source_b_id, "schema_b"),
        ):
            schema_id = f"obj_{uuid4().hex[:12]}"
            metadata_store.execute(
                """INSERT INTO source_objects
                    (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
                VALUES (?, ?, 'schema', ?, ?, '{}', ?, ?)""",
                [schema_id, source_id, schema_name, f"duckdb.{schema_name}", now, now],
            )
            table_id = f"obj_{uuid4().hex[:12]}"
            metadata_store.execute(
                """INSERT INTO source_objects
                    (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
                VALUES (?, ?, 'table', ?, 'shared_table', ?, '{}', ?, ?)""",
                [table_id, source_id, schema_id, f"duckdb.{schema_name}.shared_table", now, now],
            )

        resp = self.client.post(
            "/routing/resolve",
            json={"table_names": ["shared_table"]},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Ambiguous table name", resp.json()["detail"])

    def test_routing_resolve_accepts_semantic_intent(self) -> None:
        from datetime import datetime
        from uuid import uuid4

        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Semantic API Routing Src"),
        )
        source_id = resp.json()["source_id"]

        resp = self.client.post(
            "/engines",
            json={
                **build_duckdb_engine_payload("/tmp/semantic_api_duck.duckdb", "Semantic API Duck"),
                "deployment_capabilities": {
                    "supported_step_types": ["sample_rows", "profile_table"],
                },
            },
        )
        duck_engine_id = resp.json()["engine_id"]

        resp = self.client.post(
            "/engines",
            json=build_trino_engine_payload(
                "Semantic API Trino",
                {
                    "host": "localhost",
                    "port": 8080,
                    "user": "test",
                    "catalog": "hive",
                    "schema": "default",
                },
            ),
        )
        trino_engine_id = resp.json()["engine_id"]

        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": duck_engine_id,
                "priority": 9,
            },
        )
        self.client.post(
            "/bindings",
            json={
                "source_id": source_id,
                "engine_id": trino_engine_id,
                "priority": 7,
            },
        )

        metadata_store = self.client.app.state.metadata_store
        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'semantic_api_schema', 'demo.semantic_api_schema', '{}', ?, ?)""",
            [schema_id, source_id, now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        metadata_store.execute(
            """INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'semantic_api_table', 'demo.semantic_api_schema.semantic_api_table', '{}', ?, ?)""",
            [table_id, source_id, schema_id, now, now],
        )

        resp = self.client.post(
            "/routing/resolve",
            json={
                "table_names": ["semantic_api_table"],
                "routing_intent": {
                    "step_type": "metric_query",
                    "metric_names": ["watch_time"],
                    "requested_dimensions": ["platform", "app_version", "network_type"],
                    "compatible_dimensions": ["platform", "app_version", "network_type"],
                    "policy_hints": ["aggregate_only"],
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["engine"]["engine_id"], trino_engine_id)
        self.assertEqual(result["routing_detail"]["strategy"], "semantic_intent_and_capability")
        self.assertIsNotNone(result["selection_reason"])
        self.assertEqual(result["capability_profile"]["engine_type"], "trino")


class BindingConfigTests(unittest.TestCase):
    """Tests for config loading and startup auto-registration of bindings."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.class_tmp = tempfile.TemporaryDirectory()
        duck_path = Path(cls.class_tmp.name) / "shared.duckdb"
        get_seeded_duckdb_path(duck_path)
        cls.shared_analytics = DuckDBAnalyticsEngine(duck_path)
        cls.shared_analytics.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.class_tmp.cleanup()

    def test_load_config_with_bindings(self) -> None:
        from app.config import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "sources": [
                        {
                            "name": "Cfg Src",
                            "type": "duckdb",
                            "authority": {
                                "catalog_system": "duckdb",
                                "connection": {
                                    "path": str(Path(self.class_tmp.name) / "shared.duckdb")
                                },
                                "synthetic_catalog": "main",
                            },
                        }
                    ],
                    "engines": [
                        {
                            "name": "Cfg Eng",
                            "type": "duckdb",
                            "connection": {"path": "/tmp/cfg.duckdb"},
                        }
                    ],
                    "bindings": [{"source": "Cfg Src", "engine": "Cfg Eng", "priority": 15}],
                },
                f,
            )
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertEqual(len(cfg.bindings), 1)
        self.assertEqual(cfg.bindings[0].source, "Cfg Src")
        self.assertEqual(cfg.bindings[0].engine, "Cfg Eng")
        self.assertEqual(cfg.bindings[0].priority, 15)

    def test_startup_registers_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.yaml"
            config_path.write_text(
                yaml.dump(
                    {
                        "sources": [
                            {
                                "name": "Startup Src",
                                "type": "duckdb",
                                "authority": {
                                    "catalog_system": "duckdb",
                                    "connection": {
                                        "path": str(Path(self.class_tmp.name) / "shared.duckdb")
                                    },
                                    "synthetic_catalog": "main",
                                },
                            }
                        ],
                        "engines": [
                            {
                                "name": "Startup Eng",
                                "type": "duckdb",
                                "connection": {"path": str(Path(tmpdir) / "startup.duckdb")},
                            }
                        ],
                        "bindings": [
                            {"source": "Startup Src", "engine": "Startup Eng", "priority": 20}
                        ],
                    }
                )
            )

            meta_path = Path(tmpdir) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            client = TestClient(
                create_app(
                    metadata_store=metadata,
                    analytics_engine=self.shared_analytics,
                    config_path=str(config_path),
                )
            )

            try:
                resp = client.get("/bindings")
                self.assertEqual(resp.status_code, 200)
                bindings = resp.json()
                self.assertGreaterEqual(len(bindings), 1)
                # Find the binding for our source/engine
                matching = [b for b in bindings if b["priority"] == 20]
                self.assertEqual(len(matching), 1)
            finally:
                client.close()

    def test_load_config_with_namespace(self) -> None:
        from app.config import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "sources": [
                        {
                            "name": "NS Cfg Src",
                            "type": "duckdb",
                            "authority": {
                                "catalog_system": "duckdb",
                                "connection": {
                                    "path": str(Path(self.class_tmp.name) / "shared.duckdb")
                                },
                                "synthetic_catalog": "main",
                            },
                        }
                    ],
                    "engines": [
                        {
                            "name": "NS Cfg Eng",
                            "type": "duckdb",
                            "connection": {"path": "/tmp/ns_cfg.duckdb"},
                        }
                    ],
                    "bindings": [
                        {
                            "source": "NS Cfg Src",
                            "engine": "NS Cfg Eng",
                            "priority": 10,
                            "namespace": {"catalog": "hive"},
                        }
                    ],
                },
                f,
            )
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertEqual(len(cfg.bindings), 1)
        self.assertEqual(cfg.bindings[0].namespace, {"catalog": "hive"})


if __name__ == "__main__":
    unittest.main()
