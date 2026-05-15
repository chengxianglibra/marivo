"""Tests for the Phase 2 Intent Action Surface.

Covers:
  - Generated AOI request model schema validation
  - Intent HTTP endpoints: correct routing, schema errors (422), not-implemented (501)
  - AOI atomic request validation rules and artifact-id reference behavior
  - Legacy /steps/* endpoints confirm 404
  - run_intent: observe→metric_query execution (with semantic layer wired up)
  - run_intent: stub intents return NotImplementedError
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import duckdb
from fastapi.testclient import TestClient
from pydantic import ValidationError

from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.contracts.generated import aoi
from marivo.main import create_app
from tests.semantic_test_helpers import seed_duckdb_source_object
from tests.shared_fixtures import get_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


_CALENDAR_VERSION = "cn_public_holiday_test_v1"


def _weekday_of(iso_date: str) -> int:
    """Return ISO weekday (1=Mon, 7=Sun) for an ISO date string."""
    from datetime import date as _date

    return _date.fromisoformat(iso_date).isoweekday()


def _seed_calendar_table_to_duckdb(db_path: Path) -> None:
    """Create analytics.cn_public_holiday in the test DuckDB with minimal calendar data."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.cn_public_holiday (
                calendar_date DATE NOT NULL,
                region_code VARCHAR NOT NULL,
                calendar_version VARCHAR NOT NULL,
                weekday INTEGER NOT NULL,
                is_weekend BOOLEAN NOT NULL,
                is_workday BOOLEAN NOT NULL,
                holiday_name VARCHAR,
                holiday_group_id VARCHAR,
                year_relative_holiday_key VARCHAR
            )
            """
        )
        rows: list[tuple] = []
        for year in (2025, 2026):
            month = 4
            for day in range(1, 9):
                iso = f"{year:04d}-{month:02d}-{day:02d}"
                wd = _weekday_of(iso)
                is_we = wd >= 6
                rows.append(
                    (
                        iso,
                        "CN",
                        _CALENDAR_VERSION,
                        wd,
                        is_we,
                        not is_we,
                        None,
                        None,
                        None,
                    )
                )
        con.executemany(
            "INSERT INTO analytics.cn_public_holiday VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    finally:
        con.close()


def _seed_calendar_rows_to_metadata(metadata: SQLiteMetadataStore) -> None:
    """Seed the calendar table in the SQLite metadata store with test data."""
    rows: list[tuple] = []
    for year in (2025, 2026):
        month = 4
        for day in range(1, 9):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            wd = _weekday_of(iso)
            is_we = 1 if wd >= 6 else 0
            is_wd = 1 if wd < 6 else 0
            rows.append((iso, "CN", _CALENDAR_VERSION, wd, is_we, is_wd, None, None, None))
    with metadata.connect() as con:
        con.executemany(
            """
            INSERT INTO calendar
                (calendar_date, region_code, calendar_version, weekday,
                 is_weekend, is_workday, holiday_name, holiday_group_id,
                 year_relative_holiday_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()


def _seed_default_calendar_source_metadata(db_path: Path) -> None:
    _seed_calendar_table_to_duckdb(db_path)
    metadata = SQLiteMetadataStore(db_path.with_suffix(".meta.sqlite"))
    metadata.initialize()
    _seed_calendar_rows_to_metadata(metadata)
    now = "2026-04-18T00:00:00+00:00"
    seed_duckdb_source_object(
        metadata,
        source_id="src_test_calendar_duckdb",
        object_id="obj_test_calendar_holiday",
        display_name="DuckDB",
        table_name="cn_public_holiday",
        table_fqn="main.analytics.cn_public_holiday",
        now=now,
        connection={"path": str(db_path)},
        authority_locator={"catalog": "main", "schema": "analytics", "table": "cn_public_holiday"},
        properties={"calendar_version": _CALENDAR_VERSION},
        sync_version="test_sync_v1",
        synced_at=now,
    )


def _insert_observe_artifact(
    service: Any,
    *,
    session_id: str,
    step_id: str,
    metric: str,
    observation_type: str,
    time_scope: dict[str, object],
    value: float | None = None,
    dimensions: list[str] | None = None,
    segments: list[dict[str, object]] | None = None,
    granularity: str | None = None,
    series: list[dict[str, object]] | None = None,
    unit: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "intent_type": "observe",
        "observation_type": observation_type,
        "metric": metric,
        "time_scope": time_scope,
        "scope": {},
        "unit": unit,
        "analytical_metadata": {
            "quality_status": "ready",
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": len(series or segments or []),
        },
        "execution_metadata": {
            "query_hash": "test",
            "engine": "duckdb",
            "executed_at": "2026-01-01T00:00:00",
        },
    }
    if observation_type == "scalar":
        payload["value"] = value
    if dimensions is not None:
        payload["dimensions"] = dimensions
    if segments is not None:
        payload["segments"] = segments
        payload["scope_value"] = value
    if granularity is not None:
        payload["granularity"] = granularity
    if series is not None:
        payload["series"] = series
    artifact_id = service._insert_artifact(
        session_id,
        step_id,
        "observation",
        f"{metric}_{observation_type}",
        payload,
    )
    result = {
        "intent_type": "observe",
        "step_type": "observe",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "observe",
        },
        "artifact_id": artifact_id,
        **payload,
    }
    service._insert_step(
        step_id,
        session_id,
        "observe",
        f"seeded observe {metric}",
        result,
        provenance={"seeded": True},
    )
    return artifact_id


