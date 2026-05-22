from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.main import create_app
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_detect_tables(db_path: Path) -> None:
    """Copy the cached detect_intent template with spike/uniform data."""
    get_named_seeded_duckdb_path(db_path, "detect_intent")


def _seed_metadata(
    meta: SQLiteMetadataStore,
    *,
    db_path: str | Path | None = None,
    src_suffix: str = "01",
    metric_name: str = "detect_event_count",
    table_fqn: str = "analytics.detect_events",
    native_name: str = "detect_events",
    binding_role: str = "primary",
    measure_type: str | None = None,
    dimensions: list[str] | None = None,
) -> str:
    """Insert minimal metadata records so detect can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = f"ds_detecttest{src_suffix}"
    obj_id = f"obj_detecttest{src_suffix}"
    seed_duckdb_source_object(
        meta,
        source_id=src_id,
        object_id=obj_id,
        display_name="Detect Test Source",
        table_name=native_name,
        table_fqn=table_fqn,
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=dimensions or ["event_date"],
        definition_sql="COUNT(*)",
        measure_type=measure_type,
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator=table_fqn,
        source_object_ref=obj_id,
        binding_role=binding_role,
        dimension_names=dimensions or ["event_date"],
    )
    return metric_name


class DetectIntentEndpointTests(unittest.TestCase):
    """HTTP-level tests for /sessions/{id}/intents/detect.

    Uses the detect intent fixture so HTTP tests can cover both success-empty
    and dimension execution paths.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "detect_http.duckdb"
        _seed_detect_tables(db_path)

        # Create the metadata store separately so the app and fixtures share one store.
        meta_path = db_path.with_suffix(".meta.sqlite")
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        cls.client = TestClient(
            create_app(db_path=db_path, metadata_store=metadata),
            headers={"X-Marivo-User": "test_user"},
        )

        # Register metric pointing to analytics.uniform_events.
        _seed_metadata(
            cls.client.app.state.services.metadata_store,
            db_path=db_path,
            src_suffix="http01",
            metric_name="http_detect_metric",
            table_fqn="analytics.uniform_events",
            native_name="uniform_events",
            dimensions=["event_date", "dimension.cluster"],
        )
        _seed_metadata(
            cls.client.app.state.services.metadata_store,
            db_path=db_path,
            src_suffix="http02",
            metric_name="http_detect_split_metric",
            table_fqn="analytics.detect_events",
            native_name="detect_events",
            dimensions=["event_date", "dimension.cluster"],
        )

        r = cls.client.post("/sessions", json={"goal": "detect HTTP test"})
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _time_scope(self, start: str = "2026-01-01", end: str = "2026-01-15") -> dict:
        return {
            "field": "event_date",
            "start": f"{start}T00:00:00Z" if "T" not in start else start,
            "end": f"{end}T00:00:00Z" if "T" not in end else end,
        }

    def _detect_payload(self, metric: str, **extra: object) -> dict:
        payload: dict = {
            "metric": _metric_ref(metric),
            "time_scope": self._time_scope(),
            "granularity": "day",
        }
        payload.update(extra)
        return payload

    def _source_artifact_id(self, metric: str, **extra: object) -> str:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json=self._detect_payload(metric, **extra),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["artifact_id"]

    def test_detect_missing_source_artifact_id_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"sensitivity": "balanced"},
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_rejects_removed_metric_field_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"metric": _metric_ref("http_detect_metric")},
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_unknown_source_artifact_returns_404_or_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"source_artifact_id": "artifact_that_does_not_exist_xyz"},
        )
        self.assertIn(r.status_code, {404, 422})

    def test_detect_rejects_removed_source_style_fields(self) -> None:
        metadata = self.client.app.state.services.metadata_store
        assert metadata is not None
        artifact_id = self._source_artifact_id("http_detect_metric")

        for removed_field in (
            "metric",
            "time_scope",
            "granularity",
            "filter",
            "dimension",
            "strategy",
        ):
            response = self.client.post(
                f"/sessions/{self.session_id}/intents/detect",
                json={"source_artifact_id": artifact_id, removed_field: "bad"},
            )

            self.assertEqual(response.status_code, 422, response.text)

    def test_detect_ready_metric_with_auxiliary_binding_returns_200(self) -> None:
        metadata = self.client.app.state.services.metadata_store
        metric_name = _seed_metadata(
            metadata,
            db_path=self.client.app.state.services.resolved_path,
            src_suffix="http_aux",
            metric_name="http_detect_aux_metric",
            table_fqn="analytics.uniform_events",
            native_name="uniform_events",
            binding_role="auxiliary",
            dimensions=["event_date", "dimension.cluster"],
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "source_artifact_id": self._source_artifact_id(metric_name),
                "sensitivity": "balanced",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertIn("artifact_id", result)
        self.assertEqual(result["artifact_family"], "candidate_set")
        self.assertIn("items", result["payload"])

    def test_observe_invalid_time_scope_for_detect_source_returns_422(self) -> None:
        """The source artifact construction rejects invalid windows before detect."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": self._time_scope(start="2026-02-21", end="2026-02-07"),
                "granularity": "day",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_invalid_grain_for_detect_source_returns_422(self) -> None:
        """Unsupported source granularity is rejected before detect."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": self._time_scope(start="2026-02-07", end="2026-03-08"),
                "granularity": "minute",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_success_empty_on_uniform_data(self) -> None:
        """Uniform watch_events data: detect returns 200 with total_candidate_count = 0."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "source_artifact_id": self._source_artifact_id("http_detect_metric"),
                "sensitivity": "balanced",
            },
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()["result"]
        self.assertIn("artifact_id", body)
        # uniform_events has the same number of rows per day per cluster → no candidates
        self.assertEqual(body["payload"]["items"], [])

    def test_detect_all_public_options_return_200(self) -> None:
        source_artifact_id = self._source_artifact_id(
            "http_detect_split_metric",
            filter={
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "cluster = 'alpha'"},
                ]
            },
        )
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "source_artifact_id": source_artifact_id,
                "sensitivity": "balanced",
                "limit": 1,
            },
        )

        self.assertEqual(r.status_code, 200, msg=r.text)
        items = r.json()["result"]["payload"]["items"]
        self.assertLessEqual(len(items), 1)

    def test_detect_dimension_array_removed_field_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "source_artifact_id": self._source_artifact_id("http_detect_split_metric"),
                "dimension": ["dimension.cluster"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_nonexistent_session_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_does_not_exist/intents/detect",
            json={
                "source_artifact_id": "artifact_source",
            },
        )
        self.assertEqual(r.status_code, 404)
