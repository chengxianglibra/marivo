from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
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


def _bundle_result(bundle: dict[str, object]) -> dict[str, object]:
    return bundle["result"]  # type: ignore[index]


def _bundle_product_metadata(bundle: dict[str, object]) -> dict[str, object]:
    return bundle["product_metadata"]  # type: ignore[index]


# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC = "diag_revenue"

# Scan window: 2024-03-01 to 2024-03-11 (10 days)
_SCAN_START = "2024-03-01"
_SCAN_END = "2024-03-11"

# Anomaly spike on 2024-03-05 (channel A = 700, B = 100, C = 100 → total 900)
# Normal days: all channels = 100 → total 300
# z-score of 900 with balanced threshold = 2.0: z ≈ 3.0 (above threshold → candidate)
_ANOMALY_DATE = "2024-03-05"
_ANOMALY_DATE_END = "2024-03-06"  # exclusive

# Baseline for 1-day candidate: previous adjacent day
_BASELINE_DATE = "2024-03-04"
_BASELINE_DATE_END = "2024-03-05"  # exclusive

_CHANNELS = ["A", "B", "C"]
_NORMAL_VALUE = 100.0
_ANOMALY_CHANNEL = "A"
_ANOMALY_VALUE = 700.0


def _detect_time_scope(start: str = _SCAN_START, end: str = _SCAN_END) -> dict[str, str]:
    return {"field": "event_date", "start": start, "end": end}


def _aoi_detect_time_scope(start: str = _SCAN_START, end: str = _SCAN_END) -> dict[str, str]:
    def _as_utc_datetime(value: str) -> str:
        if "T" in value:
            return value if value.endswith("Z") else f"{value}Z"
        return f"{value}T00:00:00Z"

    return {
        "field": "event_date",
        "start": _as_utc_datetime(start),
        "end": _as_utc_datetime(end),
    }


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_diag_table(db_path: Path) -> None:
    """Copy the shared seeded analytics.diag_events fixture into place."""
    get_named_seeded_duckdb_path(db_path, "diagnose_intent")


def _seed_metadata(meta: SQLiteMetadataStore, db_path: str | Path) -> None:
    """Insert minimal metadata so diagnose can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = "src_diagtest01"
    obj_id = "obj_diagtest01"
    met_id = "met_diagtest01"
    map_id = "map_diagtest01"

    seed_duckdb_source_object(
        meta,
        source_id=src_id,
        object_id=obj_id,
        display_name="Diag Test Source",
        table_name="diag_events",
        table_fqn="analytics.diag_events",
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC,
        display_name=_METRIC,
        grain="day",
        dimensions=["event_date", "channel"],
        definition_sql="SUM(value)",
        measure_type="sum",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC,
        carrier_locator="analytics.diag_events",
        source_object_ref=obj_id,
        surface_name="value",
        dimension_names=["event_date", "channel"],
    )
    # Datasource IS the engine; no separate mapping needed


class DiagnoseHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_http.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_http.meta.sqlite"

        _seed_diag_table(db_path)
        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()
        _seed_metadata(metadata, db_path)

        app = create_app(metadata_store=metadata, analytics_engine=analytics)
        cls.client = TestClient(app, headers={"X-Marivo-User": "test_user"})

        # Create a session to reuse
        resp = cls.client.post("/sessions", json={"goal": "diag http test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_valid_diagnose_returns_200_with_bundle(self) -> None:
        """Valid diagnose request returns 200 with result_type='diagnosis_bundle'."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _metric_ref(_METRIC),
                "time_scope": _aoi_detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
                "followup_limit": 1,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result"]["bundle_type"], "diagnosis_bundle")

    def test_missing_candidate_dimensions_returns_422(self) -> None:
        """Missing required candidate_dimensions returns 422."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _metric_ref(_METRIC),
                "time_scope": _aoi_detect_time_scope(),
                "granularity": "day",
                # no candidate_dimensions
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_unknown_session_returns_404(self) -> None:
        """Unknown session returns 404."""
        resp = self.client.post(
            "/sessions/sess_nonexistent/intents/diagnose",
            json={
                "metric": _metric_ref(_METRIC),
                "time_scope": _aoi_detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
            },
        )
        self.assertEqual(resp.status_code, 404)
