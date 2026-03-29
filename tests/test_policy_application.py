from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class PolicyApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "policy_application.duckdb"
        get_seeded_duckdb_path(self.db_path)
        self.client = TestClient(create_app(self.db_path))
        self.governance = self.client.app.state.governance_service
        self.metadata = self.client.app.state.metadata_store
        assert self.governance is not None

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_check_step_returns_policy_transforms(self) -> None:
        row_filter = self.governance.create_policy(
            "ios_only",
            "row_filter",
            definition={"predicate": "platform = 'ios'"},
        )
        field_mask = self.governance.create_policy(
            "mask_email",
            "field_mask",
            definition={"fields": ["email"]},
        )

        result = self.governance.check_step(
            "sess_policy_app",
            "profile_table",
            params={"table_name": "analytics.watch_events"},
            tables=["analytics.watch_events"],
        )

        self.governance.delete_policy(row_filter["policy_id"])
        self.governance.delete_policy(field_mask["policy_id"])

        self.assertTrue(result["passed"])
        self.assertEqual(
            [decision["code"] for decision in result["soft_signals"]],
            ["row_filter_applied", "field_mask_applied"],
        )
        self.assertEqual(
            result["transforms"]["row_filters"][0]["expression"],
            "platform = 'ios'",
        )
        self.assertEqual(result["transforms"]["masked_fields"], ["email"])

    def test_run_step_persists_governance_context_in_provenance(self) -> None:
        policy = self.governance.create_policy(
            "ios_only_profile",
            "row_filter",
            definition={"predicate": "platform = 'ios'"},
            scope={"step_types": ["profile_table"]},
        )
        session_id = self.client.post("/sessions", json={"goal": "policy provenance"}).json()[
            "session_id"
        ]

        response = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )

        self.governance.delete_policy(policy["policy_id"])

        self.assertEqual(response.status_code, 200)
        self.assertIn("governance", response.json())

        step = self.metadata.query_one(
            """
            SELECT provenance_json
            FROM steps
            WHERE session_id = ? AND step_type = 'profile_table'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [session_id],
        )
        assert step is not None
        provenance = json.loads(step["provenance_json"])

        self.assertEqual(
            provenance["governance"]["transforms"]["row_filters"][0]["expression"],
            "platform = 'ios'",
        )
        self.assertEqual(
            provenance["governance"]["soft_signals"][0]["code"],
            "row_filter_applied",
        )


if __name__ == "__main__":
    unittest.main()
