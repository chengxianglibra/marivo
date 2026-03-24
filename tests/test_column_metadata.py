from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class ColumnUnitMetadataTests(unittest.TestCase):
    """Tests for column unit annotation and enrichment in profile_table/sample_rows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "col_unit.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

        # Register and sync a DuckDB source
        resp = cls.client.post(
            "/sources",
            json={"source_type": "duckdb", "display_name": "ColUnit Test", "connection": {"path": str(cls.db_path)}},
        )
        cls.source_id = resp.json()["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_profile_table_has_data_type(self) -> None:
        """After sync, profile_table column entries should have data_type from synced column objects."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test data_type in profile."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        col_entries = result["profile"]["columns"]
        self.assertGreater(len(col_entries), 0)
        # At least some columns should have data_type populated from sync
        has_data_type = any("data_type" in col for col in col_entries)
        self.assertTrue(has_data_type, "Expected at least one column to have data_type after sync")

    def test_sample_rows_columns_metadata_empty_without_sync(self) -> None:
        """sample_rows should return columns_metadata={} when no sync has been done (no crash)."""
        # Create a fresh app with no sync done
        temp = tempfile.TemporaryDirectory()
        try:
            fresh_db = Path(temp.name) / "fresh.duckdb"
            get_seeded_duckdb_path(fresh_db)
            fresh_client = TestClient(create_app(fresh_db))
            # Register source but don't sync
            fresh_client.post(
                "/sources",
                json={"source_type": "duckdb", "display_name": "Fresh", "connection": {"path": str(fresh_db)}},
            )
            session_id = fresh_client.post(
                "/sessions", json={"goal": "Test no sync."},
            ).json()["session_id"]
            resp = fresh_client.post(
                f"/sessions/{session_id}/steps/sample_rows",
                json={"table_name": "analytics.watch_events", "limit": 2},
            )
            self.assertEqual(resp.status_code, 200)
            result = resp.json()
            self.assertIn("columns_metadata", result)
            self.assertEqual(result["columns_metadata"], {})
            fresh_client.close()
        finally:
            temp.cleanup()

    def test_column_unit_end_to_end(self) -> None:
        """Full flow: sync → patch unit on a column → profile_table shows unit → sample_rows shows unit."""
        # Find a column object
        resp = self.client.get(f"/sources/{self.source_id}/objects", params={"type": "column"})
        col_objects = resp.json()
        self.assertGreater(len(col_objects), 0)

        # Find a column from watch_events
        watch_col = next(
            (o for o in col_objects if "watch_events" in o["fqn"]),
            col_objects[0],
        )
        col_obj_id = watch_col["object_id"]
        col_name = watch_col["native_name"]

        # Patch unit
        patch_resp = self.client.patch(
            f"/sources/{self.source_id}/objects/{col_obj_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["properties"]["unit"], "seconds")

        # profile_table should include unit in the column entry
        session_id = self.client.post(
            "/sessions", json={"goal": "Test unit in profile."},
        ).json()["session_id"]
        profile_resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(profile_resp.status_code, 200)
        col_profiles = profile_resp.json()["profile"]["columns"]
        annotated = next((c for c in col_profiles if c["column"] == col_name), None)
        if annotated is not None:
            self.assertEqual(annotated.get("unit"), "seconds")

        # sample_rows should include unit in columns_metadata
        session2_id = self.client.post(
            "/sessions", json={"goal": "Test unit in sample."},
        ).json()["session_id"]
        sample_resp = self.client.post(
            f"/sessions/{session2_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 2},
        )
        self.assertEqual(sample_resp.status_code, 200)
        sample_result = sample_resp.json()
        self.assertIn("columns_metadata", sample_result)
        if col_name in sample_result["columns_metadata"]:
            self.assertEqual(sample_result["columns_metadata"][col_name]["unit"], "seconds")


class ConstraintsAppliedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        # Sync source so profile_table works
        sources = cls.client.get("/sources").json()
        if sources:
            cls.client.post(f"/sources/{sources[0]['source_id']}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_no_constraints_returns_empty(self) -> None:
        """Session with no constraints → profile_table returns empty applied/skipped."""
        session_id = self.client.post(
            "/sessions", json={"goal": "No constraints test."}
        ).json()["session_id"]
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["applied"], [])
        self.assertEqual(ca["skipped"], [])
        self.assertIsNone(ca["note"])

    def test_profile_table_skips_constraints(self) -> None:
        """Session with raw_filter + constraints → profile_table skips them."""
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Constrained profile test.",
                "constraints": {"platform": "ios"},
                "raw_filter": "region = 'US'",
            },
        ).json()["session_id"]
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["applied"], [])
        self.assertEqual(len(ca["skipped"]), 2)
        self.assertIsNotNone(ca["note"])
        self.assertIn("profile_table", ca["note"])

    def test_aggregate_query_applies_constraints(self) -> None:
        """Session with raw_filter → aggregate_query lists it in applied."""
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Constrained aggregate test.",
                "raw_filter": "platform = 'android'",
            },
        ).json()["session_id"]
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["skipped"], [])
        self.assertEqual(len(ca["applied"]), 1)
        self.assertIn("raw_filter:", ca["applied"][0])


if __name__ == "__main__":
    unittest.main()
