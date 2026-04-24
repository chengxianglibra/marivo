from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.engines import EngineService
from app.mappings import MappingService
from app.routing import QueryRouter, RoutingResolutionError
from app.semantic_runtime.errors import SemanticRuntimeNotReadyError
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)


def _duckdb_source_payload(path: str, display_name: str) -> dict[str, object]:
    return {
        "source_type": "duckdb",
        "display_name": display_name,
        "authority": {
            "catalog_system": "duckdb",
            "connection": {"path": path},
            "synthetic_catalog": "main",
        },
        "sync": {"mode": "none"},
        "policy": {"allow_live_browse": True, "allow_sync": False},
    }


def _trino_engine_payload(display_name: str) -> dict[str, object]:
    return {
        "engine_type": "trino",
        "display_name": display_name,
        "connection": {
            "host": "localhost",
            "catalog": "iceberg_prod",
            "schema": "analytics",
        },
    }


class MappingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.meta_path = Path(self.temp_dir.name) / "test-mappings.meta.sqlite"
        self.db_path = Path(self.temp_dir.name) / "test-mappings.duckdb"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.metadata.initialize()
        self.source_service = SourceService(self.metadata)
        self.engine_service = EngineService(self.metadata)
        self.mapping_service = MappingService(self.metadata)
        self.router = QueryRouter(self.metadata, self.engine_service)
        self.app = create_app(
            self.db_path,
            metadata_store=self.metadata,
            analytics_engine=DuckDBAnalyticsEngine(str(self.db_path)),
        )
        self.service = cast("Any", self.app.state.service)

        self.source = self.source_service.register_source(
            "duckdb",
            "DuckDB Source",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": str(self.db_path)},
                "synthetic_catalog": "main",
            },
            sync={"mode": "none"},
            policy={"allow_live_browse": True, "allow_sync": False},
        )
        self.engine = self.engine_service.register_engine(
            "duckdb",
            "DuckDB Engine",
            connection={"path": str(self.db_path)},
        )
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_watch_events",
                self.source["source_id"],
                "watch_events",
                "main.analytics.watch_events",
                json.dumps({"catalog": "main", "schema": "analytics", "table": "watch_events"}),
                now,
                now,
                now,
            ],
        )

    def tearDown(self) -> None:
        analytics = getattr(self.app.state, "analytics", None)
        if analytics is not None:
            analytics.close()
        self.temp_dir.cleanup()

    def test_create_mapping_and_route_table(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["readiness_status"], "ready")
        route = self.router.resolve_tables(["watch_events"])
        self.assertEqual(route.engine_id, self.engine["engine_id"])
        self.assertEqual(
            route.qualified_names["watch_events"], "duckdb_runtime.analytics.watch_events"
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["authority_locator"],
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["mapping_id"],
            mapping["mapping_id"],
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["authority_catalog"],
            "main",
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["execution_catalog"],
            "duckdb_runtime",
        )
        self.assertFalse(
            route.routing_detail["execution_locators"]["watch_events"]["default_schema_applied"]
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["readiness_blockers"], []
        )
        self.assertEqual(route.routing_detail["selected_mapping_ids"], [mapping["mapping_id"]])

    def test_metric_execution_context_uses_mapping_projection(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )
        ensure_published_typed_metric(
            self.metadata,
            metric_name="mapping_ready_metric",
            display_name="Mapping Ready Metric",
            measure_type="sum",
            dimensions=["event_date"],
        )
        ensure_published_typed_metric_binding(
            self.metadata,
            metric_name="mapping_ready_metric",
            carrier_locator="main.analytics.watch_events",
            source_object_ref="obj_watch_events",
            metric_input_target_keys=["measure"],
        )

        context = self.service._resolve_metric_execution_context("metric.mapping_ready_metric")

        self.assertEqual(context.table_name, "duckdb_runtime.analytics.watch_events")
        self.assertEqual(context.mapping_id, mapping["mapping_id"])
        self.assertEqual(
            context.authority_locator,
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(
            context.execution_locator,
            {
                "catalog": "duckdb_runtime",
                "schema": "analytics",
                "table": "watch_events",
                "mapping_id": mapping["mapping_id"],
                "authority_catalog": "main",
                "execution_catalog": "duckdb_runtime",
                "default_schema_applied": False,
                "readiness_blockers": [],
                "authority_locator": {
                    "catalog": "main",
                    "schema": "analytics",
                    "table": "watch_events",
                },
            },
        )

    def test_metric_execution_preflight_reports_mapping_route_blocker(self) -> None:
        ensure_published_typed_metric(
            self.metadata,
            metric_name="mapping_missing_metric",
            display_name="Mapping Missing Metric",
            measure_type="sum",
            dimensions=["event_date"],
        )
        ensure_published_typed_metric_binding(
            self.metadata,
            metric_name="mapping_missing_metric",
            carrier_locator="main.analytics.watch_events",
            source_object_ref="obj_watch_events",
            metric_input_target_keys=["measure"],
        )

        with self.assertRaises(SemanticRuntimeNotReadyError) as error:
            self.service._resolve_metric_execution_context("metric.mapping_missing_metric")

        blocker = error.exception.blocking_requirements[0]
        self.assertEqual(blocker["code"], "METRIC_EXECUTION_BINDING_UNRESOLVED")
        candidate = blocker["details"]["candidate_bindings"][0]
        self.assertIn(candidate["failure_stage"], (None, "mapping_route_preflight"))
        self.assertEqual(
            candidate["authority_locator"],
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )
        self.assertIsNone(candidate["mapping_id"])
        routing_detail = candidate.get("routing_detail") or {}
        if routing_detail:
            self.assertEqual(
                routing_detail["resolution_status"],
                "no_active_mappings",
            )
        blockers = candidate.get("readiness_blockers") or []
        if blockers:
            self.assertEqual(blockers[0]["failure_code"], "mapping_missing")

    def test_routing_accepts_authority_locator_name_variants(self) -> None:
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        full_route = self.router.resolve_tables(["main.analytics.watch_events"])
        schema_route = self.router.resolve_tables(["analytics.watch_events"])
        short_route = self.router.resolve_tables(["watch_events"])

        self.assertEqual(
            full_route.qualified_names["main.analytics.watch_events"],
            "duckdb_runtime.analytics.watch_events",
        )
        self.assertEqual(
            schema_route.qualified_names["analytics.watch_events"],
            "duckdb_runtime.analytics.watch_events",
        )
        self.assertEqual(
            short_route.qualified_names["watch_events"],
            "duckdb_runtime.analytics.watch_events",
        )

    def test_routing_reports_schema_table_ambiguity_across_catalogs(self) -> None:
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_watch_events_other",
                self.source["source_id"],
                "watch_events",
                "other.analytics.watch_events",
                json.dumps({"catalog": "other", "schema": "analytics", "table": "watch_events"}),
                now,
                now,
                now,
            ],
        )
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                },
                {
                    "authority_catalog": "other",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                },
            ],
        )

        with self.assertRaisesRegex(ValueError, "Ambiguous table name"):
            self.router.resolve_tables(["analytics.watch_events"])

        full_route = self.router.resolve_tables(["main.analytics.watch_events"])
        self.assertEqual(
            full_route.qualified_names["main.analytics.watch_events"],
            "duckdb_runtime.analytics.watch_events",
        )

    def test_routing_rejects_full_authority_locator_shared_by_multiple_sources(self) -> None:
        second_source = self.source_service.register_source(
            "duckdb",
            "DuckDB Source 2",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": str(self.db_path)},
                "synthetic_catalog": "main",
            },
            sync={"mode": "none"},
            policy={"allow_live_browse": True, "allow_sync": False},
        )
        second_engine = self.engine_service.register_engine(
            "duckdb",
            "DuckDB Engine 2",
            connection={"path": str(self.db_path)},
        )
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_watch_events_source2",
                second_source["source_id"],
                "watch_events",
                "main.analytics.watch_events",
                json.dumps({"catalog": "main", "schema": "analytics", "table": "watch_events"}),
                now,
                now,
                now,
            ],
        )
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )
        self.mapping_service.create_mapping(
            second_source["source_id"],
            second_engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime_2",
                    "default_schema": None,
                }
            ],
        )

        with self.assertRaisesRegex(ValueError, "matches multiple sources"):
            self.router.resolve_tables(["main.analytics.watch_events"])

    def test_mapping_incomplete_fails_closed(self) -> None:
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "other_catalog",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        with self.assertRaisesRegex(ValueError, "mapping_incomplete"):
            self.router.resolve_tables(["watch_events"])

    def test_duplicate_authority_catalog_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate authority_catalog"):
            self.mapping_service.create_mapping(
                self.source["source_id"],
                self.engine["engine_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": None,
                    },
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_alt",
                        "default_schema": None,
                    },
                ],
            )

    def test_validate_and_readiness_surface_mapping_status(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "other_catalog",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        validation = self.mapping_service.validate_mapping(mapping["mapping_id"])
        self.assertFalse(validation["is_valid"])
        self.assertEqual(validation["readiness_status"], "not_ready")
        self.assertEqual(validation["failure_code"], "mapping_incomplete")

        readiness = self.mapping_service.get_mapping_readiness(mapping["mapping_id"])
        self.assertEqual(
            readiness,
            {
                "mapping_id": mapping["mapping_id"],
                "readiness_status": "not_ready",
                "failure_code": "mapping_incomplete",
            },
        )

    def test_mapping_propagates_source_failure_code(self) -> None:
        self.metadata.execute(
            "UPDATE sources SET authority_json = ? WHERE source_id = ?",
            [
                json.dumps(
                    {
                        "catalog_system": "duckdb",
                        "connection": {},
                        "synthetic_catalog": "main",
                    }
                ),
                self.source["source_id"],
            ],
        )
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        validation = self.mapping_service.validate_mapping(mapping["mapping_id"])
        self.assertFalse(validation["is_valid"])
        self.assertEqual(validation["failure_code"], "source_invalid_connection")

    def test_mapping_propagates_engine_failure_code(self) -> None:
        self.metadata.execute(
            "UPDATE engines SET connection_json = ? WHERE engine_id = ?",
            ["{}", self.engine["engine_id"]],
        )
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        validation = self.mapping_service.validate_mapping(mapping["mapping_id"])
        self.assertFalse(validation["is_valid"])
        self.assertEqual(validation["failure_code"], "engine_invalid_connection")

    def test_mapping_rejects_unknown_authority_catalog_when_source_catalogs_known(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "other_catalog",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["failure_code"], "mapping_incomplete")

    def test_mapping_requires_full_authority_catalog_coverage(self) -> None:
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_orders",
                self.source["source_id"],
                "orders",
                "other.analytics.orders",
                json.dumps({"catalog": "other", "schema": "analytics", "table": "orders"}),
                now,
                now,
                now,
            ],
        )

        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["failure_code"], "mapping_incomplete")

    def test_mapping_without_known_source_catalogs_only_checks_shape(self) -> None:
        source = self.source_service.register_source(
            "duckdb",
            "No Objects Source",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": str(self.db_path)},
                "synthetic_catalog": "main",
            },
            sync={"mode": "none"},
            policy={"allow_live_browse": True, "allow_sync": False},
        )
        mapping = self.mapping_service.create_mapping(
            source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "unknown_catalog",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["readiness_status"], "ready")
        self.assertIsNone(mapping["failure_code"])

    def test_mapping_rejects_duckdb_to_trino_combo(self) -> None:
        with (
            patch("app.registry.source_registry.build_catalog_adapter", return_value=object()),
            patch("app.registry.engine_registry.build_analytics_engine", return_value=object()),
        ):
            trino_engine = self.engine_service.register_engine(
                "trino",
                "Trino Engine",
                connection={"host": "localhost", "catalog": "iceberg_prod", "schema": "analytics"},
            )
            mapping = self.mapping_service.create_mapping(
                self.source["source_id"],
                trino_engine["engine_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "iceberg_prod",
                        "default_schema": None,
                    }
                ],
            )

        self.assertEqual(mapping["failure_code"], "mapping_invalid_type_combo")

    def test_mapping_rejects_trino_to_duckdb_combo(self) -> None:
        with (
            patch("app.registry.source_registry.build_catalog_adapter", return_value=object()),
            patch("app.registry.engine_registry.build_analytics_engine", return_value=object()),
        ):
            trino_source = self.source_service.register_source(
                "trino",
                "Trino Source",
                authority={
                    "catalog_system": "trino",
                    "connection": {
                        "host": "localhost",
                        "catalog": "lakehouse",
                        "schema": "analytics",
                    },
                },
                sync={"mode": "none"},
                policy={"allow_live_browse": True, "allow_sync": False},
            )
            self.metadata.execute(
                """
                INSERT INTO source_objects (
                    object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                    authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
                )
                VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
                """,
                [
                    "obj_trino_watch_events",
                    trino_source["source_id"],
                    "watch_events",
                    "lakehouse.analytics.watch_events",
                    json.dumps(
                        {
                            "catalog": "lakehouse",
                            "schema": "analytics",
                            "table": "watch_events",
                        }
                    ),
                    "2026-04-23T00:00:00+00:00",
                    "2026-04-23T00:00:00+00:00",
                    "2026-04-23T00:00:00+00:00",
                ],
            )
            mapping = self.mapping_service.create_mapping(
                trino_source["source_id"],
                self.engine["engine_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "lakehouse",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": None,
                    }
                ],
            )

        self.assertEqual(mapping["failure_code"], "mapping_invalid_type_combo")

    def test_router_skips_mapping_when_engine_is_not_ready(self) -> None:
        self.metadata.execute(
            "UPDATE engines SET connection_json = ? WHERE engine_id = ?",
            ["{}", self.engine["engine_id"]],
        )
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["readiness_status"], "not_ready")
        self.assertEqual(mapping["failure_code"], "engine_invalid_connection")
        with self.assertRaises(RoutingResolutionError) as error:
            self.router.resolve_tables(["watch_events"])
        self.assertEqual(error.exception.code, "routing_source_unavailable")
        self.assertEqual(
            error.exception.routing_detail["readiness_blockers"][0]["failure_code"],
            "engine_invalid_connection",
        )

    def test_resolve_route_reports_no_active_mapping_as_structured_failure(self) -> None:
        resolution = self.router.resolve_route(["watch_events"])

        self.assertFalse(resolution.resolved)
        assert resolution.failure is not None
        self.assertEqual(resolution.failure.code, "routing_source_unmapped")
        self.assertEqual(
            resolution.failure.routing_detail["readiness_blockers"],
            [
                {
                    "kind": "mapping_missing",
                    "source_id": self.source["source_id"],
                    "failure_code": "mapping_missing",
                    "message": (
                        f"Source '{self.source['source_id']}' has no active execution mappings"
                    ),
                }
            ],
        )

    def test_resolve_route_reports_candidates_when_sources_have_no_common_engine(self) -> None:
        second_source = self.source_service.register_source(
            "duckdb",
            "DuckDB Source 2",
            authority={
                "catalog_system": "duckdb",
                "connection": {"path": str(self.db_path)},
                "synthetic_catalog": "main",
            },
            sync={"mode": "none"},
            policy={"allow_live_browse": True, "allow_sync": False},
        )
        second_engine = self.engine_service.register_engine(
            "duckdb",
            "DuckDB Engine 2",
            connection={"path": str(self.db_path)},
        )
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_orders_source2",
                second_source["source_id"],
                "orders",
                "main.analytics.orders",
                json.dumps({"catalog": "main", "schema": "analytics", "table": "orders"}),
                now,
                now,
                now,
            ],
        )
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )
        self.mapping_service.create_mapping(
            second_source["source_id"],
            second_engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime_2",
                    "default_schema": None,
                }
            ],
        )

        resolution = self.router.resolve_route(["watch_events", "orders"])

        self.assertFalse(resolution.resolved)
        assert resolution.failure is not None
        self.assertEqual(resolution.failure.code, "routing_no_common_engine")
        candidates = resolution.failure.routing_detail["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertFalse(candidates[0]["eligible"])
        self.assertTrue(candidates[0]["missing_sources"])

    def test_get_engine_info_for_source_returns_none_when_only_engine_is_not_ready(self) -> None:
        self.metadata.execute(
            "UPDATE engines SET connection_json = ? WHERE engine_id = ?",
            ["{}", self.engine["engine_id"]],
        )
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertIsNone(self.router.get_engine_info_for_source(self.source["source_id"]))
        with self.assertRaisesRegex(ValueError, "engine_invalid_connection"):
            self.router.resolve_engine_for_source(self.source["source_id"])

    def test_update_and_delete_mapping(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=1,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        updated = self.mapping_service.update_mapping(
            mapping["mapping_id"],
            priority=8,
            status="inactive",
        )
        self.assertEqual(updated["priority"], 8)
        self.assertEqual(updated["status"], "inactive")
        self.assertEqual(updated["readiness_status"], "not_ready")
        self.assertEqual(updated["failure_code"], "mapping_inactive")

        self.mapping_service.delete_mapping(mapping["mapping_id"])
        with self.assertRaisesRegex(KeyError, "Unknown mapping"):
            self.mapping_service.get_mapping(mapping["mapping_id"])

    def test_registry_rejects_blank_catalog_fields(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"catalog_mappings\[\]\.execution_catalog is required"
        ):
            self.mapping_service.create_mapping(
                self.source["source_id"],
                self.engine["engine_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "   ",
                        "default_schema": None,
                    }
                ],
            )

        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )
        with self.assertRaisesRegex(
            ValueError, r"catalog_mappings\[\]\.default_schema must not be blank"
        ):
            self.mapping_service.update_mapping(
                mapping["mapping_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": "   ",
                    }
                ],
            )


class MappingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "mapping-api.duckdb"
        self.meta_path = Path(self.temp_dir.name) / "mapping-api.meta.sqlite"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.analytics = DuckDBAnalyticsEngine(self.db_path)
        app = create_app(
            db_path=self.db_path,
            metadata_store=self.metadata,
            analytics_engine=self.analytics,
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _insert_source_table(
        self,
        source_id: str,
        *,
        object_id: str,
        native_name: str,
        catalog: str = "main",
        schema: str = "analytics",
    ) -> None:
        now = "2026-04-23T00:00:00+00:00"
        locator = {"catalog": catalog, "schema": schema, "table": native_name}
        app_state = cast("Any", self.client.app).state
        app_state.metadata_store.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                object_id,
                source_id,
                native_name,
                f"{catalog}.{schema}.{native_name}",
                json.dumps(locator),
                now,
                now,
                now,
            ],
        )

    def test_create_and_get_mapping(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "API Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "API Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "priority": 5,
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": None,
                    }
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        mapping_id = resp.json()["mapping_id"]

        detail = self.client.get(f"/mappings/{mapping_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["mapping_id"], mapping_id)
        self.assertEqual(detail.json()["catalog_mappings"][0]["authority_catalog"], "main")
        self.assertEqual(detail.json()["readiness_status"], "ready")
        self.assertIsNone(detail.json()["failure_code"])
        self.assertIn("created_at", detail.json())
        self.assertIn("updated_at", detail.json())

    def test_list_update_and_delete_mapping_use_stable_response_shapes(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "List Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "List Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        create_resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    }
                ],
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        mapping_id = create_resp.json()["mapping_id"]

        list_resp = self.client.get("/mappings")
        self.assertEqual(list_resp.status_code, 200)
        self.assertIsInstance(list_resp.json(), list)
        listed = next(item for item in list_resp.json() if item["mapping_id"] == mapping_id)
        self.assertEqual(listed["source_id"], source_resp.json()["source_id"])
        self.assertEqual(listed["engine_id"], engine_resp.json()["engine_id"])
        self.assertEqual(listed["catalog_mappings"][0]["execution_catalog"], "duckdb_runtime")

        update_resp = self.client.put(
            f"/mappings/{mapping_id}",
            json={"priority": 7, "status": "inactive"},
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["priority"], 7)
        self.assertEqual(update_resp.json()["status"], "inactive")
        self.assertEqual(update_resp.json()["failure_code"], "mapping_inactive")
        self.assertEqual(update_resp.json()["readiness_status"], "not_ready")

        delete_resp = self.client.delete(f"/mappings/{mapping_id}")
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(
            delete_resp.json(),
            {"status": "deleted", "mapping_id": mapping_id},
        )

    def test_mapping_detail_exposes_new_failure_code(self) -> None:
        with (
            patch("app.registry.source_registry.build_catalog_adapter", return_value=object()),
            patch("app.registry.engine_registry.build_analytics_engine", return_value=object()),
        ):
            source_resp = self.client.post(
                "/sources",
                json=_duckdb_source_payload(str(self.db_path), "API DuckDB Source"),
            )
            engine_resp = self.client.post(
                "/engines",
                json=_trino_engine_payload("API Trino Engine"),
            )
            mapping_resp = self.client.post(
                "/mappings",
                json={
                    "source_id": source_resp.json()["source_id"],
                    "engine_id": engine_resp.json()["engine_id"],
                    "catalog_mappings": [
                        {
                            "authority_catalog": "main",
                            "execution_catalog": "iceberg_prod",
                        }
                    ],
                },
            )

        self.assertEqual(mapping_resp.status_code, 200)
        detail = self.client.get(f"/mappings/{mapping_resp.json()['mapping_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["failure_code"], "mapping_invalid_type_combo")
        self.assertEqual(detail.json()["readiness_status"], "not_ready")

    def test_routing_resolve_returns_structured_success_payload(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Routing Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Routing Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        self._insert_source_table(
            source_resp.json()["source_id"],
            object_id="obj_watch_events_api",
            native_name="watch_events",
        )
        mapping_resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    }
                ],
            },
        )
        self.assertEqual(mapping_resp.status_code, 200)

        response = self.client.post("/routing/resolve", json={"table_names": ["watch_events"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["engine"]["engine_id"], engine_resp.json()["engine_id"])
        self.assertEqual(
            payload["qualified_names"],
            {"watch_events": "duckdb_runtime.analytics.watch_events"},
        )
        self.assertEqual(
            payload["routing_detail"]["selected_mapping_ids"],
            [mapping_resp.json()["mapping_id"]],
        )
        self.assertEqual(
            payload["routing_detail"]["execution_locators"]["watch_events"]["mapping_id"],
            mapping_resp.json()["mapping_id"],
        )

    def test_routing_resolve_returns_structured_failure_payload(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Unmapped Routing Source"),
        )
        self._insert_source_table(
            source_resp.json()["source_id"],
            object_id="obj_watch_events_unmapped",
            native_name="watch_events",
        )

        response = self.client.post("/routing/resolve", json={"table_names": ["watch_events"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["failure_code"], "routing_source_unmapped")
        self.assertIsNone(payload["engine"])
        self.assertEqual(payload["qualified_names"], {})
        self.assertEqual(payload["routing_detail"]["resolution_status"], "no_active_mappings")
        self.assertEqual(
            payload["routing_detail"]["readiness_blockers"][0]["failure_code"],
            "mapping_missing",
        )

    def test_routing_resolve_failure_payload_distinguishes_ambiguous_lookup(self) -> None:
        source_resp_1 = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Ambiguous Source"),
        )
        source_resp_2 = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Ambiguous Source 2"),
        )
        self._insert_source_table(
            source_resp_1.json()["source_id"],
            object_id="obj_watch_events_ambiguous_1",
            native_name="watch_events",
        )
        self._insert_source_table(
            source_resp_2.json()["source_id"],
            object_id="obj_watch_events_ambiguous_2",
            native_name="watch_events",
            catalog="other_catalog",
        )

        response = self.client.post(
            "/routing/resolve", json={"table_names": ["analytics.watch_events"]}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["failure_code"], "routing_table_ambiguous")

    def test_routing_openapi_uses_explicit_response_model(self) -> None:
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("RouteResolveResponse", payload["components"]["schemas"])
        route_post = payload["paths"]["/routing/resolve"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(route_post["$ref"], "#/components/schemas/RouteResolveResponse")

    def test_bindings_route_removed(self) -> None:
        self.assertEqual(self.client.get("/bindings").status_code, 404)
        openapi_response = self.client.get("/openapi.json")
        self.assertEqual(openapi_response.status_code, 200)
        self.assertNotIn("/bindings", openapi_response.json()["paths"])

    def test_create_mapping_rejects_duplicate_authority_catalog_in_request(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Duplicate Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Duplicate Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    },
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_alt",
                    },
                ],
            },
        )

        self.assertEqual(resp.status_code, 422)
        self.assertIn("duplicate authority_catalog", resp.text)

    def test_update_mapping_rejects_duplicate_authority_catalog_in_request(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "Update Duplicate Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Update Duplicate Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        create_resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    }
                ],
            },
        )
        self.assertEqual(create_resp.status_code, 200)

        update_resp = self.client.put(
            f"/mappings/{create_resp.json()['mapping_id']}",
            json={
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    },
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_alt",
                    },
                ],
            },
        )

        self.assertEqual(update_resp.status_code, 422)
        self.assertIn("duplicate authority_catalog", update_resp.text)


if __name__ == "__main__":
    unittest.main()
