from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.evidence_engine.factories import make_anomaly_observation
from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _seed_published_watch_metric(client: TestClient) -> None:
    metrics = client.get("/semantic/metrics").json()
    if any(m.get("name") == "watch_time_attribution" for m in metrics):
        return

    entity = client.post(
        "/semantic/entities",
        json={"name": "session", "display_name": "Session", "keys": ["session_id"]},
    ).json()
    client.post(f"/semantic/entities/{entity['entity_id']}/publish")

    metric = client.post(
        "/semantic/metrics",
        json={
            "name": "watch_time_attribution",
            "display_name": "Watch Time Attribution",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity["entity_id"],
        },
    ).json()
    client.post(f"/semantic/metrics/{metric['metric_id']}/publish")


def _insert_tentative_claim(
    store,
    session_id: str,
    *,
    scope: dict[str, object],
    claim_id: str | None = None,
    supporting: list[str] | None = None,
) -> str:
    claim_id = claim_id or f"claim_{uuid4().hex[:12]}"
    supporting = supporting or []
    store.execute(
        """
        INSERT INTO claims (
            claim_id, session_id, claim_type, text, scope_json, confidence, status,
            supporting_observation_ids_json, contradicting_observation_ids_json,
            confidence_breakdown_json, inference_level, inference_justification_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            claim_id,
            session_id,
            "root_cause_candidate",
            "tentative attribution claim",
            json.dumps(scope),
            0.55,
            "tentative",
            json.dumps(supporting),
            json.dumps([]),
            json.dumps(
                {
                    "effect_strength": 0.5,
                    "consistency": 0.6,
                    "sample_score": 0.6,
                    "data_quality_score": 0.9,
                    "contradiction_penalty": 0.0,
                }
            ),
            "L0",
            json.dumps([]),
        ],
    )
    return claim_id


def _insert_anomaly_observation(
    store,
    session_id: str,
    *,
    obs_id: str | None = None,
    metric: str = "watch_time_attribution",
    slice_info: dict[str, object] | None = None,
) -> str:
    obs = make_anomaly_observation(
        metric,
        slice_info or {"platform": "android"},
        {
            "value": 100.0,
            "mean": 10.0,
            "std": 4.0,
            "z_score": 3.5,
            "outlier_factor": 10.0,
            "method": "z_score",
            "stratum": {},
            "sample_size": 10,
        },
        {"freshness_ok": True, "sample_size_ok": True},
    )
    if obs_id is not None:
        obs["observation_id"] = obs_id
    store.execute(
        """
        INSERT INTO observations (
            observation_id, session_id, step_id, observation_type,
            subject_json, payload_json, significance_json, quality_json,
            observed_window_json, temporal_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            obs["observation_id"],
            session_id,
            "step_test",
            obs["type"],
            json.dumps(obs["subject"]),
            json.dumps(obs["payload"]),
            json.dumps(obs["significance"]),
            json.dumps(obs["quality"]),
            None,
            0,
        ],
    )
    return str(obs["observation_id"])


class AttributeChangeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "attribution.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        _seed_published_watch_metric(cls.client)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_session(self, goal: str = "attribution test", raw_filter: str | None = None) -> str:
        payload = {"goal": goal, "budget": {"max_steps": 10}}
        if raw_filter is not None:
            payload["raw_filter"] = raw_filter
        resp = self.client.post("/sessions", json=payload)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    def _period_bounds(self) -> tuple[str, str, str, str]:
        services = self.client.app.state.services
        row = services.analytics_engine.query_rows(
            "SELECT MAX(event_date) AS max_date FROM analytics.watch_events"
        )[0]
        period_end = str(row["max_date"])
        period_start = period_end
        end_date = date.fromisoformat(period_end)
        baseline_end = (end_date - timedelta(days=7)).isoformat()
        baseline_start = baseline_end
        return period_start, period_end, baseline_start, baseline_end

    def test_attribute_change_returns_contributions_and_observations(self) -> None:
        session_id = self._create_session(raw_filter="platform = 'android'")
        period_start, period_end, baseline_start, baseline_end = self._period_bounds()

        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": ["platform", "content_type"],
                "top_k": 3,
                "min_contribution_pct": 0.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["step_type"], "attribute_change")
        self.assertIn("contributions", body)
        self.assertTrue(body["contributions"])
        self.assertIn("observations", body)
        self.assertTrue(body["observations"])
        self.assertIn("readiness", body)
        self.assertIn("live_claims", body)
        self.assertIn("constraints_applied", body)
        self.assertGreaterEqual(len(body["contributions"]), 1)
        self.assertTrue(all("top_contributors" in item for item in body["contributions"]))
        self.assertTrue(body["constraints_applied"]["applied"])

    def test_attribute_change_links_anomaly_with_justifies_edge(self) -> None:
        session_id = self._create_session("anomaly link test")
        store = self.client.app.state.services.metadata_store
        anomaly_id = _insert_anomaly_observation(store, session_id)
        period_start, period_end, baseline_start, baseline_end = self._period_bounds()

        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": ["platform"],
                "anomaly_observation_id": anomaly_id,
                "min_contribution_pct": 0.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        edges = store.query_rows(
            """
            SELECT edge_type, from_node_type, to_node_type, from_node_id, to_node_id
            FROM evidence_edges
            WHERE session_id = ? AND edge_type = 'justifies'
            """,
            [session_id],
        )
        self.assertTrue(
            any(
                edge["from_node_type"] == "observation" and edge["to_node_type"] == "observation"
                for edge in edges
            )
        )

    def test_attribute_change_rejects_missing_anomaly_observation(self) -> None:
        session_id = self._create_session("missing anomaly id")
        period_start, period_end, baseline_start, baseline_end = self._period_bounds()

        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": ["platform"],
                "anomaly_observation_id": "obs_missing_123",
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("anomaly_observation_id not found", resp.json()["detail"])

    def test_attribute_change_rejects_empty_candidate_dimensions(self) -> None:
        session_id = self._create_session("empty dims")
        period_start, period_end, baseline_start, baseline_end = self._period_bounds()

        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": [],
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_attribute_change_empty_current_window_returns_empty_contributions(self) -> None:
        session_id = self._create_session("empty window")
        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": "2099-01-01",
                "period_end": "2099-01-01",
                "baseline_start": "2098-12-25",
                "baseline_end": "2098-12-25",
                "candidate_dimensions": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["contributions"], [])
        self.assertFalse(body["debug"]["current_has_data"])

    def test_attribute_change_creates_l3_claim_for_matching_contributor(self) -> None:
        probe_session = self._create_session("probe attribution")
        period_start, period_end, baseline_start, baseline_end = self._period_bounds()
        probe_resp = self.client.post(
            f"/sessions/{probe_session}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": ["platform"],
                "top_k": 5,
                "min_contribution_pct": 0.0,
            },
        )
        self.assertEqual(probe_resp.status_code, 200)
        top_value = probe_resp.json()["observations"][0]["payload"]["biggest_shift_segment"]

        session_id = self._create_session("L3 claim test")
        store = self.client.app.state.services.metadata_store
        claim_id = _insert_tentative_claim(
            store,
            session_id,
            scope={
                "metric": "watch_time_attribution",
                "slice": {"segment": "platform", "biggest_shift": top_value},
            },
        )

        resp = self.client.post(
            f"/sessions/{session_id}/steps/attribute_change",
            json={
                "metric_name": "watch_time_attribution",
                "table_name": "analytics.watch_events",
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "candidate_dimensions": ["platform"],
                "top_k": 5,
                "min_contribution_pct": 0.0,
            },
        )
        self.assertEqual(resp.status_code, 200)

        claim_row = store.query_one(
            "SELECT inference_level, inference_justification_json FROM claims WHERE claim_id = ?",
            [claim_id],
        )
        self.assertIsNotNone(claim_row)
        self.assertEqual(claim_row["inference_level"], "L3")
        self.assertIn("mechanistic_explanation", claim_row["inference_justification_json"])

        resp_synth = self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")
        self.assertEqual(resp_synth.status_code, 200)
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        matching_claims = [c for c in evidence.get("claims", []) if c["claim_id"] == claim_id]
        self.assertTrue(matching_claims)
        self.assertEqual(matching_claims[0]["inference_level"], "L3")


if __name__ == "__main__":
    unittest.main()
