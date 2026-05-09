from __future__ import annotations

import os
import re
import tempfile
import unittest
from contextlib import closing
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from marivo.adapters.schema import expected_metadata_tables, metadata_schema_marker_row
from marivo.adapters.server.mysql_metadata import (
    MySQLMetadataStore,
    _expected_mysql_foreign_key_names,
    _expected_mysql_index_names,
)
from marivo.config import MetadataConfig
from marivo.core.evidence.canonical_finding import (
    FindingExtractionResult,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.runtime.evidence.finding_extractor_registry import (
    FindingExtractor,
    FindingExtractorRegistry,
)
from marivo.transports.http.app_factory import create_app
from tests.shared_fixtures import get_seeded_duckdb_path

MYSQL_TEST_DSN = os.environ.get("MARIVO_TEST_MYSQL_DSN")
pytestmark = pytest.mark.mysql


def _mysql_config_from_env(database: str | None = None) -> dict[str, Any]:
    assert MYSQL_TEST_DSN is not None
    config = MetadataConfig.model_validate({"engine": "mysql", "dsn": MYSQL_TEST_DSN})
    mysql_config = config.mysql_connection_config()
    if database is not None:
        mysql_config["database"] = database
    return mysql_config


def _mysql_connect_kwargs(
    mysql_config: dict[str, Any], *, include_database: bool
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": mysql_config["host"],
        "port": int(mysql_config["port"]),
        "user": mysql_config["user"],
        "password": mysql_config.get("password"),
        "connect_timeout": int(mysql_config["connect_timeout"]),
        "charset": "utf8mb4",
        "autocommit": False,
    }
    if include_database:
        kwargs["database"] = mysql_config["database"]
    if mysql_config.get("ssl"):
        kwargs["ssl"] = {} if mysql_config["ssl"] is True else mysql_config["ssl"]
    return kwargs


def _pymysql_connect(**kwargs: Any) -> Any:
    pymysql = import_module("pymysql")
    cursors = import_module("pymysql.cursors")
    return pymysql.connect(cursorclass=cursors.DictCursor, **kwargs)


def _quote_database_name(database: str) -> str:
    if re.fullmatch(r"[a-zA-Z0-9_]+", database) is None:
        raise ValueError(f"Unsafe MySQL test database name: {database!r}")
    return f"`{database}`"


def _close_store_connections(store: MySQLMetadataStore) -> None:
    while True:
        try:
            con = store._pool.get_nowait()
        except Exception:
            return
        store._close_quietly(con)


def _build_observation_finding(artifact_id: str, session_id: str, step_id: str) -> dict[str, Any]:
    canonical_item_key, item_ref = make_item_identity("value")
    return {
        "finding_id": make_finding_id(artifact_id, "observation", canonical_item_key),
        "finding_type": "observation",
        "artifact_id": artifact_id,
        "step_ref": StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type="observation_artifact",
        ),
        "subject": {
            "metric": "test_metric",
            "entity": None,
            "slice": {},
            "grain": None,
            "analysis_axis": "scalar",
        },
        "observed_window": None,
        "quality": {
            "data_complete": None,
            "sample_size": None,
            "row_count": None,
            "null_rate": None,
            "quality_status": "ready",
            "quality_warnings": [],
        },
        "provenance": {
            "source_step_type": "observation_artifact",
            "extractor_name": "mysql_obs_stub",
            "extractor_version": "0.0.1",
            "artifact_schema_version": "v1",
            "canonical_item_key": canonical_item_key,
            "artifact_item_ref": item_ref,
            "projection_ref": None,
        },
        "payload": {"observation_kind": "scalar", "value": 42.0, "unit": None},
    }


class _ObserveSuccessExtractor(FindingExtractor):
    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "mysql_obs_success_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        del artifact_payload
        finding = _build_observation_finding(artifact_id, session_id, step_ref["step_id"])
        return {
            "findings": [finding],  # type: ignore[list-item]
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 1,
        }


class _FailFindingInsertMySQLStore(MySQLMetadataStore):
    def execute_sql(self, con: Any, sql: str, params: list[Any] | None = None) -> Any:
        if "INTO findings" in sql:
            raise RuntimeError("injected finding insert failure")
        return super().execute_sql(con, sql, params)