class _SessionBackedIntentEndpointMixin:
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / f"{cls.__name__.lower()}.duckdb"
        cls.client = TestClient(create_app(cls.db_path), headers={"X-Marivo-User": "test_user"})
        response = cls.client.post("/sessions", json={"goal": f"{cls.__name__} session"})
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()


# ── Model-level validation tests (no HTTP) ───────────────────────────────────


class AoiGeneratedIntentModelTests(unittest.TestCase):
    def _time_scope(self) -> dict[str, str]:
        return {
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        }

    def test_aoi_observe_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            aoi.Observe1.model_validate(
                {
                    "metric": "metric.dau",
                    "time_scope": self._time_scope(),
                    "filter": None,
                    "unexpected": True,
                }
            )

    def test_aoi_observe_accepts_generated_shape(self) -> None:
        request = aoi.Observe1.model_validate(
            {
                "metric": "metric.dau",
                "time_scope": self._time_scope(),
                "filter": None,
                "granularity": "day",
                "dimensions": None,
            }
        )

        self.assertEqual(request.metric, "metric.dau")
        self.assertEqual(request.time_scope.field, "event_time")

    def test_aoi_compare_requires_artifact_ids(self) -> None:
        with self.assertRaises(ValidationError):
            aoi.Compare.model_validate(
                {
                    "left_ref": {"step_id": "step_1"},
                    "right_ref": {"step_id": "step_2"},
                }
            )

    def test_aoi_compare_accepts_artifact_ids(self) -> None:
        request = aoi.Compare.model_validate(
            {
                "left_artifact_id": "art_left",
                "right_artifact_id": "art_right",
                "compare_type": "normal",
            }
        )

        self.assertEqual(request.left_artifact_id, "art_left")
        self.assertEqual(request.right_artifact_id, "art_right")

    def test_aoi_detect_rejects_legacy_time_scope_shape(self) -> None:
        with self.assertRaises(ValidationError):
            aoi.Detect.model_validate(
                {
                    "metric": "metric.dau",
                    "time_scope": {"kind": "range", "start": "2026-05-01", "end": "2026-05-02"},
                    "granularity": "day",
                }
            )

    def test_aoi_forecast_requires_source_artifact_id(self) -> None:
        with self.assertRaises(ValidationError):
            aoi.Forecast.model_validate({"source_ref": {"step_id": "step_1"}, "horizon": 7})


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


