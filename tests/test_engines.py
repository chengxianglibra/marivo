from __future__ import annotations

import json
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.engines import EngineService, _build_analytics_engine
from app.execution.capabilities import build_engine_capability_profile
from app.main import create_app
from app.observability import JSONFormatter
from app.session import SessionManager
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class _FakeAnalyticsEngine:
    def __init__(self) -> None:
        self.queries: list[tuple[str, list[object] | None]] = []

    def initialize(self) -> None:
        return None

    def query_rows(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        self.queries.append((sql, params))
        return []

    def table_exists(self, table_name: str) -> bool:
        return True

    def table_row_count(self, table_name: str) -> int:
        return 0


class EngineServiceTests(unittest.TestCase):
    """Unit tests for EngineService using SQLiteMetadataStore directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "test_engines.meta.sqlite"
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.service = EngineService(cls.metadata)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_register_and_list_engines(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Test Trino",
            connection={
                "host": "localhost",
                "port": 8080,
                "user": "test",
                "catalog": "hive",
                "schema": "default",
            },
        )
        self.assertTrue(engine["engine_id"].startswith("eng_"))
        self.assertEqual(engine["engine_type"], "trino")
        self.assertEqual(engine["display_name"], "Test Trino")
        self.assertEqual(engine["auth"], {"mode": "none"})
        self.assertEqual(engine["status"], "active")
        self.assertEqual(engine["readiness_status"], "ready")
        self.assertIsNone(engine["failure_code"])

        engines = self.service.list_engines()
        self.assertTrue(any(e["engine_id"] == engine["engine_id"] for e in engines))

    def test_get_engine(self) -> None:
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Get Test Engine",
            connection={"path": "/tmp/test.duckdb"},
        )
        fetched = self.service.get_engine(engine["engine_id"])
        self.assertEqual(fetched["engine_id"], engine["engine_id"])
        self.assertEqual(fetched["connection"]["path"], "/tmp/test.duckdb")
        self.assertEqual(fetched["auth"], {"mode": "none"})
        self.assertEqual(fetched["default_namespace"], {"catalog": None, "schema": None})
        self.assertEqual(fetched["intrinsic_capabilities"]["performance_class"], "embedded")
        self.assertEqual(fetched["readiness_status"], "ready")
        self.assertIsNone(fetched["failure_code"])

    def test_register_engine_persists_auth_contract(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Auth Trino",
            connection={"host": "localhost"},
            auth={
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "marivo",
            },
        )
        self.assertEqual(
            engine["auth"],
            {
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "marivo",
            },
        )

    def test_register_engine_trims_fallback_username(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Trimmed Auth Trino",
            connection={"host": "localhost"},
            auth={
                "mode": "username_only",
                "username_source": "fixed",
                "fallback_username": " marivo ",
            },
        )

        self.assertEqual(
            engine["auth"],
            {
                "mode": "username_only",
                "username_source": "fixed",
                "fallback_username": "marivo",
            },
        )

    def test_register_engine_rejects_mode_none_with_extra_auth_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "engine_auth_invalid"):
            self.service.register_engine(
                engine_type="trino",
                display_name="Invalid None Auth Trino",
                connection={"host": "localhost"},
                auth={"mode": "none", "fallback_username": "marivo"},
            )

    def test_register_engine_rejects_fixed_username_without_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "engine_auth_invalid"):
            self.service.register_engine(
                engine_type="trino",
                display_name="Invalid Fixed Auth Trino",
                connection={"host": "localhost"},
                auth={"mode": "username_only", "username_source": "fixed"},
            )

    def test_register_engine_rejects_duckdb_username_only_auth(self) -> None:
        with self.assertRaisesRegex(ValueError, "engine_auth_unsupported"):
            self.service.register_engine(
                engine_type="duckdb",
                display_name="Invalid DuckDB Auth",
                connection={"path": "/tmp/test.duckdb"},
                auth={"mode": "username_only", "username_source": "session_user"},
            )

    def test_get_capability_profile_merges_defaults_and_overrides(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Capability Trino",
            connection={"host": "localhost"},
            deployment_capabilities={"min_staleness_minutes": 15},
        )

        profile = self.service.get_capability_profile(engine["engine_id"])

        self.assertEqual(profile.engine_type, "trino")
        self.assertEqual(profile.performance_class, "distributed")
        self.assertEqual(profile.min_staleness_minutes, 15)

    def test_register_engine_without_deployment_overrides_preserves_default_profile(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Default Capability Trino",
            connection={"host": "localhost"},
        )

        profile = self.service.get_capability_profile(engine["engine_id"])

        self.assertEqual(engine["deployment_capabilities"], {})
        self.assertEqual(
            profile.supported_step_types,
            ("sample_rows", "profile_table", "metric_query"),
        )
        self.assertEqual(profile.min_staleness_minutes, 5)

    def test_get_engine_404(self) -> None:
        with self.assertRaises(KeyError):
            self.service.get_engine("eng_nonexistent")

    def test_ensure_engine_idempotent(self) -> None:
        e1 = self.service.ensure_engine(
            engine_type="duckdb",
            display_name="Idempotent Engine",
            connection={"path": "/tmp/idem.duckdb"},
        )
        e2 = self.service.ensure_engine(
            engine_type="duckdb",
            display_name="Idempotent Engine",
            connection={"path": "/tmp/idem.duckdb"},
        )
        self.assertEqual(e1["engine_id"], e2["engine_id"])

    def test_register_engine_rejects_unsupported_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported engine type"):
            self.service.register_engine(
                engine_type="spark",
                display_name="Unsupported Engine",
                connection={},
            )

    def test_build_duckdb_engine(self) -> None:
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Build Test DuckDB",
            connection={"path": str(Path(self.temp_dir.name) / "build_test.duckdb")},
        )
        analytics = self.service.build_analytics_engine(engine["engine_id"])
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        self.assertIsInstance(analytics, DuckDBAnalyticsEngine)

    def test_build_duckdb_engine_ignores_session_execution_identity(self) -> None:
        duckdb_path = Path(self.temp_dir.name) / "build_test_ignore_session.duckdb"
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Ignore Session DuckDB",
            connection={"path": str(duckdb_path), "user": "legacy_user"},
        )
        session = SessionManager(self.metadata).create_session(
            "DuckDB ignores session execution identity",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )
        analytics = self.service.build_analytics_engine(
            engine["engine_id"],
            session_id=session["session_id"],
        )

        self.assertEqual(
            resolved_connection,
            {"path": str(duckdb_path), "user": "legacy_user"},
        )
        self.assertIsInstance(analytics, DuckDBAnalyticsEngine)

    def test_build_trino_engine_uses_session_user_for_username_only_auth(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Session Auth Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={"mode": "username_only", "username_source": "session_user"},
        )
        session = SessionManager(self.metadata).create_session(
            "Route with session user",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        analytics = self.service.build_analytics_engine(
            engine["engine_id"],
            session_id=session["session_id"],
        )

        self.assertEqual(analytics.user, "alice")

    def test_build_trino_engine_passes_session_user_to_builder_connection(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Builder Session Auth Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "svc_marivo",
            },
        )
        session_manager = SessionManager(self.metadata)
        alice = session_manager.create_session(
            "Builder route with alice",
            {},
            {},
            {},
            {"session_user": "alice"},
        )
        bob = session_manager.create_session(
            "Builder route with bob",
            {},
            {},
            {},
            {"session_user": "bob"},
        )

        with patch(
            "app.registry.engine_registry.build_analytics_engine",
            return_value=_FakeAnalyticsEngine(),
        ) as mock_builder:
            self.service.build_analytics_engine(engine["engine_id"], session_id=alice["session_id"])
            self.service.build_analytics_engine(engine["engine_id"], session_id=bob["session_id"])

        runtime_calls = [
            call.args
            for call in mock_builder.call_args_list
            if call.args[1].get("user") in {"alice", "bob"}
        ]
        self.assertEqual(len(runtime_calls), 2)
        alice_engine_type, alice_connection = runtime_calls[0]
        bob_engine_type, bob_connection = runtime_calls[1]
        self.assertEqual(alice_engine_type, "trino")
        self.assertEqual(bob_engine_type, "trino")
        self.assertEqual(alice_connection["user"], "alice")
        self.assertEqual(bob_connection["user"], "bob")
        self.assertEqual(alice_connection["host"], "localhost")
        self.assertEqual(bob_connection["host"], "localhost")

    def test_build_trino_engine_prefers_session_user_over_fallback_username(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Session Auth Trino With Fallback",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "svc_marivo",
            },
        )
        session = SessionManager(self.metadata).create_session(
            "Route with session user preferred over fallback",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )

        self.assertEqual(resolved_connection["user"], "alice")

    def test_build_trino_engine_uses_fixed_fallback_username(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Fixed Auth Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "fixed",
                "fallback_username": "svc_marivo",
            },
        )
        session = SessionManager(self.metadata).create_session(
            "Route with fixed auth user",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )

        self.assertEqual(resolved_connection["user"], "svc_marivo")

    def test_docs_example_trino_session_user_prefers_session_over_fallback(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Docs Example Session User Trino",
            connection={"host": "trino.example.com", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "marivo",
            },
        )
        session = SessionManager(self.metadata).create_session(
            "Docs example: Trino from session user",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )

        self.assertEqual(resolved_connection["user"], "alice")

    def test_docs_example_trino_fixed_fallback_ignores_session_user(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Docs Example Fixed Trino",
            connection={"host": "trino.example.com", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "fixed",
                "fallback_username": "marivo",
            },
        )
        session = SessionManager(self.metadata).create_session(
            "Docs example: Trino fixed fallback",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )

        self.assertEqual(resolved_connection["user"], "marivo")

    def test_docs_example_duckdb_ignores_session_user(self) -> None:
        duckdb_path = Path(self.temp_dir.name) / "docs_example_duckdb_ignore.duckdb"
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Docs Example DuckDB Ignore",
            connection={"path": str(duckdb_path)},
        )
        session = SessionManager(self.metadata).create_session(
            "Docs example: DuckDB ignores session user",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        resolved_connection = self.service.resolve_runtime_connection(
            engine,
            session_id=session["session_id"],
        )
        analytics = self.service.build_analytics_engine(
            engine["engine_id"],
            session_id=session["session_id"],
        )

        self.assertNotIn("user", resolved_connection)
        self.assertIsInstance(analytics, DuckDBAnalyticsEngine)

    def test_build_trino_engine_logs_execution_auth_resolution(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Logged Session Auth Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={"mode": "username_only", "username_source": "session_user"},
        )
        session = SessionManager(self.metadata).create_session(
            "Route with logged session user",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        fake_engine = _FakeAnalyticsEngine()
        with patch("app.registry.engine_registry.build_analytics_engine", return_value=fake_engine):
            analytics = self.service.build_analytics_engine(
                engine["engine_id"],
                session_id=session["session_id"],
            )
            with self.assertLogs("marivo.execution_auth", level="INFO") as captured:
                analytics.query_rows("SELECT 1")

        payload = json.loads(JSONFormatter().format(captured.records[0]))
        self.assertEqual(payload["message"], "execution_auth_resolved")
        self.assertEqual(payload["session_id"], session["session_id"])
        self.assertEqual(payload["engine_id"], engine["engine_id"])
        self.assertEqual(payload["session_user"], "alice")
        self.assertEqual(payload["actor_ref"], "agent.alice")
        self.assertEqual(fake_engine.queries, [("SELECT 1", None)])

    def test_build_trino_engine_does_not_log_execution_auth_until_runtime_use(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Deferred Audit Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={"mode": "username_only", "username_source": "session_user"},
        )
        session = SessionManager(self.metadata).create_session(
            "Route with deferred audit",
            {},
            {},
            {},
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        with (
            patch(
                "app.registry.engine_registry.build_analytics_engine",
                return_value=_FakeAnalyticsEngine(),
            ),
            self.assertNoLogs("marivo.execution_auth", level="INFO"),
        ):
            self.service.build_analytics_engine(
                engine["engine_id"],
                session_id=session["session_id"],
            )

    def test_build_trino_engine_uses_fallback_username_when_session_user_missing(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Fallback Auth Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "svc_marivo",
            },
        )
        session = SessionManager(self.metadata).create_session(
            "Route without session user", {}, {}, {}
        )

        analytics = self.service.build_analytics_engine(
            engine["engine_id"],
            session_id=session["session_id"],
        )

        self.assertEqual(analytics.user, "svc_marivo")

    def test_build_trino_engine_does_not_fallback_to_raw_connection_user(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="No Legacy Fallback Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={"mode": "username_only", "username_source": "session_user"},
        )

        with self.assertRaisesRegex(ValueError, "session_user_missing"):
            self.service.build_analytics_engine(engine["engine_id"])

    def test_build_trino_engine_logs_preflight_failure_when_session_user_missing(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Logged Missing Session User Trino",
            connection={"host": "localhost", "user": "legacy_user"},
            auth={"mode": "username_only", "username_source": "session_user"},
        )
        session = SessionManager(self.metadata).create_session(
            "Route missing session user but with actor",
            {},
            {},
            {},
            {"actor_ref": "agent.alice"},
        )

        with (
            self.assertLogs("marivo.execution_auth", level="WARNING") as captured,
            self.assertRaisesRegex(ValueError, "session_user_missing"),
        ):
            self.service.build_analytics_engine(
                engine["engine_id"],
                session_id=session["session_id"],
            )

        payload = json.loads(JSONFormatter().format(captured.records[0]))
        self.assertEqual(payload["message"], "execution_auth_preflight_failed")
        self.assertEqual(payload["session_id"], session["session_id"])
        self.assertEqual(payload["engine_id"], engine["engine_id"])
        self.assertIsNone(payload["session_user"])
        self.assertEqual(payload["actor_ref"], "agent.alice")
        self.assertEqual(payload["failure_code"], "session_user_missing")

    def test_validate_engine_reports_invalid_connection(self) -> None:
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Unconfigured DuckDB",
            connection={},
        )

        validation = self.service.validate_engine(engine["engine_id"])

        self.assertEqual(
            validation,
            {
                "engine_id": engine["engine_id"],
                "is_valid": False,
                "readiness_status": "not_ready",
                "failure_code": "engine_invalid_connection",
            },
        )

    def test_get_engine_readiness_reports_ready_engine(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Ready Trino",
            connection={"host": "localhost"},
        )

        readiness = self.service.get_engine_readiness(engine["engine_id"])

        self.assertEqual(
            readiness,
            {
                "engine_id": engine["engine_id"],
                "readiness_status": "ready",
                "failure_code": None,
            },
        )

    def test_get_engine_reports_not_ready_when_namespace_is_invalid(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Broken Namespace Trino",
            connection={"host": "localhost"},
        )
        self.metadata.execute(
            "UPDATE engines SET default_namespace_json = ? WHERE engine_id = ?",
            ['{"catalog":"hive","schema":""}', engine["engine_id"]],
        )

        fetched = self.service.get_engine(engine["engine_id"])

        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "engine_invalid_namespace")

    def test_get_engine_reports_not_ready_when_deployment_capabilities_are_invalid(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Broken Deployment Trino",
            connection={"host": "localhost"},
        )
        self.metadata.execute(
            "UPDATE engines SET deployment_capabilities_json = ? WHERE engine_id = ?",
            [
                '{"supported_step_types":["sample_rows",""],"min_staleness_minutes":-1}',
                engine["engine_id"],
            ],
        )

        fetched = self.service.get_engine(engine["engine_id"])

        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "engine_invalid_deployment_capabilities")

    def test_get_engine_reports_not_ready_when_policy_is_invalid(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Broken Policy Trino",
            connection={"host": "localhost"},
        )
        self.metadata.execute(
            "UPDATE engines SET policy_json = ? WHERE engine_id = ?",
            [
                '{"allowed_step_types":["sample_rows",""],"required_policy_support":[]}',
                engine["engine_id"],
            ],
        )

        fetched = self.service.get_engine(engine["engine_id"])

        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "engine_invalid_policy")

    def test_get_engine_degrades_on_malformed_stored_auth_json(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Malformed Auth Trino",
            connection={"host": "localhost"},
        )
        self.metadata.execute(
            "UPDATE engines SET auth_json = ? WHERE engine_id = ?",
            ['{"mode":"username_only","extra":"oops"}', engine["engine_id"]],
        )

        fetched = self.service.get_engine(engine["engine_id"])

        self.assertEqual(fetched["auth"], {"mode": "none"})

    def test_get_engine_degrades_on_legacy_row_without_auth_column(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_engines.meta.sqlite"
        con = sqlite3.connect(legacy_path)
        try:
            con.execute(
                """
                CREATE TABLE engines (
                    engine_id TEXT PRIMARY KEY,
                    engine_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    connection_json TEXT NOT NULL,
                    default_namespace_json TEXT NOT NULL DEFAULT '{}',
                    intrinsic_capabilities_json TEXT NOT NULL DEFAULT '{}',
                    deployment_capabilities_json TEXT NOT NULL DEFAULT '{}',
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE source_execution_mappings (
                    mapping_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    engine_id TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    catalog_mappings_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT INTO engines (
                    engine_id, engine_type, display_name, connection_json,
                    default_namespace_json, intrinsic_capabilities_json,
                    deployment_capabilities_json, policy_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "eng_legacy",
                    "duckdb",
                    "Legacy DuckDB",
                    json.dumps({"path": "/tmp/legacy.duckdb"}),
                    json.dumps({"catalog": None, "schema": None}),
                    json.dumps(build_engine_capability_profile("duckdb").to_dict()),
                    json.dumps({}),
                    json.dumps(
                        {
                            "allowed_step_types": [],
                            "required_policy_support": [],
                        }
                    ),
                    "active",
                    "2026-04-24T00:00:00Z",
                    "2026-04-24T00:00:00Z",
                ),
            )
            con.commit()
        finally:
            con.close()

        legacy_service = EngineService(SQLiteMetadataStore(legacy_path))
        engine = legacy_service.get_engine("eng_legacy")

        self.assertEqual(engine["engine_id"], "eng_legacy")
        self.assertEqual(engine["engine_type"], "duckdb")
        self.assertEqual(engine["display_name"], "Legacy DuckDB")
        self.assertEqual(engine["auth"], {"mode": "none"})
        self.assertEqual(legacy_service.list_engines()[0]["auth"], {"mode": "none"})


class EngineAPITests(unittest.TestCase):
    """Integration tests for engine endpoints via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_engine_api.duckdb"
        meta_path = Path(cls.temp_dir.name) / "test_engine_api.meta.sqlite"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path, metadata_store=SQLiteMetadataStore(meta_path)))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_post_and_get_engine(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Trino",
                "connection": {
                    "host": "localhost",
                    "port": 8080,
                    "user": "test",
                    "catalog": "hive",
                    "schema": "default",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine = resp.json()
        self.assertTrue(engine["engine_id"].startswith("eng_"))
        self.assertEqual(engine["engine_type"], "trino")
        self.assertEqual(engine["auth"], {"mode": "none"})
        self.assertEqual(engine["default_namespace"], {"catalog": "hive", "schema": "default"})
        self.assertEqual(
            engine["intrinsic_capabilities"],
            {
                "materialization_support": "catalog_table",
                "performance_class": "distributed",
                "federation_support": "connector",
            },
        )
        self.assertEqual(engine["deployment_capabilities"], {})
        self.assertEqual(
            engine["policy"],
            {"allowed_step_types": [], "required_policy_support": []},
        )
        self.assertEqual(engine["readiness_status"], "ready")
        self.assertIsNone(engine["failure_code"])

        resp = self.client.get(f"/engines/{engine['engine_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["engine_id"], engine["engine_id"])
        self.assertEqual(resp.json()["auth"], {"mode": "none"})
        self.assertEqual(resp.json()["readiness_status"], "ready")
        self.assertIsNone(resp.json()["failure_code"])
        self.assertEqual(resp.json()["mappings"], [])

    def test_post_engine_with_username_only_auth(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Auth Trino",
                "connection": {
                    "host": "localhost",
                    "port": 8080,
                    "catalog": "hive",
                    "schema": "default",
                },
                "auth": {
                    "mode": "username_only",
                    "username_source": "session_user",
                    "fallback_username": "marivo",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine_id = resp.json()["engine_id"]
        self.assertEqual(
            resp.json()["auth"],
            {
                "mode": "username_only",
                "username_source": "session_user",
                "fallback_username": "marivo",
            },
        )

        detail = self.client.get(f"/engines/{engine_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["auth"], resp.json()["auth"])

        listed = self.client.get("/engines")
        self.assertEqual(listed.status_code, 200)
        listed_auth = {
            engine["engine_id"]: engine["auth"]
            for engine in listed.json()
            if engine["engine_id"] == engine_id
        }
        self.assertEqual(listed_auth, {engine_id: resp.json()["auth"]})

    def test_post_engine_trims_fallback_username(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Fixed Auth Trino",
                "connection": {"host": "localhost"},
                "auth": {
                    "mode": "username_only",
                    "username_source": "fixed",
                    "fallback_username": " marivo ",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auth"]["fallback_username"], "marivo")

    def test_post_engine_rejects_mode_none_with_extra_auth_fields(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Invalid None Auth",
                "connection": {"host": "localhost"},
                "auth": {"mode": "none", "fallback_username": "marivo"},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_engine_rejects_fixed_username_without_fallback(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Invalid Fixed Auth",
                "connection": {"host": "localhost"},
                "auth": {"mode": "username_only", "username_source": "fixed"},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_engine_rejects_duckdb_username_only_auth(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "API Invalid DuckDB Auth",
                "connection": {"path": "/tmp/api.duckdb"},
                "auth": {"mode": "username_only", "username_source": "session_user"},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_engine_includes_mapping_summaries(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Engine Summary Source",
                "authority": {
                    "catalog_system": "duckdb",
                    "connection": {
                        "path": str(Path(self.temp_dir.name) / "test_engine_api.duckdb")
                    },
                    "synthetic_catalog": "main",
                },
            },
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Engine Summary Target",
                "connection": {"path": str(Path(self.temp_dir.name) / "test_engine_api.duckdb")},
            },
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

        detail = self.client.get(f"/engines/{engine_resp.json()['engine_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["mappings"]), 1)
        self.assertEqual(
            detail.json()["mappings"][0]["mapping_id"], mapping_resp.json()["mapping_id"]
        )
        self.assertEqual(detail.json()["mappings"][0]["source_id"], source_resp.json()["source_id"])
        self.assertEqual(
            detail.json()["mappings"][0]["catalog_mappings"],
            [
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

    def test_list_engines(self) -> None:
        self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "API DuckDB",
                "connection": {"path": "/tmp/api.duckdb"},
            },
        )
        resp = self.client.get("/engines")
        self.assertEqual(resp.status_code, 200)
        engines = resp.json()
        self.assertIsInstance(engines, list)
        self.assertTrue(any(e["display_name"] == "API DuckDB" for e in engines))
        matching = next(e for e in engines if e["display_name"] == "API DuckDB")
        self.assertEqual(matching["auth"], {"mode": "none"})
        self.assertEqual(matching["readiness_status"], "ready")
        self.assertIsNone(matching["failure_code"])

    def test_post_engine_omitting_deployment_capabilities_keeps_defaults(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Default Trino",
                "connection": {"host": "localhost"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine = resp.json()
        self.assertEqual(engine["deployment_capabilities"], {})

    def test_post_engine_rejects_unsupported_type(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "spark",
                "display_name": "Unsupported Engine",
                "connection": {},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_engine_404(self) -> None:
        resp = self.client.get("/engines/eng_nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_engine_api_normalizes_malformed_stored_connection(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "Malformed Stored Engine",
                "connection": {"host": "localhost"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine_id = resp.json()["engine_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            "UPDATE engines SET connection_json = ? WHERE engine_id = ?",
            ['"oops"', engine_id],
        )

        detail = self.client.get(f"/engines/{engine_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["connection"], {})
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "engine_invalid_connection")

        listed = self.client.get("/engines")
        self.assertEqual(listed.status_code, 200)
        listed_engine = next(item for item in listed.json() if item["engine_id"] == engine_id)
        self.assertEqual(listed_engine["connection"], {})
        self.assertEqual(listed_engine["readiness_status"], "not_ready")
        self.assertEqual(listed_engine["failure_code"], "engine_invalid_connection")

    def test_engine_api_degrades_malformed_stored_auth_json(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "Malformed Stored Auth Engine",
                "connection": {"host": "localhost"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine_id = resp.json()["engine_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            "UPDATE engines SET auth_json = ? WHERE engine_id = ?",
            ['{"mode":"username_only","extra":"oops"}', engine_id],
        )

        detail = self.client.get(f"/engines/{engine_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["auth"], {"mode": "none"})

        listed = self.client.get("/engines")
        self.assertEqual(listed.status_code, 200)
        listed_engine = next(item for item in listed.json() if item["engine_id"] == engine_id)
        self.assertEqual(listed_engine["auth"], {"mode": "none"})

    def test_engine_openapi_uses_explicit_response_model(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        schemas = payload["components"]["schemas"]
        self.assertIn("EngineResponse", schemas)
        self.assertIn("EngineDefaultNamespaceResponse", schemas)
        self.assertIn("EngineAuthResponse", schemas)
        self.assertIn("EngineIntrinsicCapabilitiesResponse", schemas)
        self.assertIn("EngineDeploymentCapabilitiesResponse", schemas)
        self.assertIn("EnginePolicyResponse", schemas)

        engine_get = payload["paths"]["/engines"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(engine_get["items"]["$ref"], "#/components/schemas/EngineResponse")

        engine_post = payload["paths"]["/engines"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(engine_post["$ref"], "#/components/schemas/EngineResponse")
        self.assertIn("auth", schemas["EngineResponse"]["properties"])


class TrinoAnalyticsEngineTests(unittest.TestCase):
    """Unit tests for TrinoAnalyticsEngine using mocks."""

    def test_init_stores_config(self) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        engine = TrinoAnalyticsEngine(
            host="trino.example.com", port=8443, user="alice", catalog="iceberg", schema="prod"
        )
        self.assertEqual(engine.host, "trino.example.com")
        self.assertEqual(engine.port, 8443)
        self.assertEqual(engine.user, "alice")
        self.assertEqual(engine.catalog, "iceberg")
        self.assertEqual(engine.schema, "prod")

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_initialize_validates_connectivity(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost", port=8080)
        engine.initialize()

        mock_cursor.execute.assert_called_once_with("SELECT 1")
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_query_rows(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "alice"), (2, "bob")]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost")
        rows = engine.query_rows("SELECT id, name FROM users")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"id": 1, "name": "alice"})
        self.assertEqual(rows[1], {"id": 2, "name": "bob"})
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_table_exists(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost", catalog="hive", schema="default")
        result = engine.table_exists("my_table")

        self.assertTrue(result)
        mock_cursor.execute.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_table_row_count(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost")
        count = engine.table_row_count("my_table")

        self.assertEqual(count, 42)
        mock_conn.close.assert_called_once()


class BuildAnalyticsEngineTests(unittest.TestCase):
    """Tests for the _build_analytics_engine factory function."""

    def test_build_duckdb(self) -> None:
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        engine = _build_analytics_engine("duckdb", {"path": "/tmp/test.duckdb"})
        self.assertIsInstance(engine, DuckDBAnalyticsEngine)

    def test_build_trino(self) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        engine = _build_analytics_engine(
            "trino",
            {
                "host": "localhost",
                "port": 8080,
                "user": "test",
                "catalog": "hive",
                "schema": "default",
            },
        )
        self.assertIsInstance(engine, TrinoAnalyticsEngine)

    def test_build_unsupported(self) -> None:
        with self.assertRaises(ValueError):
            _build_analytics_engine("spark", {})


class CapabilityProfileTests(unittest.TestCase):
    def test_build_engine_capability_profile_defaults_duckdb(self) -> None:
        profile = build_engine_capability_profile("duckdb")
        self.assertEqual(profile.performance_class, "embedded")
        self.assertIn("temporary_tables", profile.supported_sql_features)


class EngineConfigTests(unittest.TestCase):
    """Tests for YAML config loading with engines."""

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

    def test_load_config_with_engines(self) -> None:
        from app.config import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                textwrap.dedent("""\
                metadata:
                  engine: sqlite
                  path: data/test.meta.sqlite
                observability:
                  log_level: DEBUG
                """)
            )
            f.flush()
            config = load_config(Path(f.name))

        assert config.metadata is not None
        self.assertEqual(config.metadata.path, "data/test.meta.sqlite")
        self.assertEqual(config.observability.log_level, "DEBUG")

    def test_startup_does_not_register_engines_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                textwrap.dedent("""\
                observability:
                  log_level: INFO
                """)
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            client = TestClient(
                create_app(
                    metadata_store=metadata,
                    analytics_engine=self.shared_analytics,
                    config_path=str(config_path),
                )
            )

            resp = client.get("/engines")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), [])

            client.close()


if __name__ == "__main__":
    unittest.main()