@unittest.skipUnless(MYSQL_TEST_DSN, "MySQL DSN not configured")
class MySQLMetadataIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = f"marivo_test_{uuid4().hex}"
        self.base_config = _mysql_config_from_env()
        self.admin = _pymysql_connect(
            **_mysql_connect_kwargs(self.base_config, include_database=False)
        )
        self._create_database(self.database)
        self.store = self._store_for_database(self.database)

    def tearDown(self) -> None:
        if hasattr(self, "store"):
            _close_store_connections(self.store)
        if hasattr(self, "admin"):
            self._drop_database(self.database)
            self.admin.close()

    def test_initialize_creates_expected_schema_shape(self) -> None:
        self.store.initialize()

        self.assertEqual(self._table_names(), expected_metadata_tables("mysql"))
        marker = self.store.query_one(
            "SELECT backend, schema_version, ddl_fingerprint FROM metadata_schema_marker"
        )
        self.assertEqual(marker, metadata_schema_marker_row("mysql"))

        primary_key_tables = self._primary_key_tables()
        self.assertEqual(primary_key_tables, expected_metadata_tables("mysql"))

        index_names = self._index_names()
        self.assertTrue(_expected_mysql_index_names().issubset(index_names))
        self.assertIn("idx_findings_artifact_type_key", self._unique_index_names())
        self.assertIn(
            "idx_propositions_session_type_identity",
            self._unique_index_names(),
        )

        self.assertEqual(self._foreign_key_names(), _expected_mysql_foreign_key_names())

    def test_initialize_fails_closed_for_unknown_non_empty_schema(self) -> None:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute("CREATE TABLE customer_table (id INT PRIMARY KEY)")
                con.commit()
            finally:
                cursor.close()

        with self.assertRaisesRegex(RuntimeError, "unknown tables"):
            self.store.initialize()

        self.assertIn("customer_table", self._table_names())

    def test_query_shape_execute_many_insert_ignore_and_upsert_contracts(self) -> None:
        self.store.initialize()
        session_rows = [
            (
                f"sess_{uuid4().hex}",
                "goal one",
                "{}",
                "{}",
                "{}",
                "open",
            ),
            (
                f"sess_{uuid4().hex}",
                "goal two",
                "{}",
                "{}",
                "{}",
                "open",
            ),
        ]
        self.store.execute_many(
            """
            INSERT INTO sessions
                (
                    session_id, goal, constraints_json, budget_json,
                    execution_identity_json, status
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            session_rows,
        )

        rows = self.store.query_rows(
            "SELECT session_id, goal FROM sessions WHERE session_id IN (?, ?) ORDER BY goal",
            [session_rows[0][0], session_rows[1][0]],
        )
        self.assertEqual(
            rows,
            [
                {"session_id": session_rows[0][0], "goal": "goal one"},
                {"session_id": session_rows[1][0], "goal": "goal two"},
            ],
        )
        self.assertIsNone(
            self.store.query_one(
                "SELECT session_id FROM sessions WHERE session_id = ?", ["missing"]
            )
        )

        source_id = f"ds_{uuid4().hex}"
        source_columns = [
            "datasource_id",
            "datasource_type",
            "display_name",
            "connection_json",
            "status",
            "created_at",
            "updated_at",
        ]
        self.store.insert_ignore(
            "datasources",
            source_columns,
            [
                source_id,
                "duckdb",
                "first",
                "{}",
                "active",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ],
        )
        self.store.insert_ignore(
            "datasources",
            source_columns,
            [
                source_id,
                "duckdb",
                "second",
                "{}",
                "active",
                "2026-01-02 00:00:00",
                "2026-01-02 00:00:00",
            ],
        )
        source = self.store.query_one(
            "SELECT display_name FROM datasources WHERE datasource_id = ?", [source_id]
        )
        self.assertEqual(source, {"display_name": "first"})

        step_id = f"step_{uuid4().hex}"
        self.store.execute(
            """
            INSERT INTO steps
                (step_id, session_id, step_type, status, summary, result_json, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [step_id, session_rows[0][0], "observe", "done", "summary", "{}", "{}"],
        )
        self.store.upsert_by_key(
            "step_metadata",
            ["step_id", "metadata_kind", "semantic_snapshot_json"],
            [step_id, "semantic", '{"version": 1}'],
            ["step_id"],
            ["metadata_kind", "semantic_snapshot_json"],
            updated_at_column="updated_at",
        )
        self.store.upsert_by_key(
            "step_metadata",
            ["step_id", "metadata_kind", "semantic_snapshot_json"],
            [step_id, "semantic", '{"version": 2}'],
            ["step_id"],
            ["metadata_kind", "semantic_snapshot_json"],
            updated_at_column="updated_at",
        )
        step_metadata = self.store.query_one(
            """
            SELECT COUNT(*) AS cnt, MAX(semantic_snapshot_json) AS snapshot, MAX(updated_at) AS updated_at
            FROM step_metadata
            WHERE step_id = ?
            """,
            [step_id],
        )
        self.assertIsNotNone(step_metadata)
        self.assertEqual(step_metadata["cnt"], 1)
        self.assertEqual(step_metadata["snapshot"], '{"version": 2}')
        self.assertIsNotNone(step_metadata["updated_at"])

    def test_foreign_key_cascade_and_unique_constraint_contracts(self) -> None:
        self.store.initialize()
        session_id = f"sess_{uuid4().hex}"
        step_id = f"step_{uuid4().hex}"
        artifact_id = f"art_{uuid4().hex}"
        self.store.execute(
            """
            INSERT INTO sessions
                (
                    session_id, goal, constraints_json, budget_json,
                    execution_identity_json, status
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [session_id, "integration", "{}", "{}", "{}", "open"],
        )
        self.store.execute(
            """
            INSERT INTO steps
                (step_id, session_id, step_type, status, summary, result_json, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [step_id, session_id, "observe", "done", "summary", "{}", "{}"],
        )
        self.store.execute(
            """
            INSERT INTO step_metadata
                (step_id, metadata_kind, semantic_snapshot_json)
            VALUES (?, ?, ?)
            """,
            [step_id, "semantic", "{}"],
        )
        self.store.execute("DELETE FROM steps WHERE step_id = ?", [step_id])
        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM step_metadata WHERE step_id = ?", [step_id]
            ),
            {"cnt": 0},
        )

        self.store.execute(
            """
            INSERT INTO artifacts
                (artifact_id, session_id, step_id, artifact_type, name, content_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [artifact_id, session_id, step_id, "evidence", "artifact", "{}"],
        )
        finding_values = [
            session_id,
            artifact_id,
            "{}",
            "observation",
            "item-key",
            "{}",
            "{}",
            "{}",
            "{}",
        ]
        self.store.execute(
            """
            INSERT INTO findings
                (
                    finding_id, session_id, artifact_id, step_ref_json, finding_type,
                    canonical_item_key, subject_json, quality_json, provenance_json, payload_json
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [f"find_{uuid4().hex}", *finding_values],
        )
        with self.assertRaises(Exception):
            self.store.execute(
                """
                INSERT INTO findings
                    (
                        finding_id, session_id, artifact_id, step_ref_json, finding_type,
                        canonical_item_key, subject_json, quality_json, provenance_json, payload_json
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [f"find_{uuid4().hex}", *finding_values],
            )

    def test_artifact_commit_rolls_back_staged_artifact_on_finding_failure(self) -> None:
        _close_store_connections(self.store)
        self.store = self._store_for_database(self.database, _FailFindingInsertMySQLStore)
        self.store.initialize()
        session_id = f"sess_{uuid4().hex}"
        step_id = f"step_{uuid4().hex}"
        self.store.execute(
            """
            INSERT INTO sessions
                (
                    session_id, goal, constraints_json, budget_json,
                    execution_identity_json, status
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [session_id, "rollback", "{}", "{}", "{}", "open"],
        )

        import json as _json

        from marivo.core.evidence.canonical_finding import StepRef as _StepRef
        from marivo.runtime.evidence.finding_extractor_registry import (
            validate_for_commit as _validate,
        )

        registry = FindingExtractorRegistry()
        registry.register(_ObserveSuccessExtractor())
        extractor = registry.find("observation_artifact", None)
        assert extractor is not None
        artifact_id = f"art_{uuid4().hex[:12]}"
        effective_step_ref = _StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type="observe",
        )
        result = extractor.extract(
            artifact_id, {"observation_type": "scalar"}, effective_step_ref, session_id
        )
        _validate(extractor.family, result)

        with (
            self.assertRaisesRegex(RuntimeError, "injected finding insert failure"),
            self.store.connect() as con,
        ):
            self.store.execute_sql(
                con,
                """
                    INSERT INTO artifacts
                        (artifact_id, session_id, step_id, artifact_type, name,
                         content_json, lifecycle, artifact_schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, 'staged', ?)
                    """,
                [
                    artifact_id,
                    session_id,
                    step_id,
                    "observation_artifact",
                    "obs_rollback",
                    _json.dumps({"observation_type": "scalar"}, default=str, sort_keys=True),
                    None,
                ],
            )
            for f in result["findings"]:
                self.store.execute_sql(
                    con,
                    self.store.insert_ignore_sql(
                        "findings",
                        [
                            "finding_id",
                            "session_id",
                            "artifact_id",
                            "step_ref_json",
                            "finding_type",
                            "canonical_item_key",
                            "subject_json",
                            "observed_window_json",
                            "quality_json",
                            "provenance_json",
                            "payload_json",
                            "schema_version",
                        ],
                    ),
                    [
                        f["finding_id"],
                        session_id,
                        artifact_id,
                        _json.dumps(f["step_ref"]),
                        f["finding_type"],
                        f["provenance"]["canonical_item_key"],
                        _json.dumps(f["subject"]),
                        _json.dumps(f["observed_window"])
                        if f.get("observed_window") is not None
                        else None,
                        _json.dumps(f["quality"]),
                        _json.dumps(f["provenance"]),
                        _json.dumps(f["payload"]),
                        "v1",
                    ],
                )
            self.store.execute_sql(
                con,
                "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
                [artifact_id],
            )
            con.commit()

        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM artifacts WHERE session_id = ?",
                [session_id],
            ),
            {"cnt": 0},
        )
        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM findings WHERE session_id = ?",
                [session_id],
            ),
            {"cnt": 0},
        )
        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM propositions WHERE session_id = ?",
                [session_id],
            ),
            {"cnt": 0},
        )

    def test_app_startup_with_mysql_config_supports_basic_session_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            duck_path = tmp_path / "analytics.duckdb"
            get_seeded_duckdb_path(duck_path)
            mysql_config = _mysql_config_from_env(self.database)
            config_path = tmp_path / "marivo.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "metadata": {
                            "engine": "mysql",
                            "host": mysql_config["host"],
                            "port": mysql_config["port"],
                            "database": mysql_config["database"],
                            "user": mysql_config["user"],
                            "password": mysql_config.get("password"),
                            "connect_timeout": mysql_config["connect_timeout"],
                            "pool_size": 1,
                            "ssl": mysql_config.get("ssl"),
                        }
                    },
                    sort_keys=False,
                )
            )

            app = create_app(db_path=duck_path, config_path=config_path)
            client = TestClient(app)
            try:
                create_response = client.post("/sessions", json={"goal": "mysql startup"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session_id"]

                detail_response = client.get(f"/sessions/{session_id}")
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(detail_response.json()["session_id"], session_id)

                list_response = client.get("/sessions", params={"session_id": session_id})
                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(list_response.json()["items"][0]["session_id"], session_id)
            finally:
                client.close()
                _close_store_connections(app.state.metadata_store)

    def _create_database(self, database: str) -> None:
        cursor = self.admin.cursor()
        try:
            cursor.execute(
                f"CREATE DATABASE {_quote_database_name(database)} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            self.admin.commit()
        except Exception as exc:
            self.admin.rollback()
            raise AssertionError(
                "MARIVO_TEST_MYSQL_DSN must allow CREATE/DROP DATABASE for integration tests"
            ) from exc
        finally:
            cursor.close()

    def _drop_database(self, database: str) -> None:
        cursor = self.admin.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS {_quote_database_name(database)}")
            self.admin.commit()
        finally:
            cursor.close()

    def _store_for_database(
        self,
        database: str,
        store_cls: type[MySQLMetadataStore] = MySQLMetadataStore,
    ) -> MySQLMetadataStore:
        mysql_config = _mysql_config_from_env(database)
        return store_cls(
            host=str(mysql_config["host"]),
            port=int(mysql_config["port"]),
            database=str(mysql_config["database"]),
            user=str(mysql_config["user"]),
            password=(
                str(mysql_config["password"]) if mysql_config.get("password") is not None else None
            ),
            connect_timeout=int(mysql_config["connect_timeout"]),
            pool_size=1,
            ssl=mysql_config.get("ssl"),
        )

    def _database_connection(self) -> Any:
        mysql_config = _mysql_config_from_env(self.database)
        return _pymysql_connect(**_mysql_connect_kwargs(mysql_config, include_database=True))

    def _table_names(self) -> set[str]:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute(
                    """
                    SELECT TABLE_NAME AS name
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                    """
                )
                return {str(row["name"]) for row in cursor.fetchall()}
            finally:
                cursor.close()

    def _primary_key_tables(self) -> set[str]:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute(
                    """
                    SELECT DISTINCT TABLE_NAME AS table_name
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND INDEX_NAME = 'PRIMARY'
                    """
                )
                return {str(row["table_name"]) for row in cursor.fetchall()}
            finally:
                cursor.close()

    def _index_names(self) -> set[str]:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute(
                    """
                    SELECT DISTINCT INDEX_NAME AS name
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                    """
                )
                return {str(row["name"]) for row in cursor.fetchall()}
            finally:
                cursor.close()

    def _unique_index_names(self) -> set[str]:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute(
                    """
                    SELECT DISTINCT INDEX_NAME AS name
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND NON_UNIQUE = 0
                    """
                )
                return {str(row["name"]) for row in cursor.fetchall()}
            finally:
                cursor.close()

    def _foreign_key_names(self) -> set[str]:
        with closing(self._database_connection()) as con:
            cursor = con.cursor()
            try:
                cursor.execute(
                    """
                    SELECT CONSTRAINT_NAME AS name
                    FROM information_schema.REFERENTIAL_CONSTRAINTS
                    WHERE CONSTRAINT_SCHEMA = DATABASE()
                    """
                )
                return {str(row["name"]) for row in cursor.fetchall()}
            finally:
                cursor.close()


if __name__ == "__main__":
    unittest.main()