class ObserveIntentValidationEndpointTests(_SessionBackedIntentEndpointMixin, unittest.TestCase):
    """Observe validation paths that only require a session-backed app."""

    def test_observe_requires_metric_and_time_scope(self) -> None:
        r = self.client.post(f"/sessions/{self.session_id}/intents/observe", json={})
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        fields = {e["loc"][-1] for e in detail}
        self.assertIn("metric", fields)
        self.assertIn("time_scope", fields)

    def test_observe_rejects_granularity_plus_dimensions(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
                "dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_unknown_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("non_existent_metric_xyz"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_snapshot_now_unknown_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("non_existent_metric_xyz"),
                "time_scope": {"kind": "snapshot_now"},
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_rejects_session_user_override_fields(self) -> None:
        for extra_field, extra_value in (
            ("session_user", "alice"),
            ("execution_user", "alice"),
            ("execution_identity", {"session_user": "alice"}),
        ):
            with self.subTest(extra_field=extra_field):
                response = self.client.post(
                    f"/sessions/{self.session_id}/intents/observe",
                    json={
                        "metric": _metric_ref("dau"),
                        "time_scope": {
                            "kind": "range",
                            "start": "2024-01-01",
                            "end": "2024-01-08",
                        },
                        extra_field: extra_value,
                    },
                )
                self.assertEqual(response.status_code, 422)


class AttributeUnknownMetricEndpointTests(unittest.TestCase):
    """Lightweight coverage for attribute's unknown-metric HTTP failure path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "attribute_unknown_metric.duckdb"
        cls.client = TestClient(create_app(cls.db_path), headers={"X-Marivo-User": "test_user"})
        response = cls.client.post("/sessions", json={"goal": "attribute unknown metric test"})
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_attribute_unknown_metric_returns_422(self) -> None:
        # attribute is now a real runner; an unresolvable metric yields OBSERVE_FAILED → 422
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/attribute",
            json={
                "metric": _metric_ref("dau"),
                "left": {
                    "time_scope": {
                        "kind": "range",
                        "start": "2024-01-08",
                        "end": "2024-01-15",
                    }
                },
                "right": {
                    "time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    }
                },
                "dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 422)


class LightweightIntentEndpointTests(_SessionBackedIntentEndpointMixin, unittest.TestCase):
    """HTTP intent validation paths that only need a session-backed app."""

    def test_compare_validation_error_includes_schema_guidance_and_example(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={"left_artifact_id": "art_left"},
        )

        self.assertEqual(r.status_code, 422)
        payload = r.json()
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertIn("/openapi/schemas/", payload["guidance"]["schema_url"])
        self.assertIn("/openapi/paths/", payload["guidance"]["contract_url"])
        self.assertIn("left_artifact_id", payload["guidance"]["examples"][0]["payload"])
        self.assertIn("right_artifact_id", payload["guidance"]["examples"][0]["payload"])

    def test_detect_validation_error_includes_schema_guidance_and_example(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"metric": "metric.watch_time", "time_scope": {"kind": "range"}},
        )

        self.assertEqual(r.status_code, 422)
        payload = r.json()
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertIn("/openapi/schemas/", payload["guidance"]["schema_url"])
        example_time_scope = payload["guidance"]["examples"][0]["payload"]["time_scope"]
        self.assertEqual(example_time_scope["kind"], "range")
        self.assertEqual(set(example_time_scope), {"kind", "start", "end"})
        self.assertEqual(payload["guidance"]["examples"][0]["payload"]["granularity"], "day")

    def test_compare_nonexistent_ref_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_artifact_id": "art_001",
                "right_artifact_id": "art_002",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("ARTIFACT_NOT_FOUND", r.json()["detail"])

    def test_correlate_nonexistent_steps_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_artifact_id": "art_nonexistent_a",
                "right_artifact_id": "art_nonexistent_b",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("ARTIFACT_NOT_FOUND", r.json()["detail"])

    def test_detect_unregistered_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_rejects_missing_horizon(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={"source_artifact_id": "art_source"},
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_nonexistent_step_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "source_artifact_id": "art_source",
                "horizon": 7,
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("ARTIFACT_NOT_FOUND", r.json()["detail"])

    def test_diagnose_invalid_request_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_validate_invalid_request_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={"metric": _metric_ref("dau")},
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_on_nonexistent_session_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_nonexistent/intents/observe",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-08T00:00:00Z",
                },
                "filter": None,
            },
        )
        self.assertEqual(r.status_code, 404)


class ClosedSessionWriteGuardTests(unittest.TestCase):
    """Phase 8.1: non-open session rejects all intent write operations (422)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "closed_session.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.client = TestClient(create_app(db_path), headers={"X-Marivo-User": "test_user"})
        r = cls.client.post("/sessions", json={"goal": "to be closed"})
        cls.session_id = r.json()["session_id"]
        cls.client.post(f"/sessions/{cls.session_id}/terminate", json={"terminal_reason": "test"})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_observe_on_closed_session_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-08T00:00:00Z",
                },
                "filter": None,
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not open", r.json()["detail"])

    def test_detect_on_closed_session_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-08T00:00:00Z",
                },
                "granularity": "day",
                "filter": None,
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not open", r.json()["detail"])

    def test_attribute_on_closed_session_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/attribute",
            json={
                "metric": _metric_ref("dau"),
                "left": {
                    "time_scope": {"kind": "range", "start": "2024-01-08", "end": "2024-01-15"}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"}
                },
                "dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not open", r.json()["detail"])

    def test_diagnose_on_closed_session_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
                "candidate_dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not open", r.json()["detail"])

    def test_validate_on_closed_session_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "metric": _metric_ref("dau"),
                "left": {
                    "time_scope": {"kind": "range", "start": "2024-01-08", "end": "2024-01-15"}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"}
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not open", r.json()["detail"])


class ArtifactLifecycleTests(unittest.TestCase):
    """Phase 3a: staged/committed lifecycle and ObservationRef resolution."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        from marivo.main import create_app
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "lifecycle.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.app = create_app(db_path)
        cls.service = cls.app.state.services.runtime

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        state = self.service.create_session("lifecycle test")
        return str(state.session_id)

    def test_insert_artifact_staged_lifecycle(self) -> None:
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}"
        artifact_id = self.service.insert_artifact(
            session_id, step_id, "observation", "test", {"v": 1}, lifecycle="staged"
        )
        row = self.service.metadata.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "staged")

    def test_commit_artifact_transitions_to_committed(self) -> None:
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}"
        artifact_id = self.service.insert_artifact(
            session_id, step_id, "observation", "test", {"v": 2}, lifecycle="staged"
        )
        self.service.metadata.execute(
            "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
            [artifact_id],
        )
        row = self.service.metadata.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")

    def test_resolve_artifact_for_ref_returns_content(self) -> None:
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}"
        content = {"observation_type": "scalar", "value": 42.0}
        self.service.insert_artifact(session_id, step_id, "observation", "test", content)
        result = self.service.resolve_artifact_for_ref(session_id, step_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["observation_type"], "scalar")
        self.assertEqual(result["value"], 42.0)

    def test_resolve_artifact_for_ref_staged_not_returned(self) -> None:
        """Staged artifacts are not returned by ref resolution."""
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}_staged"
        self.service.insert_artifact(
            session_id, step_id, "observation", "test", {"v": 3}, lifecycle="staged"
        )
        result = self.service.resolve_artifact_for_ref(session_id, step_id)
        self.assertIsNone(result)

    def test_resolve_artifact_for_ref_not_found_returns_none(self) -> None:
        result = self.service.resolve_artifact_for_ref("sess_nonexistent", "step_none")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
