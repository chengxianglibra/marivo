from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.storage.mysql_metadata import MySQLMetadataStore
from app.storage.schema import metadata_schema_marker_row
from tests.shared_fixtures import get_seeded_duckdb_path


def _mysql_store_from_env() -> MySQLMetadataStore:
    dsn = os.environ["MARIVO_TEST_MYSQL_DSN"]
    from app.config import MetadataConfig

    config = MetadataConfig.model_validate({"engine": "mysql", "dsn": dsn})
    mysql_config = config.mysql_connection_config()
    return MySQLMetadataStore(
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
        dsn=dsn,
    )


@unittest.skipUnless(os.environ.get("MARIVO_TEST_MYSQL_DSN"), "MySQL DSN not configured")
class MySQLMetadataIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _mysql_store_from_env()
        self.store.initialize()

    def test_initialize_writes_marker_and_supports_basic_dml(self) -> None:
        marker = self.store.query_one(
            "SELECT backend, schema_version, ddl_fingerprint FROM metadata_schema_marker"
        )
        self.assertEqual(marker, metadata_schema_marker_row("mysql"))

        session_id = f"sess_{uuid4().hex}"
        self.store.execute(
            """
            INSERT INTO sessions
                (session_id, goal, constraints_json, budget_json, policy_json, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [session_id, "integration", "{}", "{}", "{}", "open"],
        )
        row = self.store.query_one(
            "SELECT session_id, goal FROM sessions WHERE session_id = ?", [session_id]
        )
        self.assertEqual(row, {"session_id": session_id, "goal": "integration"})
        self.store.execute("DELETE FROM sessions WHERE session_id = ?", [session_id])

    def test_transaction_rolls_back_staged_artifact_on_failure(self) -> None:
        artifact_id = f"art_{uuid4().hex}"

        with self.assertRaises(Exception), self.store.connect() as con:
            self.store.execute_sql(
                con,
                """
                INSERT INTO artifacts
                    (artifact_id, session_id, step_id, artifact_type, name, content_json, lifecycle)
                VALUES (?, ?, ?, ?, ?, ?, 'staged')
                """,
                [artifact_id, "sess_missing", "step_missing", "evidence", "bad", "[]"],
            )
            self.store.execute_sql(
                con,
                "INSERT INTO findings (finding_id) VALUES (?)",
                [f"find_{uuid4().hex}"],
            )
            con.commit()

        row = self.store.query_one(
            "SELECT artifact_id FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNone(row)

    def test_app_startup_with_mysql_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            duck_path = Path(tmp) / "analytics.duckdb"
            get_seeded_duckdb_path(duck_path)
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                f"metadata:\n  engine: mysql\n  dsn: {os.environ['MARIVO_TEST_MYSQL_DSN']!r}\n"
            )

            app = create_app(db_path=duck_path, config_path=config_path)
            client = TestClient(app)
            try:
                self.assertEqual(client.get("/sources").status_code, 200)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
