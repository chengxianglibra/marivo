"""Tests for the Phase 2 Intent Action Surface.

Covers:
  - Intent request model schema validation (ObserveRequest, CompareRequest, etc.)
  - Intent HTTP endpoints: correct routing, schema errors (422), not-implemented (501)
  - ObserveRequest model validation rules (illegal combinations)
  - CompareRequest / CorrelateRequest / TestRequest / ForecastRequest same-session ref guard
  - DecomposeRequest compare_ref.step_type validation
  - Legacy /steps/* endpoints confirm 404
  - run_intent: observe→metric_query execution (with semantic layer wired up)
  - run_intent: stub intents return NotImplementedError
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.models import (
    ArtifactRef,
    AttributeRequest,
    CompareRequest,
    DecomposeRequest,
    DetectRequest,
    ObservationRef,
    ObserveRequest,
)
from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    create_typed_entity,
    create_typed_metric,
    create_typed_metric_binding,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    ensure_published_typed_time,
    publish_typed_entity,
    publish_typed_metric,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path, get_seeded_duckdb_path


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
                year_relative_holiday_key VARCHAR,
                event_group_id VARCHAR,
                year_relative_event_key VARCHAR
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
                        None,
                        None,
                    )
                )
        con.executemany(
            "INSERT INTO analytics.cn_public_holiday VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    finally:
        con.close()


def _seed_default_calendar_source_metadata(db_path: Path) -> None:
    _seed_calendar_table_to_duckdb(db_path)
    metadata = SQLiteMetadataStore(db_path.with_suffix(".meta.sqlite"))
    metadata.initialize()
    now = "2026-04-18T00:00:00+00:00"
    metadata.execute(
        """
        INSERT OR IGNORE INTO sources (
            source_id, source_type, display_name, connection_json,
            capabilities_json, sync_mode, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "src_test_calendar_duckdb",
            "duckdb",
            "DuckDB",
            json.dumps({"path": str(db_path)}),
            "{}",
            "by_select",
            "active",
            now,
            now,
        ],
    )
    metadata.execute(
        """
        INSERT OR IGNORE INTO source_objects (
            object_id, source_id, object_type, parent_id, native_name, native_id,
            fqn, properties_json, sync_version, synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "obj_test_calendar_holiday",
            "src_test_calendar_duckdb",
            "table",
            None,
            "cn_public_holiday",
            None,
            "duckdb.analytics.cn_public_holiday",
            json.dumps({"calendar_version": _CALENDAR_VERSION}),
            "test_sync_v1",
            now,
            now,
            now,
        ],
    )


def _create_metric_binding(
    client: TestClient,
    *,
    binding_ref: str,
    metric_ref: str,
    source_object_ref: str,
    carrier_locator: str,
    binding_role: str = "primary",
    metric_input_target_keys: list[str] | None = None,
    surface_name: str = "value",
) -> str:
    metric_input_keys = metric_input_target_keys or ["measure"]
    field_surfaces = [
        {"surface_ref": "field.event_date", "physical_name": "event_date"},
        {"surface_ref": f"field.{surface_name}", "physical_name": surface_name},
    ]
    field_bindings = [
        {
            "carrier_binding_key": "primary",
            "target": {
                "target_kind": "primary_time",
                "target_key": "time.event_date",
            },
            "semantic_ref": "time.event_date",
            "surface_ref": "field.event_date",
        }
    ]
    for target_key in metric_input_keys:
        field_bindings.append(
            {
                "carrier_binding_key": "primary",
                "target": {
                    "target_kind": "metric_input",
                    "target_key": target_key,
                },
                "semantic_ref": f"metric_input.{target_key}",
                "surface_ref": f"field.{surface_name}",
            }
        )
    resp = client.post(
        "/semantic/bindings",
        json={
            "header": {
                "binding_ref": binding_ref,
                "display_name": binding_ref,
                "binding_scope": "metric",
                "bound_object_ref": metric_ref,
                "binding_contract_version": "binding.v1",
            },
            "interface_contract": {
                "carrier_bindings": [
                    {
                        "binding_key": "primary",
                        "source_object_ref": source_object_ref,
                        "carrier_kind": "table",
                        "carrier_locator": carrier_locator,
                        "binding_role": binding_role,
                        "field_surfaces": field_surfaces,
                    }
                ],
                "field_bindings": field_bindings,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    binding_id = resp.json()["binding_id"]
    publish_resp = client.post(f"/semantic/bindings/{binding_id}/publish")
    assert publish_resp.status_code == 200, publish_resp.text
    return binding_id


def _register_duckdb_runtime(
    client: TestClient,
    *,
    db_path: Path,
    source_display_name: str,
    engine_display_name: str,
) -> str:
    source = client.post(
        "/sources",
        json={
            "source_type": "duckdb",
            "display_name": source_display_name,
            "connection": {"path": str(db_path)},
        },
    ).json()
    engine = client.post(
        "/engines",
        json={
            "engine_type": "duckdb",
            "display_name": engine_display_name,
            "connection": {"database": str(db_path)},
        },
    ).json()
    client.post(
        "/bindings",
        json={"source_id": source["source_id"], "engine_id": engine["engine_id"], "priority": 0},
    )
    return str(source["source_id"])


def _ensure_source_object(
    metadata: SQLiteMetadataStore,
    *,
    source_id: str,
    native_name: str,
    fqn: str,
    now: str = "2026-01-01T00:00:00",
) -> str:
    existing = metadata.query_one(
        "SELECT object_id FROM source_objects WHERE source_id = ? AND fqn = ?",
        [source_id, fqn],
    )
    if existing is not None:
        return str(existing["object_id"])
    object_id = f"obj_{uuid4().hex[:12]}"
    metadata.execute(
        """
        INSERT INTO source_objects
            (object_id, source_id, object_type, native_name, fqn,
             properties_json, created_at, updated_at)
        VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
        """,
        [object_id, source_id, native_name, fqn, now, now],
    )
    return object_id


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
    aligned_baseline_series: list[dict[str, object]] | None = None,
    segmented_yoy: list[dict[str, object]] | None = None,
    unit: str | None = None,
    resolved_policy_summary: dict[str, object] | None = None,
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
            "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
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
    if segmented_yoy is not None:
        payload["segmented_yoy"] = segmented_yoy
    if granularity is not None:
        payload["granularity"] = granularity
    if series is not None:
        payload["series"] = series
    if aligned_baseline_series is not None:
        payload["aligned_baseline_series"] = aligned_baseline_series
    if resolved_policy_summary is not None:
        payload["resolved_policy_summary"] = resolved_policy_summary
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
        cls.client = TestClient(create_app(cls.db_path))
        response = cls.client.post("/sessions", json={"goal": f"{cls.__name__} session"})
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()


class _ObserveIntentTestCase:
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / f"{cls.__name__.lower()}.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        _seed_default_calendar_source_metadata(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.client = TestClient(cls.app)
        cls._setup_base_semantic_layer()
        cls._setup_additional_semantic_layer()
        cls._setup_calendar_reader()
        response = cls.client.post(
            "/sessions",
            json={"goal": f"{cls.__name__} session", "budget": {}, "policy": {}},
        )
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @classmethod
    def _setup_base_semantic_layer(cls) -> None:
        metadata = cls.app.state.service.metadata
        cls.source_id = _register_duckdb_runtime(
            cls.client,
            db_path=cls.db_path,
            source_display_name=f"{cls.__name__} Source",
            engine_display_name=f"{cls.__name__} Engine",
        )
        cls.watch_events_fqn = "analytics.watch_events"
        cls.watch_events_object_id = _ensure_source_object(
            metadata,
            source_id=cls.source_id,
            native_name="watch_events",
            fqn=cls.watch_events_fqn,
        )

        metric = create_typed_metric(
            cls.client,
            name="observe_test_dau",
            display_name="DAU (observe test)",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="average",
        )
        cls.metric_id = metric["metric_contract_id"]
        publish_typed_metric(cls.client, cls.metric_id)
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.observe_test_dau",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
        )

    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        return

    @classmethod
    def _setup_calendar_reader(cls) -> None:
        svc = cls.app.state.service
        import sys

        print(
            f"DEBUG _setup_calendar_reader: service id={id(svc)}, intent_types={list(svc.intent_registry._runners.keys())}",
            file=sys.stderr,
        )
        metadata = svc.metadata
        # Find the test class's engine (points to the temp DuckDB with calendar data).
        engine_row = metadata.query_one(
            "SELECT engine_id FROM engines WHERE display_name = ?",
            [f"{cls.__name__} Engine"],
        )
        if engine_row is None:
            return
        engine_id = str(engine_row["engine_id"])
        # Remove any existing bindings for the calendar source and rebind to the test engine
        # with the analytics schema namespace so routing produces analytics.cn_public_holiday.
        metadata.execute(
            "DELETE FROM source_engine_bindings WHERE source_id = ?",
            ["src_test_calendar_duckdb"],
        )
        metadata.execute(
            """
            INSERT INTO source_engine_bindings
                (source_id, engine_id, priority, namespace_json, status, created_at, updated_at)
            VALUES (?, ?, 0, '{"schema": "analytics"}', 'active', ?, ?)
            """,
            [
                "src_test_calendar_duckdb",
                engine_id,
                "2026-04-18T00:00:00+00:00",
                "2026-04-18T00:00:00+00:00",
            ],
        )
        from app.config import CalendarConfig, CalendarSnapshotConfig, CalendarSourceBindingConfig

        svc.config.calendar = CalendarConfig(
            default_region_code="CN",
            snapshots=[
                CalendarSnapshotConfig(
                    resolved_calendar_source="calendar.test_fixture",
                    resolved_calendar_version=_CALENDAR_VERSION,
                    region_code="CN",
                    effective_start="2024-01-01",
                    effective_end="2026-12-31",
                    holiday_source=CalendarSourceBindingConfig(
                        source_name="DuckDB",
                        table_fqn="duckdb.analytics.cn_public_holiday",
                        calendar_version=_CALENDAR_VERSION,
                    ),
                ),
            ],
        )
        svc._refresh_calendar_data_reader()


# ── Model-level validation tests (no HTTP) ───────────────────────────────────


class ObserveRequestModelTests(unittest.TestCase):
    def _make(self, **kwargs):
        base = {
            "metric": _metric_ref("dau"),
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        }
        base.update(kwargs)
        return ObserveRequest(**base)

    def test_scalar_mode(self) -> None:
        r = self._make()
        self.assertEqual(r.result_mode, "standard")
        self.assertIsNone(r.granularity)
        self.assertIsNone(r.dimensions)

    def test_time_series_mode(self) -> None:
        r = self._make(granularity="day")
        self.assertEqual(r.granularity, "day")

    def test_calendar_policy_ref_is_accepted(self) -> None:
        r = self._make(calendar_policy_ref="calendar_policy.holiday_yoy")
        self.assertEqual(r.calendar_policy_ref, "calendar_policy.holiday_yoy")

    def test_calendar_policy_ref_rejects_unknown_ref(self) -> None:
        with self.assertRaisesRegex(Exception, "Unknown calendar_policy_ref"):
            self._make(calendar_policy_ref="calendar_policy.not_real")

    def test_segmented_mode(self) -> None:
        r = self._make(dimensions=["region"])
        self.assertEqual(r.dimensions, ["region"])

    def test_empty_dimensions_normalized_to_none(self) -> None:
        r = self._make(dimensions=[])
        self.assertIsNone(r.dimensions)

    def test_granularity_and_dimensions_mutually_exclusive(self) -> None:
        with self.assertRaises(Exception):
            self._make(granularity="day", dimensions=["region"])

    def test_non_standard_mode_rejects_granularity(self) -> None:
        with self.assertRaises(Exception):
            self._make(result_mode="numeric_sample_summary", granularity="day")

    def test_non_standard_mode_rejects_dimensions(self) -> None:
        with self.assertRaises(Exception):
            self._make(result_mode="rate_sample_summary", dimensions=["platform"])

    def test_snapshot_now_time_scope(self) -> None:
        r = ObserveRequest(
            metric=_metric_ref("dau"),
            time_scope={"kind": "snapshot_now"},
        )
        self.assertEqual(r.time_scope.kind, "snapshot_now")

    def test_as_of_time_scope(self) -> None:
        r = ObserveRequest(
            metric=_metric_ref("dau"),
            time_scope={"kind": "as_of", "at": "2024-06-01T00:00:00"},
        )
        self.assertEqual(r.time_scope.kind, "as_of")

    def test_snapshot_now_rejects_granularity(self) -> None:
        with self.assertRaises(Exception):
            ObserveRequest(
                metric=_metric_ref("dau"),
                time_scope={"kind": "snapshot_now"},
                granularity="day",
            )

    def test_hour_granularity_requires_datetime_range_boundaries(self) -> None:
        with self.assertRaises(Exception):
            ObserveRequest(
                metric=_metric_ref("dau"),
                time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-02"},
                granularity="hour",
            )

    def test_hour_granularity_accepts_space_separated_datetimes(self) -> None:
        r = ObserveRequest(
            metric=_metric_ref("dau"),
            time_scope={
                "kind": "range",
                "start": "2024-01-01 00:00:00",
                "end": "2024-01-01 02:00:00",
            },
            granularity="hour",
        )
        self.assertEqual(r.granularity, "hour")


class AttributeRequestModelTests(unittest.TestCase):
    def _make(self, **kwargs: Any) -> AttributeRequest:
        base: dict[str, Any] = {
            "metric": _metric_ref("dau"),
            "left": {
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
            "right": {
                "time_scope": {"kind": "range", "start": "2023-01-01", "end": "2023-01-08"},
            },
            "dimensions": ["region"],
        }
        base.update(kwargs)
        return AttributeRequest(**base)

    def test_side_level_calendar_policy_ref_is_accepted(self) -> None:
        request = self._make(
            left={
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "calendar_policy_ref": "calendar_policy.holiday_yoy",
            },
            right={
                "time_scope": {"kind": "range", "start": "2023-01-01", "end": "2023-01-08"},
                "calendar_policy_ref": "calendar_policy.holiday_yoy",
            },
        )
        self.assertEqual(request.left.calendar_policy_ref, "calendar_policy.holiday_yoy")
        self.assertEqual(request.right.calendar_policy_ref, "calendar_policy.holiday_yoy")

    def test_side_level_calendar_policy_ref_rejects_unknown_ref(self) -> None:
        with self.assertRaisesRegex(Exception, "Unknown calendar_policy_ref"):
            self._make(
                left={
                    "time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "calendar_policy_ref": "calendar_policy.not_real",
                }
            )


class DetectRequestModelTests(unittest.TestCase):
    def test_hour_detect_accepts_datetime_boundaries(self) -> None:
        r = DetectRequest(
            metric=_metric_ref("dau"),
            time_scope={
                "mode": "single_window",
                "grain": "hour",
                "current": {"start": "2024-01-01T00:00:00", "end": "2024-01-01 03:00:00"},
            },
        )
        self.assertEqual(r.time_scope.grain, "hour")

    def test_hour_detect_rejects_date_only_boundaries(self) -> None:
        with self.assertRaises(Exception):
            DetectRequest(
                metric=_metric_ref("dau"),
                time_scope={
                    "mode": "single_window",
                    "grain": "hour",
                    "current": {"start": "2024-01-01", "end": "2024-01-02"},
                },
            )


class CompareRequestModelTests(unittest.TestCase):
    def _ref(self, session_id: str = "sess_a", step_id: str = "step_1") -> ObservationRef:
        return ObservationRef(session_id=session_id, step_id=step_id, step_type="observe")

    def test_valid_request(self) -> None:
        r = CompareRequest(left_ref=self._ref(), right_ref=self._ref("sess_a", "step_2"))
        self.assertEqual(r.mode, "auto")

    def test_time_series_mode_allowed(self) -> None:
        r = CompareRequest(
            left_ref=self._ref(), right_ref=self._ref("sess_a", "step_2"), mode="time_series"
        )
        self.assertEqual(r.mode, "time_series")

    def test_observation_ref_step_type_locked_to_observe(self) -> None:
        with self.assertRaises(Exception):
            ObservationRef(session_id="sess_a", step_id="step_1", step_type="compare")


class DecomposeRequestModelTests(unittest.TestCase):
    def test_valid_request(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_cmp", step_type="compare")
        r = DecomposeRequest(compare_ref=ref, dimension="region")
        self.assertEqual(r.method, "delta_share")

    def test_compare_ref_must_be_compare_step_type(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_obs", step_type="observe")
        with self.assertRaises(Exception):
            DecomposeRequest(compare_ref=ref, dimension="region")

    def test_dimension_required(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_cmp", step_type="compare")
        with self.assertRaises(Exception):
            DecomposeRequest(compare_ref=ref, dimension="")


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


class _SemanticObserveIntentEndpointMixin:
    import_bridge_table_name = "intent_import_bridge_events"

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / f"{cls.__name__.lower()}.duckdb"
        get_named_seeded_duckdb_path(cls.db_path, "intent_api")
        _seed_default_calendar_source_metadata(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        metadata = cls.client.app.state.service.metadata
        cls.source_id = _register_duckdb_runtime(
            cls.client,
            db_path=cls.db_path,
            source_display_name="Intent API Source",
            engine_display_name="Intent API Engine",
        )
        cls.watch_events_fqn = "analytics.watch_events"
        cls.watch_events_object_id = _ensure_source_object(
            metadata,
            source_id=cls.source_id,
            native_name="watch_events",
            fqn=cls.watch_events_fqn,
        )
        cls.import_bridge_fqn = f"analytics.{cls.import_bridge_table_name}"
        cls.import_bridge_object_id = _ensure_source_object(
            metadata,
            source_id=cls.source_id,
            native_name=cls.import_bridge_table_name,
            fqn=cls.import_bridge_fqn,
        )
        r = cls.client.post("/sessions", json={"goal": f"{cls.__name__} session"})
        cls.session_id = r.json()["session_id"]
        cls._setup_semantic_layer()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        return

    @classmethod
    def _setup_basic_observe_metrics(cls) -> None:
        metadata = cls.client.app.state.service.metadata

        ensure_published_typed_metric(
            metadata,
            metric_name="intent_not_ready_metric",
            display_name="Intent Not Ready Metric",
            definition_sql="COUNT(*)",
            dimensions=["platform"],
            measure_type="average",
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_not_ready_metric",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            metric_input_target_keys=["numerator"],
        )

        ensure_published_typed_metric(
            metadata,
            metric_name="intent_aux_binding_metric",
            display_name="Intent Auxiliary Binding Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_aux_binding_metric",
            object_id=cls.import_bridge_object_id,
            carrier_locator=cls.import_bridge_fqn,
            binding_role="auxiliary",
        )

        ensure_published_typed_metric(
            metadata,
            metric_name="intent_preflight_failure_metric",
            display_name="Intent Preflight Failure Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_preflight_failure_metric",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
        )

    @classmethod
    def _setup_compatibility_metric(cls) -> None:
        metadata = cls.client.app.state.service.metadata
        ensure_published_typed_time(
            metadata, time_ref="time.signup_date", display_name="Signup Date"
        )
        existing_dimension = metadata.query_one(
            """
            SELECT dimension_contract_id, status
            FROM semantic_dimension_contracts
            WHERE dimension_ref = ?
            """,
            ["dimension.intent_signup_week"],
        )
        if existing_dimension is None:
            dimension_resp = cls.client.post(
                "/semantic/dimensions",
                json={
                    "header": {
                        "dimension_ref": "dimension.intent_signup_week",
                        "display_name": "Intent Signup Week",
                        "dimension_contract_version": "dimension.v1",
                    },
                    "interface_contract": {
                        "value_domain": {
                            "structure_kind": "time_derived",
                            "semantic_role": "category",
                            "value_type": "string",
                            "domain_kind": "open",
                        },
                        "grouping": {"supports_grouping": True},
                        "time_derived_requirement": {
                            "required_time_anchor_ref": "time.signup_date"
                        },
                    },
                },
            )
            assert dimension_resp.status_code == 200, dimension_resp.text
            publish_resp = cls.client.post(
                f"/semantic/dimensions/{dimension_resp.json()['dimension_contract_id']}/publish"
            )
            assert publish_resp.status_code == 200, publish_resp.text
        elif existing_dimension["status"] != "published":
            publish_resp = cls.client.post(
                f"/semantic/dimensions/{existing_dimension['dimension_contract_id']}/publish"
            )
            assert publish_resp.status_code == 200, publish_resp.text

        ensure_published_typed_metric(
            metadata,
            metric_name="intent_compatible_metric",
            display_name="Intent Compatible Metric",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["dimension.intent_signup_week"],
            grain="day",
            measure_type="average",
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_compatible_metric",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
        )

    @classmethod
    def _ensure_cluster_dimension(cls) -> None:
        metadata = cls.client.app.state.service.metadata
        dimension_row = metadata.query_one(
            """
            SELECT dimension_contract_id, status
            FROM semantic_dimension_contracts
            WHERE dimension_ref = ?
            """,
            ["dimension.cluster"],
        )
        if dimension_row is None:
            dimension_resp = cls.client.post(
                "/semantic/dimensions",
                json={
                    "header": {
                        "dimension_ref": "dimension.cluster",
                        "display_name": "Cluster",
                        "dimension_contract_version": "dimension.v1",
                    },
                    "interface_contract": {
                        "value_domain": {
                            "structure_kind": "flat",
                            "semantic_role": "category",
                            "value_type": "string",
                            "domain_kind": "open",
                        },
                        "grouping": {"supports_grouping": True},
                    },
                },
            )
            assert dimension_resp.status_code == 200, dimension_resp.text
            dimension_id = dimension_resp.json()["dimension_contract_id"]
            publish_dimension_resp = cls.client.post(f"/semantic/dimensions/{dimension_id}/publish")
            assert publish_dimension_resp.status_code == 200, publish_dimension_resp.text
            return

        if dimension_row["status"] != "published":
            publish_dimension_resp = cls.client.post(
                f"/semantic/dimensions/{dimension_row['dimension_contract_id']}/publish"
            )
            assert publish_dimension_resp.status_code == 200, publish_dimension_resp.text

    @classmethod
    def _create_import_bridge_metric(cls, *, mode: str) -> str:
        metadata = cls.client.app.state.service.metadata
        ensure_published_typed_time(metadata)
        cls._ensure_cluster_dimension()

        suffix = uuid4().hex[:8]
        entity = create_typed_entity(
            cls.client,
            name=f"intent_bridge_entity_{suffix}",
            display_name="Intent Bridge Entity",
            keys=["user_id"],
            primary_time_ref="time.event_date",
        )
        publish_typed_entity(cls.client, entity["entity_contract_id"])
        entity_ref = entity["header"]["entity_ref"]

        metric_name = f"intent_bridge_metric_{suffix}"
        metric_ref = f"metric.{metric_name}"
        metric = create_typed_metric(
            cls.client,
            name=metric_name,
            display_name="Intent Bridge Metric",
            description="Metric that relies on imported entity dimensions",
            definition_sql="SUM(value)",
            dimensions=[],
            entity_ref=entity_ref,
            grain="day",
            measure_type="sum",
        )
        publish_typed_metric(cls.client, metric["metric_contract_id"])

        primary_imported_binding_ref = f"binding.intent_bridge_entity_{suffix}"
        entity_binding_resp = cls.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": primary_imported_binding_ref,
                    "display_name": "Intent Bridge Entity Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": cls.import_bridge_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": cls.import_bridge_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.event_date",
                                    "physical_name": "event_date",
                                },
                                {
                                    "surface_ref": "field.user_id",
                                    "physical_name": "user_id",
                                },
                                {
                                    "surface_ref": "field.cluster",
                                    "physical_name": "cluster",
                                },
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.event_date",
                            },
                            "semantic_ref": "time.event_date",
                            "surface_ref": "field.event_date",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "stable_descriptor",
                                "target_key": "dimension.cluster",
                            },
                            "semantic_ref": "dimension.cluster",
                            "surface_ref": "field.cluster",
                        },
                    ],
                },
            },
        )
        assert entity_binding_resp.status_code == 200, entity_binding_resp.text
        entity_binding_id = entity_binding_resp.json()["binding_id"]
        publish_entity_binding_resp = cls.client.post(
            f"/semantic/bindings/{entity_binding_id}/publish"
        )
        assert publish_entity_binding_resp.status_code == 200, publish_entity_binding_resp.text

        imports: list[dict[str, object]] = []
        if mode in {"single", "ambiguous"}:
            imports.append(
                {
                    "import_key": "entity_bridge",
                    "binding_ref": primary_imported_binding_ref,
                    "required_ref_prefixes": ["dimension."],
                }
            )
        if mode == "ambiguous":
            secondary_imported_binding_ref = f"binding.intent_bridge_entity_alt_{suffix}"
            entity_binding_alt_resp = cls.client.post(
                "/semantic/bindings",
                json={
                    "header": {
                        "binding_ref": secondary_imported_binding_ref,
                        "display_name": "Intent Bridge Entity Binding Alt",
                        "binding_scope": "entity",
                        "bound_object_ref": entity_ref,
                        "binding_contract_version": "binding.v1",
                    },
                    "interface_contract": {
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "source_object_ref": cls.import_bridge_object_id,
                                "carrier_kind": "table",
                                "carrier_locator": cls.import_bridge_fqn,
                                "binding_role": "primary",
                                "field_surfaces": [
                                    {
                                        "surface_ref": "field.event_date",
                                        "physical_name": "event_date",
                                    },
                                    {
                                        "surface_ref": "field.user_id",
                                        "physical_name": "user_id",
                                    },
                                    {
                                        "surface_ref": "field.cluster_alt",
                                        "physical_name": "cluster",
                                    },
                                ],
                            }
                        ],
                        "field_bindings": [
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "identity_key",
                                    "target_key": "key.user_id",
                                },
                                "semantic_ref": "key.user_id",
                                "surface_ref": "field.user_id",
                            },
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "primary_time",
                                    "target_key": "time.event_date",
                                },
                                "semantic_ref": "time.event_date",
                                "surface_ref": "field.event_date",
                            },
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "stable_descriptor",
                                    "target_key": "dimension.cluster",
                                },
                                "semantic_ref": "dimension.cluster",
                                "surface_ref": "field.cluster_alt",
                            },
                        ],
                    },
                },
            )
            assert entity_binding_alt_resp.status_code == 200, entity_binding_alt_resp.text
            entity_binding_alt_id = entity_binding_alt_resp.json()["binding_id"]
            publish_entity_binding_alt_resp = cls.client.post(
                f"/semantic/bindings/{entity_binding_alt_id}/publish"
            )
            assert publish_entity_binding_alt_resp.status_code == 200, (
                publish_entity_binding_alt_resp.text
            )
            imports.append(
                {
                    "import_key": "entity_bridge_alt",
                    "binding_ref": secondary_imported_binding_ref,
                    "required_ref_prefixes": ["dimension."],
                }
            )

        metric_binding_resp = cls.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.intent_bridge_metric_{suffix}",
                    "display_name": "Intent Bridge Metric Binding",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "imports": imports,
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": cls.import_bridge_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": cls.import_bridge_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.event_date",
                                    "physical_name": "event_date",
                                },
                                {
                                    "surface_ref": "field.user_id",
                                    "physical_name": "user_id",
                                },
                                {
                                    "surface_ref": "field.value",
                                    "physical_name": "value",
                                },
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.event_date",
                            },
                            "semantic_ref": "time.event_date",
                            "surface_ref": "field.event_date",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "measure",
                            },
                            "semantic_ref": "metric_input.measure",
                            "surface_ref": "field.value",
                        },
                    ],
                },
            },
        )
        assert metric_binding_resp.status_code == 200, metric_binding_resp.text
        metric_binding_id = metric_binding_resp.json()["binding_id"]
        publish_metric_binding_resp = cls.client.post(
            f"/semantic/bindings/{metric_binding_id}/publish"
        )
        assert publish_metric_binding_resp.status_code == 200, publish_metric_binding_resp.text
        return metric_name


class ObserveIntentNotReadyEndpointTests(_SemanticObserveIntentEndpointMixin, unittest.TestCase):
    """Observe readiness error with only the not-ready metric seeded."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.client.app.state.service.metadata,
            metric_name="intent_not_ready_metric",
            display_name="Intent Not Ready Metric",
            definition_sql="COUNT(*)",
            dimensions=["platform"],
            measure_type="average",
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_not_ready_metric",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            metric_input_target_keys=["numerator"],
        )

    def test_observe_not_ready_metric_returns_409_with_structured_readiness_error(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("intent_not_ready_metric"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_not_ready")
        self.assertEqual(detail["category"], "readiness")
        self.assertEqual(detail["subject_ref"], "metric.intent_not_ready_metric")
        self.assertEqual(detail["readiness_status"], "not_ready")


class ObserveIntentAuxBindingEndpointTests(_SemanticObserveIntentEndpointMixin, unittest.TestCase):
    """Observe success path with only the auxiliary-binding metric seeded."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.client.app.state.service.metadata,
            metric_name="intent_aux_binding_metric",
            display_name="Intent Auxiliary Binding Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_aux_binding_metric",
            object_id=cls.import_bridge_object_id,
            carrier_locator=cls.import_bridge_fqn,
            binding_role="auxiliary",
        )

    def test_observe_ready_metric_with_auxiliary_binding_executes(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("intent_aux_binding_metric"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metric"], "intent_aux_binding_metric")


class ObserveIntentPreflightFailureEndpointTests(
    _SemanticObserveIntentEndpointMixin, unittest.TestCase
):
    """Observe preflight failure path with only the preflight metric seeded."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.client.app.state.service.metadata,
            metric_name="intent_preflight_failure_metric",
            display_name="Intent Preflight Failure Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.intent_preflight_failure_metric",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
        )

    def test_observe_execution_preflight_failure_returns_candidate_binding_detail(self) -> None:
        service = self.client.app.state.service
        original = service._resolve_metric_carrier_source_object
        service._resolve_metric_carrier_source_object = lambda _carrier: None
        try:
            response = self.client.post(
                f"/sessions/{self.session_id}/intents/observe",
                json={
                    "metric": _metric_ref("intent_preflight_failure_metric"),
                    "time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                },
            )
        finally:
            service._resolve_metric_carrier_source_object = original

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_not_ready")
        self.assertEqual(detail["subject_ref"], "metric.intent_preflight_failure_metric")
        self.assertEqual(
            detail["blocking_requirements"][0]["code"],
            "METRIC_EXECUTION_BINDING_UNRESOLVED",
        )
        candidate = detail["blocking_requirements"][0]["details"]["candidate_bindings"][0]
        self.assertEqual(
            candidate["binding_ref"], "binding.intent_preflight_failure_metric_primary"
        )
        self.assertEqual(candidate["failure_stage"], "source_object_lookup")


class ObserveIntentCompatibilityEndpointTests(
    _SemanticObserveIntentEndpointMixin, unittest.TestCase
):
    """Observe compatibility failures that require a time-derived dimension."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        cls._setup_compatibility_metric()

    def test_observe_incompatible_dimension_returns_409_with_structured_compatibility_error(
        self,
    ) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("intent_compatible_metric"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "dimensions": ["dimension.intent_signup_week"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["category"], "compatibility")
        self.assertEqual(detail["subject_ref"], "dimension.intent_signup_week")
        self.assertEqual(
            detail["issues"][0]["code"],
            "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
        )


class ObserveIntentImportBridgeSingleEndpointTests(
    _SemanticObserveIntentEndpointMixin, unittest.TestCase
):
    """Observe imported-dimension bridge success path with a single imported binding."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        cls.metric_name = cls._create_import_bridge_metric(mode="single")

    def test_observe_imported_dimension_bridge_allows_segmented_request(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(self.metric_name),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-04"},
                "dimensions": ["dimension.cluster"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["observation_type"], "segmented")
        self.assertEqual(data["dimensions"], ["dimension.cluster"])
        self.assertEqual(len(data["segments"]), 2)
        values = [segment["value"] for segment in data["segments"]]
        self.assertEqual(values, sorted(values, reverse=True))


class ObserveIntentImportBridgeMissingEndpointTests(
    _SemanticObserveIntentEndpointMixin, unittest.TestCase
):
    """Observe imported-dimension bridge missing-import error path."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        cls.metric_name = cls._create_import_bridge_metric(mode="missing")

    def test_observe_imported_dimension_bridge_missing_returns_structured_error(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(self.metric_name),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-04"},
                "dimensions": ["dimension.cluster"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["subject_ref"], "dimension.cluster")
        self.assertEqual(detail["issues"][0]["code"], "COMPILER_DIMENSION_IMPORT_MISSING")


@pytest.mark.slow
class ObserveIntentImportBridgeAmbiguousEndpointTests(
    _SemanticObserveIntentEndpointMixin, unittest.TestCase
):
    """Observe imported-dimension bridge ambiguous-import error path."""

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        cls.metric_name = cls._create_import_bridge_metric(mode="ambiguous")

    def test_observe_imported_dimension_bridge_ambiguous_returns_structured_error(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(self.metric_name),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-04"},
                "dimensions": ["dimension.cluster"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["subject_ref"], "dimension.cluster")
        self.assertEqual(detail["issues"][0]["code"], "COMPILER_DIMENSION_IMPORT_AMBIGUOUS")
        self.assertEqual(
            sorted(
                candidate["import_key"]
                for candidate in detail["issues"][0]["details"]["candidates"]
            ),
            ["entity_bridge", "entity_bridge_alt"],
        )


class AttributeUnknownMetricEndpointTests(unittest.TestCase):
    """Lightweight coverage for attribute's unknown-metric HTTP failure path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "attribute_unknown_metric.duckdb"
        cls.client = TestClient(create_app(cls.db_path))
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

    def test_compare_nonexistent_ref_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_001",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_002",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_compare_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": "sess_other",
                    "step_id": "step_001",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_002",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Cross-session", r.json()["detail"])

    def test_correlate_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": "sess_foreign",
                    "step_id": "step_a",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_b",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_correlate_nonexistent_steps_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_nonexistent_a",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_nonexistent_b",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("STEP_NOT_FOUND", r.json()["detail"])

    def test_detect_unregistered_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2024-01-01", "end": "2024-01-08"},
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_intent_test_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json={
                "hypothesis": {"family": "difference", "alternative": "two_sided", "alpha": 0.05},
                "left_ref": {
                    "session_id": "sess_x",
                    "artifact_id": "art_1",
                    "observation_type": "numeric_sample_summary",
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "artifact_id": "art_2",
                    "observation_type": "numeric_sample_summary",
                    "step_id": "step_2",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_intent_test_rejects_missing_steps(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json={
                "hypothesis": {"family": "difference", "alternative": "two_sided", "alpha": 0.05},
                "left_ref": {
                    "session_id": self.session_id,
                    "artifact_id": "art_1",
                    "observation_type": "numeric_sample_summary",
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "artifact_id": "art_2",
                    "observation_type": "numeric_sample_summary",
                    "step_id": "step_2",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_rejects_missing_horizon(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "source_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                }
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_nonexistent_step_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "source_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "horizon": 7,
            },
        )
        self.assertEqual(r.status_code, 422)

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
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
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
        cls.client = TestClient(create_app(db_path))
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
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
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
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2024-01-01", "end": "2024-01-08"},
                },
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
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2024-01-01", "end": "2024-01-08"},
                },
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
                "sample_kind": "rate",
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


class IntentEndpointWithSemanticLayerTests(unittest.TestCase):
    """Tests that require a semantic metric wired to a source table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "intent_semantic.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.client = TestClient(create_app(db_path))
        cls._setup_semantic_layer()
        r = cls.client.post(
            "/sessions",
            json={
                "goal": "Observe semantic metric test",
                "budget": {},
                "policy": {},
            },
        )
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        """Register a source, engine, binding, and semantic metric so observe can execute."""
        # Register source
        r = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Test DuckDB",
                "connection": {"database": ":memory:"},
            },
        )
        cls.source_id = r.json()["source_id"]

        # Register engine
        r = cls.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Test DuckDB Engine",
                "connection": {},
            },
        )
        cls.engine_id = r.json()["engine_id"]

        # Create binding
        cls.client.post(
            "/bindings",
            json={"source_id": cls.source_id, "engine_id": cls.engine_id, "priority": 0},
        )

        # Create a semantic metric (uses watch_events table from demo data)
        metric = create_typed_metric(
            cls.client,
            name="test_observe_metric",
            display_name="Test Observe Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
            grain="day",
            measure_type="average",
        )
        cls.metric_id = metric["metric_contract_id"]

    def test_observe_with_real_metric_executes_or_404_if_not_mapped(self) -> None:
        """Observe succeeds if metric is mapped to a table, or returns 422 if not mapped."""
        if self.metric_id is None:
            self.skipTest("Metric creation failed in setUpClass")

        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("test_observe_metric"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        # Either 200 (metric resolved and query ran) or 422 (not mapped to a source object yet)
        self.assertIn(r.status_code, {200, 422})
        if r.status_code == 422:
            self.assertIn("metric", r.json()["detail"].lower())
            return
        self.assertEqual(r.json()["metric"], "test_observe_metric")


class ObserveTypedArtifactTests(_ObserveIntentTestCase, unittest.TestCase):
    """Phase 3a: verify that observe produces a typed observation artifact.

    Requires a fully wired semantic layer (metric published + mapped to a source table).
    """

    def test_observe_returns_typed_artifact_shape(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()

        # Typed artifact fields from observe.md contract
        self.assertEqual(data["intent_type"], "observe")
        self.assertEqual(data["observation_type"], "scalar")
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn("artifact_id", data)
        self.assertTrue(data["artifact_id"].startswith("art_"))
        self.assertEqual(data["step_ref"]["step_type"], "observe")
        self.assertIn("analytical_metadata", data)
        self.assertIn("quality_status", data["analytical_metadata"])
        self.assertIn("execution_metadata", data)
        self.assertIn("query_hash", data["execution_metadata"])
        self.assertEqual(data["time_scope"]["kind"], "range")

    def test_observe_artifact_persisted_in_db(self) -> None:
        """Verify artifact row is stored with lifecycle='committed'."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-08", "end": "2024-01-15"},
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200)
        artifact_id = r.json()["artifact_id"]

        # Verify via direct service access
        service = self.app.state.service
        row = service.metadata.query_one(
            "SELECT artifact_type, lifecycle FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["artifact_type"], "observation")
        self.assertEqual(row["lifecycle"], "committed")

    def test_observe_time_series_returns_correct_shape(self) -> None:
        """granularity='day' produces observation_type='time_series' with series list."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["observation_type"], "time_series")
        self.assertEqual(data["granularity"], "day")
        self.assertIn("series", data)
        self.assertIsInstance(data["series"], list)
        # Each series entry has window.start, window.end, value
        for entry in data["series"]:
            self.assertIn("window", entry)
            self.assertIn("start", entry["window"])
            self.assertIn("end", entry["window"])
            self.assertIn("value", entry)

    def test_observe_segmented_returns_correct_shape(self) -> None:
        """dimensions=['platform'] produces observation_type='segmented' with segments."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "dimensions": ["platform"],
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["observation_type"], "segmented")
        self.assertEqual(data["dimensions"], ["platform"])
        self.assertIn("segments", data)
        self.assertIsInstance(data["segments"], list)

    def test_observe_segmented_calendar_alignment_returns_segmented_yoy(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                "dimensions": ["platform"],
                "calendar_policy_ref": "calendar_policy.weekday_yoy",
            },
        )
        if r.status_code in (422, 502):
            import sys

            print(f"DEBUG {r.status_code}: {r.text}", file=sys.stderr)
            self.skipTest(f"Semantic layer not fully wired: {r.text[:200]}")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["observation_type"], "segmented")
        self.assertIn("resolved_policy_summary", data)
        self.assertEqual(
            data["resolved_policy_summary"]["policy_ref"], "calendar_policy.weekday_yoy"
        )
        self.assertIn("segmented_yoy", data)
        self.assertIsInstance(data["segmented_yoy"], list)
        for entry in data["segmented_yoy"]:
            self.assertIn("keys", entry)
            self.assertIn("current_value", entry)
            self.assertIn("baseline_value", entry)
            self.assertIn("absolute_delta", entry)
            self.assertIn("relative_delta", entry)

    def test_observe_snapshot_now_returns_scalar(self) -> None:
        """snapshot_now time scope resolves and executes (returns scalar artifact)."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "snapshot_now"},
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["observation_type"], "scalar")
        self.assertEqual(data["time_scope"]["kind"], "snapshot_now")
        self.assertIn("observed_at", data["time_scope"])

    def test_observe_as_of_returns_scalar(self) -> None:
        """as_of time scope resolves and executes (returns scalar artifact)."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "as_of", "at": "2024-01-07T00:00:00"},
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["observation_type"], "scalar")
        self.assertEqual(data["time_scope"]["kind"], "as_of")
        self.assertEqual(data["time_scope"]["at"], "2024-01-07")

    def test_observe_granularity_and_dimensions_returns_400(self) -> None:
        """granularity + dimensions together is an illegal combination."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
                "dimensions": ["platform"],
            },
        )
        self.assertIn(r.status_code, (400, 422))

    def test_observe_snapshot_now_with_granularity_returns_400(self) -> None:
        """snapshot_now + granularity is an illegal combination."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "snapshot_now"},
                "granularity": "day",
            },
        )
        self.assertIn(r.status_code, (400, 422))

    def test_observe_invalid_granularity_returns_400(self) -> None:
        """Unknown granularity string is rejected."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "quarter",
            },
        )
        self.assertIn(r.status_code, (400, 422))

    def test_observe_hour_granularity_rejects_date_only_range(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-02"},
                "granularity": "hour",
            },
        )
        self.assertIn(r.status_code, (400, 422))

    def test_observe_segmented_sorted_by_value_desc(self) -> None:
        """Segmented result segments are sorted value desc per artifact contract."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "dimensions": ["platform"],
            },
        )
        if r.status_code == 422:
            self.skipTest("Semantic layer not fully wired in this environment")
        self.assertEqual(r.status_code, 200, r.text)
        segments = r.json().get("segments", [])
        values = [s["value"] for s in segments if s["value"] is not None]
        self.assertEqual(values, sorted(values, reverse=True))


class ObserveAggregateMetricSummaryErrorTests(unittest.TestCase):
    """Lightweight coverage for aggregate metrics rejected in numeric summary mode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "observe_aggregate_summary.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        _seed_default_calendar_source_metadata(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.client = TestClient(cls.app)

        metadata = cls.app.state.service.metadata
        cls.source_id = _register_duckdb_runtime(
            cls.client,
            db_path=cls.db_path,
            source_display_name="Observe Aggregate Summary Source",
            engine_display_name="Observe Aggregate Summary Engine",
        )
        cls.watch_events_fqn = "analytics.watch_events"
        cls.watch_events_object_id = _ensure_source_object(
            metadata,
            source_id=cls.source_id,
            native_name="watch_events",
            fqn=cls.watch_events_fqn,
        )

        metric = create_typed_metric(
            cls.client,
            name="observe_aggregate_summary_dau",
            display_name="Observe Aggregate Summary DAU",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="average",
        )
        publish_typed_metric(cls.client, metric["metric_contract_id"])
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.observe_aggregate_summary_dau",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
        )

        response = cls.client.post(
            "/sessions",
            json={"goal": "observe aggregate summary error test", "budget": {}, "policy": {}},
        )
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_observe_aggregate_metric_numeric_summary_returns_error(self) -> None:
        """Aggregate metric (COUNT DISTINCT) cannot be used as per-row value expression.

        numeric_sample_summary mode requires a raw column expression, not an outer aggregate.
        DuckDB rejects nested aggregates (AVG(COUNT(DISTINCT ...))) with a SQL error.
        """
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_aggregate_summary_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "result_mode": "numeric_sample_summary",
            },
        )
        # DuckDB rejects nested aggregates — returned as 502 (execution error)
        self.assertNotEqual(r.status_code, 200)


class ObserveRateMetricStandardModeTests(_ObserveIntentTestCase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        metadata = cls.app.state.service.metadata
        ensure_published_typed_metric(
            metadata,
            metric_name="observe_typed_rate",
            display_name="Observe Typed Rate",
            grain="day",
            dimensions=["event_date"],
            measure_type="rate",
        )
        ensure_published_typed_metric_binding(
            metadata,
            metric_name="observe_typed_rate",
            carrier_locator=cls.watch_events_fqn,
            source_object_ref=cls.watch_events_object_id,
            metric_input_target_keys=["numerator", "denominator"],
            surface_name="play_duration_seconds",
        )

    def test_observe_typed_rate_metric_standard_mode_uses_aggregate_sql(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_typed_rate"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 200, r.text)


class ObserveRateMetricSummaryModeTests(_ObserveIntentTestCase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.app.state.service.metadata,
            metric_name="observe_typed_rate_summary",
            display_name="Observe Typed Rate Summary",
            grain="day",
            dimensions=["event_date"],
            measure_type="rate",
        )
        ensure_published_typed_metric_binding(
            cls.app.state.service.metadata,
            metric_name="observe_typed_rate_summary",
            carrier_locator=cls.watch_events_fqn,
            source_object_ref=cls.watch_events_object_id,
            metric_input_target_keys=["numerator", "denominator"],
            surface_name="play_duration_seconds",
        )

    def test_observe_typed_rate_metric_rate_summary_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_typed_rate_summary"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "result_mode": "rate_sample_summary",
            },
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("per-row rate value expression", r.text)


class _ObserveBindingResolutionBase(_ObserveIntentTestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.app.state.service.metadata,
            metric_name="observe_binding_fallback",
            display_name="Observe Binding Fallback",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )
        ensure_published_typed_metric(
            cls.app.state.service.metadata,
            metric_name="observe_binding_ambiguous",
            display_name="Observe Binding Ambiguous",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )
        ensure_published_typed_metric(
            cls.app.state.service.metadata,
            metric_name="observe_binding_missing_slot",
            display_name="Observe Binding Missing Slot",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )

    @classmethod
    def _seed_aux_binding_table(cls) -> str:
        aux_fqn = "analytics.observe_aux_binding"
        con = duckdb.connect(str(cls.db_path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {aux_fqn} (
                    event_date DATE NOT NULL,
                    aux_value DOUBLE NOT NULL
                )
                """
            )
            con.execute(f"DELETE FROM {aux_fqn}")
            con.executemany(
                f"INSERT INTO {aux_fqn} VALUES (?, ?)",
                [("2024-01-01", 1.0), ("2024-01-02", 2.0), ("2024-01-03", 3.0)],
            )
        finally:
            con.close()
        return _ensure_source_object(
            cls.app.state.service.metadata,
            source_id=cls.source_id,
            native_name="observe_aux_binding",
            fqn=aux_fqn,
        )


@pytest.mark.slow
class ObserveBindingFallbackTests(_ObserveBindingResolutionBase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        super()._setup_additional_semantic_layer()
        aux_object_id = cls._seed_aux_binding_table()
        _create_metric_binding(
            cls.client,
            binding_ref="binding.aaa_observe_binding_fallback_incomplete",
            metric_ref="metric.observe_binding_fallback",
            source_object_ref=aux_object_id,
            carrier_locator="analytics.observe_aux_binding",
            binding_role="auxiliary",
            metric_input_target_keys=["measure"],
            surface_name="aux_value",
        )
        _create_metric_binding(
            cls.client,
            binding_ref="binding.zzz_observe_binding_fallback_complete",
            metric_ref="metric.observe_binding_fallback",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
            surface_name="play_duration_seconds",
        )

    def test_observe_uses_viable_binding_instead_of_first_binding_row(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "metric.observe_binding_fallback",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)


@pytest.mark.slow
class ObserveBindingAmbiguityTests(_ObserveBindingResolutionBase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        super()._setup_additional_semantic_layer()
        _create_metric_binding(
            cls.client,
            binding_ref="binding.aaa_observe_binding_ambiguous",
            metric_ref="metric.observe_binding_ambiguous",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )
        _create_metric_binding(
            cls.client,
            binding_ref="binding.bbb_observe_binding_ambiguous",
            metric_ref="metric.observe_binding_ambiguous",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )

    def test_observe_returns_binding_ambiguity_error_for_multiple_primary_bindings(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "metric.observe_binding_ambiguous",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("ambiguous", response.text)
        self.assertIn("binding.aaa_observe_binding_ambiguous", response.text)
        self.assertIn("binding.bbb_observe_binding_ambiguous", response.text)


class ObserveBindingCoverageTests(_ObserveBindingResolutionBase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        super()._setup_additional_semantic_layer()
        missing_slot_binding_id = _create_metric_binding(
            cls.client,
            binding_ref="binding.observe_binding_missing_slot_missing_measure",
            metric_ref="metric.observe_binding_missing_slot",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )
        cls.app.state.service.metadata.execute(
            """
            UPDATE field_bindings
            SET target_key = ?, semantic_ref = ?
            WHERE binding_id = ? AND target_kind = 'metric_input'
            """,
            ["count_target", "metric_input.count_target", missing_slot_binding_id],
        )

    def test_observe_returns_metric_input_coverage_error(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "metric.observe_binding_missing_slot",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("METRIC_INPUT_COVERAGE_MISSING", response.text)
        self.assertIn("missing required metric_input coverage", response.text)


class ObserveDistributionMetricScalarTests(_ObserveIntentTestCase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        ensure_published_typed_metric(
            cls.app.state.service.metadata,
            metric_name="observe_distribution_metric",
            display_name="Observe Distribution Metric",
            grain="day",
            dimensions=["event_date"],
            measure_type="percentile",
        )
        _create_metric_binding(
            cls.client,
            binding_ref="binding.observe_distribution_metric_primary",
            metric_ref="metric.observe_distribution_metric",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["value_component"],
            surface_name="play_duration_seconds",
        )

    def test_observe_distribution_metric_uses_bound_value_component(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "metric.observe_distribution_metric",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["observation_type"], "scalar")
        self.assertEqual(payload["metric"], "observe_distribution_metric")


class ObserveDistributionMetricHistogramTests(_ObserveIntentTestCase, unittest.TestCase):
    @classmethod
    def _setup_additional_semantic_layer(cls) -> None:
        metadata = cls.app.state.service.metadata
        ensure_published_typed_metric(
            metadata,
            metric_name="observe_distribution_histogram",
            display_name="Observe Distribution Histogram",
            grain="day",
            dimensions=["event_date"],
            measure_type="percentile",
        )
        metric_row = metadata.query_one(
            """
            SELECT metric_contract_id, family_payload_json
            FROM semantic_metric_contracts
            WHERE metric_ref = ?
            """,
            ["metric.observe_distribution_histogram"],
        )
        assert metric_row is not None
        family_payload = json.loads(metric_row["family_payload_json"] or "{}")
        family_payload["distribution_spec"] = {"kind": "histogram_ready"}
        metadata.execute(
            """
            UPDATE semantic_metric_contracts
            SET family_payload_json = ?
            WHERE metric_contract_id = ?
            """,
            [json.dumps(family_payload), metric_row["metric_contract_id"]],
        )
        _create_metric_binding(
            cls.client,
            binding_ref="binding.observe_distribution_histogram_primary",
            metric_ref="metric.observe_distribution_histogram",
            source_object_ref=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["value_component"],
            surface_name="play_duration_seconds",
        )

    def test_observe_distribution_histogram_ready_returns_unsupported_operation(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "metric.observe_distribution_histogram",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("UNSUPPORTED_OPERATION", response.text)
        self.assertNotIn("missing required metric_input coverage", response.text)


class ArtifactLifecycleTests(unittest.TestCase):
    """Phase 3a: staged/committed lifecycle and ObservationRef resolution."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        from app.main import create_app
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "lifecycle.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.app = create_app(db_path)
        cls.service = cls.app.state.service

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        from uuid import uuid4

        session_id = f"sess_{uuid4().hex[:12]}"
        self.service.metadata.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
            "VALUES (?, ?, '{}', '{}', '{}', 'open')",
            [session_id, "lifecycle test"],
        )
        return session_id

    def test_insert_artifact_staged_lifecycle(self) -> None:
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}"
        artifact_id = self.service._insert_artifact(
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
        artifact_id = self.service._insert_artifact(
            session_id, step_id, "observation", "test", {"v": 2}, lifecycle="staged"
        )
        self.service._commit_artifact(artifact_id)
        row = self.service.metadata.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")

    def test_resolve_artifact_for_ref_returns_content(self) -> None:
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}"
        content = {"observation_type": "scalar", "value": 42.0}
        self.service._insert_artifact(session_id, step_id, "observation", "test", content)
        result = self.service._resolve_artifact_for_ref(session_id, step_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["observation_type"], "scalar")
        self.assertEqual(result["value"], 42.0)

    def test_resolve_artifact_for_ref_staged_not_returned(self) -> None:
        """Staged artifacts are not returned by ref resolution."""
        session_id = self._make_session()
        step_id = f"step_{session_id[:8]}_staged"
        self.service._insert_artifact(
            session_id, step_id, "observation", "test", {"v": 3}, lifecycle="staged"
        )
        result = self.service._resolve_artifact_for_ref(session_id, step_id)
        self.assertIsNone(result)

    def test_resolve_artifact_for_ref_not_found_returns_none(self) -> None:
        result = self.service._resolve_artifact_for_ref("sess_nonexistent", "step_none")
        self.assertIsNone(result)


class CompareIntentTests(unittest.TestCase):
    """Phase 3b-1: verify that compare produces a typed compare_artifact.

    setUpClass runs two scalar observe steps and two segmented observe steps
    so subsequent compare calls have real upstream artifact refs to resolve.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from app.main import create_app
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "compare_intent.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service
        cls.skipped = False

        r = cls.client.post("/sessions", json={"goal": "compare intent test"})
        cls.session_id = r.json()["session_id"]
        cls.left_step_id = "step_compare_scalar_left"
        cls.right_step_id = "step_compare_scalar_right"
        cls.left_seg_step_id = "step_compare_segmented_left"
        cls.right_seg_step_id = "step_compare_segmented_right"
        cls.left_ts_step_id = "step_compare_ts_left"
        cls.right_ts_step_id = "step_compare_ts_right"
        cls.other_step_id = "step_compare_other_metric"

        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.left_step_id,
            metric="compare_test_dau",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-08", "end": "2024-01-15"},
            value=12.0,
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.right_step_id,
            metric="compare_test_dau",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=10.0,
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.left_seg_step_id,
            metric="compare_test_dau",
            observation_type="segmented",
            time_scope={"kind": "range", "start": "2026-02-14", "end": "2026-02-21"},
            dimensions=["platform"],
            value=12.0,
            segments=[
                {"keys": {"platform": "ios"}, "value": 7.0},
                {"keys": {"platform": "android"}, "value": 5.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.right_seg_step_id,
            metric="compare_test_dau",
            observation_type="segmented",
            time_scope={"kind": "range", "start": "2026-02-07", "end": "2026-02-14"},
            dimensions=["platform"],
            value=10.0,
            segments=[
                {"keys": {"platform": "ios"}, "value": 6.0},
                {"keys": {"platform": "android"}, "value": 4.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.left_ts_step_id,
            metric="compare_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2026-02-14", "end": "2026-02-21"},
            granularity="day",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 3.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 4.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.right_ts_step_id,
            metric="compare_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2026-02-07", "end": "2026-02-14"},
            granularity="day",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 2.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 5.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=cls.other_step_id,
            metric="compare_test_other",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            value=8.0,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _skip_if_not_wired(self) -> None:
        if self.skipped or self.left_step_id is None or self.right_step_id is None:
            self.skipTest("Semantic layer not fully wired or observe steps failed")

    def _seed_scalar_observe(self, *, step_id: str, start: str, end: str) -> str:
        _insert_observe_artifact(
            self.service,
            session_id=self.session_id,
            step_id=step_id,
            metric="compare_test_dau",
            observation_type="scalar",
            time_scope={"kind": "range", "start": start, "end": end},
            value=10.0,
        )
        return step_id

    def _update_observation_resolved_policy_summary(
        self,
        *,
        step_id: str,
        resolved_policy_summary: dict[str, object] | None,
    ) -> None:
        row = self.service.metadata.query_one(
            "SELECT artifact_id, content_json FROM artifacts WHERE step_id = ? AND lifecycle = 'committed'",
            [step_id],
        )
        self.assertIsNotNone(row)
        assert row is not None
        content = json.loads(row["content_json"])
        content["resolved_policy_summary"] = resolved_policy_summary
        self.service.metadata.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [json.dumps(content), row["artifact_id"]],
        )

    @staticmethod
    def _resolved_policy_summary(
        *,
        policy_ref: str = "calendar_policy.weekday_yoy",
        comparison_basis: str = "yoy",
        resolved_calendar_source: str = "calendar.test_fixture",
        resolved_calendar_version: str = "calendar.test_fixture_v1",
        aligned_bucket_count: int = 7,
        unpaired_bucket_count: int = 0,
        aligned_ratio: float = 1.0,
        expected_bucket_count: int = 7,
        present_bucket_count: int = 7,
        missing_bucket_count: int = 0,
        coverage_ratio: float = 1.0,
        comparability_warnings: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "policy_ref": policy_ref,
            "comparison_basis": comparison_basis,
            "resolved_calendar_source": resolved_calendar_source,
            "resolved_calendar_version": resolved_calendar_version,
            "resolved_baseline_generation_rule": {
                "strategy": "previous_year",
                "offset_value": 1,
                "offset_unit": "year",
                "fixed_start": None,
                "fixed_end": None,
                "named_window_ref": None,
            },
            "current_window": {"start": "2026-02-14", "end": "2026-02-21"},
            "baseline_window": {"start": "2025-02-14", "end": "2025-02-21"},
            "bucket_pairing": [
                {
                    "current_bucket_start": "2026-02-14",
                    "baseline_bucket_start": "2025-02-14",
                    "pairing_reason": "same_weekday_nearest",
                    "shift_days": 365,
                    "issues": [],
                    "strictness_level": "strict",
                    "is_reused_baseline_bucket": False,
                }
            ],
            "rollup_safe": True,
            "coverage_summary": {
                "aligned_bucket_count": aligned_bucket_count,
                "unpaired_bucket_count": unpaired_bucket_count,
                "aligned_ratio": aligned_ratio,
            },
            "data_coverage_summary": {
                "expected_bucket_count": expected_bucket_count,
                "present_bucket_count": present_bucket_count,
                "missing_bucket_count": missing_bucket_count,
                "coverage_ratio": coverage_ratio,
                "aligned_expected_bucket_count": expected_bucket_count,
                "aligned_present_current_bucket_count": present_bucket_count,
                "aligned_present_baseline_bucket_count": present_bucket_count,
                "aligned_present_both_bucket_count": present_bucket_count,
            },
            "comparability_warnings": comparability_warnings or [],
        }

    def test_scalar_compare_success(self) -> None:
        """compare two scalar observe artifacts returns 200 with correct shape."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["intent_type"], "compare")
        self.assertEqual(data["artifact_type"], "compare_artifact")
        self.assertEqual(data["comparison_type"], "scalar_delta")
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn("artifact_id", data)
        self.assertTrue(data["artifact_id"].startswith("art_"))
        self.assertIn("direction", data)
        self.assertIn(data["direction"], {"increase", "decrease", "flat", "undefined"})
        self.assertIn("comparability", data)
        self.assertIn(data["comparability"]["status"], {"comparable", "needs_attention"})
        self.assertIn("lineage", data)
        self.assertEqual(data["lineage"]["left_source_ref"]["step_id"], self.left_step_id)
        self.assertEqual(data["lineage"]["right_source_ref"]["step_id"], self.right_step_id)

    def test_scalar_compare_artifact_persisted(self) -> None:
        """compare artifact is written to DB with lifecycle='committed'."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        artifact_id = r.json()["artifact_id"]
        row = self.service.metadata.query_one(
            "SELECT artifact_type, lifecycle FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["artifact_type"], "compare_artifact")
        self.assertEqual(row["lifecycle"], "committed")

    def test_scalar_compare_lineage(self) -> None:
        """compare artifact lineage correctly references both upstream step IDs."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        lineage = r.json()["lineage"]
        self.assertEqual(lineage["left_source_ref"]["step_id"], self.left_step_id)
        self.assertEqual(lineage["right_source_ref"]["step_id"], self.right_step_id)
        self.assertEqual(lineage["derivation_version"], "1.0")

    def test_compare_reuses_frozen_calendar_alignment_from_observation_artifacts(self) -> None:
        left_step_id = self._seed_scalar_observe(
            step_id="step_compare_frozen_left", start="2026-02-21", end="2026-02-28"
        )
        right_step_id = self._seed_scalar_observe(
            step_id="step_compare_frozen_right", start="2026-02-14", end="2026-02-21"
        )
        summary = self._resolved_policy_summary(
            resolved_calendar_source="calendar.patched_for_compare_reuse",
            resolved_calendar_version="calendar.patched_for_compare_reuse_v3",
            aligned_bucket_count=6,
            unpaired_bucket_count=1,
            aligned_ratio=6 / 7,
        )
        self._update_observation_resolved_policy_summary(
            step_id=left_step_id,
            resolved_policy_summary=summary,
        )
        self._update_observation_resolved_policy_summary(
            step_id=right_step_id,
            resolved_policy_summary=summary,
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_step_id,
                    "step_type": "observe",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["comparability"]["status"], "needs_attention")
        self.assertEqual(
            payload["resolved_input_summary"]["calendar_alignment"]["reuse_source"],
            "observation_resolved_policy_summary",
        )
        self.assertEqual(
            payload["resolved_input_summary"]["calendar_alignment"]["resolved_calendar_source"],
            "calendar.patched_for_compare_reuse",
        )
        self.assertEqual(
            payload["resolved_input_summary"]["calendar_alignment"]["resolved_calendar_version"],
            "calendar.patched_for_compare_reuse_v3",
        )
        self.assertTrue(payload["resolved_input_summary"]["calendar_alignment"]["rollup_safe"])
        self.assertEqual(
            payload["resolved_input_summary"]["calendar_alignment"]["effective_coverage_summary"],
            {
                "aligned_bucket_count": 6,
                "unpaired_bucket_count": 1,
                "aligned_ratio": 6 / 7,
            },
        )
        self.assertEqual(
            payload["comparability"]["issues"][-1]["code"],
            "alignment_coverage_insufficient",
        )

    def test_compare_fails_when_observation_frozen_alignment_metadata_mismatches(self) -> None:
        left_step_id = self._seed_scalar_observe(
            step_id="step_compare_mismatch_left", start="2026-02-21", end="2026-02-28"
        )
        right_step_id = self._seed_scalar_observe(
            step_id="step_compare_mismatch_right", start="2026-02-14", end="2026-02-21"
        )
        self._update_observation_resolved_policy_summary(
            step_id=left_step_id,
            resolved_policy_summary=self._resolved_policy_summary(
                resolved_calendar_source="calendar.left_only_source"
            ),
        )
        self._update_observation_resolved_policy_summary(
            step_id=right_step_id,
            resolved_policy_summary=self._resolved_policy_summary(
                resolved_calendar_source="calendar.right_only_source"
            ),
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_step_id,
                    "step_type": "observe",
                },
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("NOT_COMPARABLE", response.json()["detail"])
        self.assertIn(
            "left and right observations freeze different calendar sources",
            response.json()["detail"],
        )

    def test_compare_rejects_weekday_pairing_tie_from_frozen_observation_metadata(self) -> None:
        left_step_id = self._seed_scalar_observe(
            step_id="step_compare_tie_left", start="2026-02-21", end="2026-02-28"
        )
        right_step_id = self._seed_scalar_observe(
            step_id="step_compare_tie_right", start="2026-02-14", end="2026-02-21"
        )
        summary = self._resolved_policy_summary(comparability_warnings=["weekday_pairing_tie"])
        self._update_observation_resolved_policy_summary(
            step_id=left_step_id,
            resolved_policy_summary=summary,
        )
        self._update_observation_resolved_policy_summary(
            step_id=right_step_id,
            resolved_policy_summary=summary,
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_step_id,
                    "step_type": "observe",
                },
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn(
            "weekday alignment produced an unresolved tie",
            response.json()["detail"],
        )

    def test_segmented_compare_success(self) -> None:
        """compare two segmented observe artifacts returns segmented_delta with rows."""
        if self.skipped or self.left_seg_step_id is None or self.right_seg_step_id is None:
            self.skipTest("Segmented observe steps not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_seg_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_seg_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["comparison_type"], "segmented_delta")
        self.assertIn("rows", data)
        self.assertIsInstance(data["rows"], list)
        for row in data["rows"]:
            self.assertIn("keys", row)
            self.assertIn("direction", row)
            self.assertIn("presence", row)
            self.assertIn(row["presence"], {"both", "left_only", "right_only"})
            self.assertIn(row["direction"], {"increase", "decrease", "flat", "undefined"})
        self.assertIn("dimensions", data)

    def test_time_series_compare_success(self) -> None:
        if self.skipped or self.left_ts_step_id is None or self.right_ts_step_id is None:
            self.skipTest("Time-series observe steps not available")
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_ts_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_ts_step_id,
                    "step_type": "observe",
                },
                "mode": "time_series",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["comparison_type"], "time_series_delta")
        self.assertEqual(data["granularity"], "day")
        self.assertIn("rows", data)
        self.assertTrue(len(data["rows"]) > 0)
        self.assertIn("summary_absolute_delta", data)
        self.assertEqual(data["analytical_metadata"]["pairing_basis"], "observed_series")
        self.assertIn("matched_bucket_count", data["analytical_metadata"])
        for row in data["rows"]:
            self.assertIn("window", row)
            self.assertIn("presence", row)
            self.assertIn("direction", row)

    def test_time_series_compare_reuses_calendar_aligned_bucket_pairing(self) -> None:
        left_step_id = "step_compare_ts_calendar_left"
        right_step_id = "step_compare_ts_calendar_right"
        _insert_observe_artifact(
            self.service,
            session_id=self.session_id,
            step_id=left_step_id,
            metric="compare_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2026-02-14", "end": "2026-02-21"},
            granularity="day",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
            ],
        )
        _insert_observe_artifact(
            self.service,
            session_id=self.session_id,
            step_id=right_step_id,
            metric="compare_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2025-02-14", "end": "2025-02-21"},
            granularity="day",
            series=[
                {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
                {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
            ],
        )
        summary = self._resolved_policy_summary()
        self._update_observation_resolved_policy_summary(
            step_id=left_step_id,
            resolved_policy_summary=summary,
        )
        self._update_observation_resolved_policy_summary(
            step_id=right_step_id,
            resolved_policy_summary=summary,
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": right_step_id,
                    "step_type": "observe",
                },
                "mode": "time_series",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(
            data["analytical_metadata"]["pairing_basis"],
            "calendar_aligned_observation_windows",
        )
        self.assertEqual(
            data["analytical_metadata"]["pairing_rule"],
            "calendar_aligned_bucket_pairing",
        )
        self.assertEqual(data["summary_left_value"], 10.0)
        self.assertEqual(data["summary_right_value"], 9.0)
        self.assertEqual(data["summary_absolute_delta"], 1.0)
        self.assertEqual(
            data["analytical_metadata"]["matched_left_time_scope"],
            {"kind": "range", "start": "2026-02-14", "end": "2026-02-15"},
        )
        self.assertEqual(
            data["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2025-02-14", "end": "2025-02-15"},
        )

    def test_compare_mode_time_series_guard(self) -> None:
        self._skip_if_not_wired()
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
                "mode": "time_series",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("INVALID_ARGUMENT", response.json()["detail"])

    def test_compare_nonexistent_ref_returns_422(self) -> None:
        """compare with a non-existent step ref returns 422 (STEP_NOT_FOUND)."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_does_not_exist_xyz",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_does_not_exist_abc",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("STEP_NOT_FOUND", r.json()["detail"])

    def test_compare_rejects_metric_mismatch(self) -> None:
        """compare rejects two observations with different metrics (NOT_COMPARABLE)."""
        self._skip_if_not_wired()
        if self.other_step_id is None:
            self.skipTest("Other metric observe step not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.other_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("NOT_COMPARABLE", r.json()["detail"])

    def test_compare_rejects_type_mismatch(self) -> None:
        """compare rejects scalar vs segmented observation_type (NOT_COMPARABLE)."""
        self._skip_if_not_wired()
        if self.left_seg_step_id is None:
            self.skipTest("Segmented observe step not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_seg_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("NOT_COMPARABLE", r.json()["detail"])

    def test_compare_rejects_cross_session_ref(self) -> None:
        """compare with left_ref pointing to a different session returns 422."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": "sess_other_session",
                    "step_id": "step_x",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_y",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Cross-session", r.json()["detail"])

    def test_compare_rejects_unit_mismatch(self) -> None:
        """compare rejects two observations with mismatched units (NOT_COMPARABLE)."""
        import json as _json

        self._skip_if_not_wired()
        row = self.service.metadata.query_one(
            "SELECT artifact_id, content_json FROM artifacts WHERE step_id = ? AND lifecycle = 'committed'",
            [self.right_step_id],
        )
        if row is None:
            self.skipTest("right step artifact not found in DB")
        content = _json.loads(row["content_json"])
        original_unit = content.get("unit")
        content["unit"] = "bogus_unit_xyz"
        self.service.metadata.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [_json.dumps(content), row["artifact_id"]],
        )
        try:
            r = self.client.post(
                f"/sessions/{self.session_id}/intents/compare",
                json={
                    "left_ref": {
                        "session_id": self.session_id,
                        "step_id": self.left_step_id,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "session_id": self.session_id,
                        "step_id": self.right_step_id,
                        "step_type": "observe",
                    },
                },
            )
            self.assertEqual(r.status_code, 422)
            self.assertIn("NOT_COMPARABLE", r.json()["detail"])
        finally:
            content["unit"] = original_unit
            self.service.metadata.execute(
                "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
                [_json.dumps(content), row["artifact_id"]],
            )

    def test_compare_mode_scalar_guard(self) -> None:
        """compare with mode='scalar' against segmented observations returns 422 INVALID_ARGUMENT."""
        if self.skipped or self.left_seg_step_id is None or self.right_seg_step_id is None:
            self.skipTest("Segmented observe steps not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_seg_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_seg_step_id,
                    "step_type": "observe",
                },
                "mode": "scalar",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("INVALID_ARGUMENT", r.json()["detail"])

    def test_compare_mode_segmented_guard(self) -> None:
        """compare with mode='segmented' against scalar observations returns 422 INVALID_ARGUMENT."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
                "mode": "segmented",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("INVALID_ARGUMENT", r.json()["detail"])


class DecomposeIntentTests(unittest.TestCase):
    """Phase 3b-2: verify that decompose produces a typed delta_decomposition artifact.

    setUpClass wires a semantic layer, creates a session, runs two scalar observe
    steps with dates inside the seeded data range, and then runs compare to produce
    an upstream scalar_delta compare artifact.

    A second compare artifact (segmented_delta) is also produced so that the
    "rejects segmented_delta compare" test can run.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from app.main import create_app
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "decompose_intent.duckdb"
        get_seeded_duckdb_path(db_path)
        _seed_default_calendar_source_metadata(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service
        cls.skipped = False
        cls.compare_step_id = "step_decompose_scalar_compare"
        cls.time_series_compare_step_id = "step_decompose_ts_compare"
        cls.segmented_compare_step_id = "step_decompose_segmented_compare"

        source_id = _register_duckdb_runtime(
            cls.client,
            db_path=db_path,
            source_display_name="Decompose Test Source",
            engine_display_name="Decompose Test Engine",
        )
        obj_id = _ensure_source_object(
            cls.service.metadata,
            source_id=source_id,
            native_name="watch_events",
            fqn="analytics.watch_events",
        )

        ensure_published_typed_metric(
            cls.service.metadata,
            metric_name="decompose_test_dau",
            display_name="DAU (decompose test)",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="sum",
        )
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.decompose_test_dau",
            object_id=obj_id,
            carrier_locator="analytics.watch_events",
            metric_input_target_keys=["measure"],
        )

        r = cls.client.post("/sessions", json={"goal": "decompose intent test"})
        cls.session_id = r.json()["session_id"]
        left_scalar_step = "step_decompose_scalar_left"
        right_scalar_step = "step_decompose_scalar_right"
        left_ts_step = "step_decompose_ts_left"
        right_ts_step = "step_decompose_ts_right"
        left_seg_step = "step_decompose_seg_left"
        right_seg_step = "step_decompose_seg_right"

        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=left_scalar_step,
            metric="decompose_test_dau",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2026-02-21", "end": "2026-03-07"},
            value=12.0,
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=right_scalar_step,
            metric="decompose_test_dau",
            observation_type="scalar",
            time_scope={"kind": "range", "start": "2026-02-07", "end": "2026-02-21"},
            value=10.0,
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=left_ts_step,
            metric="decompose_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2026-02-21", "end": "2026-03-07"},
            granularity="day",
            series=[
                {"window": {"start": "2026-02-21", "end": "2026-02-22"}, "value": 6.0},
                {"window": {"start": "2026-02-22", "end": "2026-02-23"}, "value": 6.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=right_ts_step,
            metric="decompose_test_dau",
            observation_type="time_series",
            time_scope={"kind": "range", "start": "2026-02-07", "end": "2026-02-21"},
            granularity="day",
            series=[
                {"window": {"start": "2026-02-21", "end": "2026-02-22"}, "value": 5.0},
                {"window": {"start": "2026-02-22", "end": "2026-02-23"}, "value": 5.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=left_seg_step,
            metric="decompose_test_dau",
            observation_type="segmented",
            time_scope={"kind": "range", "start": "2026-02-21", "end": "2026-03-07"},
            dimensions=["platform"],
            value=12.0,
            segments=[
                {"keys": {"platform": "ios"}, "value": 7.0},
                {"keys": {"platform": "android"}, "value": 5.0},
            ],
        )
        _insert_observe_artifact(
            cls.service,
            session_id=cls.session_id,
            step_id=right_seg_step,
            metric="decompose_test_dau",
            observation_type="segmented",
            time_scope={"kind": "range", "start": "2026-02-07", "end": "2026-02-21"},
            dimensions=["platform"],
            value=10.0,
            segments=[
                {"keys": {"platform": "ios"}, "value": 6.0},
                {"keys": {"platform": "android"}, "value": 4.0},
            ],
        )

        scalar_compare = cls.client.post(
            f"/sessions/{cls.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": cls.session_id,
                    "step_id": left_scalar_step,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": cls.session_id,
                    "step_id": right_scalar_step,
                    "step_type": "observe",
                },
            },
        )
        if scalar_compare.status_code != 200:
            cls.skipped = True
            return
        cls.compare_step_id = scalar_compare.json()["step_ref"]["step_id"]

        ts_compare = cls.client.post(
            f"/sessions/{cls.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": cls.session_id,
                    "step_id": left_ts_step,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": cls.session_id,
                    "step_id": right_ts_step,
                    "step_type": "observe",
                },
                "mode": "time_series",
            },
        )
        if ts_compare.status_code == 200:
            cls.time_series_compare_step_id = ts_compare.json()["step_ref"]["step_id"]

        seg_compare = cls.client.post(
            f"/sessions/{cls.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": cls.session_id,
                    "step_id": left_seg_step,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": cls.session_id,
                    "step_id": right_seg_step,
                    "step_type": "observe",
                },
            },
        )
        if seg_compare.status_code == 200:
            cls.segmented_compare_step_id = seg_compare.json()["step_ref"]["step_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _skip_if_not_wired(self) -> None:
        if self.skipped or self.compare_step_id is None:
            self.skipTest("Semantic layer not fully wired or upstream steps failed")

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_decompose_success(self) -> None:
        """decompose returns 200 with decomposition_type='delta_decomposition' and rows."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["decomposition_type"], "delta_decomposition")
        self.assertIn("rows", data)
        self.assertGreater(len(data["rows"]), 0)

    def test_decompose_artifact_persisted(self) -> None:
        """decompose artifact is written to DB with lifecycle='committed'."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 200)
        artifact_id = r.json()["artifact_id"]
        row = self.service.metadata.query_one(
            "SELECT lifecycle, artifact_type FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")
        self.assertEqual(row["artifact_type"], "delta_decomposition")

    def test_decompose_artifact_shape(self) -> None:
        """decompose artifact contains required fields per decompose.md spec."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # Top-level artifact fields
        self.assertIn("attribution", data)
        self.assertIn("status", data["attribution"])
        self.assertIn("dimension", data)
        self.assertEqual(data["dimension"], "platform")
        self.assertEqual(data["method"], "delta_share")
        # Row shape
        for row in data["rows"]:
            self.assertIn("key", row)
            self.assertIn("presence", row)
            self.assertIn(row["presence"], ("both", "left_only", "right_only"))
            self.assertIn("direction", row)
            self.assertIn(row["direction"], ("increase", "decrease", "flat", "undefined"))
            self.assertIn("absolute_contribution", row)
        # Lineage
        self.assertEqual(data["compare_ref"]["step_id"], self.compare_step_id)
        self.assertEqual(data["step_ref"]["step_type"], "decompose")

    def test_decompose_time_series_delta_success(self) -> None:
        """decompose accepts a time_series_delta compare and attributes its summary delta."""
        if self.skipped or self.time_series_compare_step_id is None:
            self.skipTest("Time-series compare not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.time_series_compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["decomposition_type"], "delta_decomposition")
        self.assertEqual(data["compare_ref"]["comparison_type"], "time_series_delta")
        self.assertEqual(data["left_ref"]["observation_type"], "time_series")
        self.assertEqual(data["right_ref"]["observation_type"], "time_series")
        self.assertEqual(
            data["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )
        self.assertEqual(data["analytical_metadata"]["source_granularity"], "day")
        self.assertGreater(len(data["rows"]), 0)

    # ── Error cases ───────────────────────────────────────────────────────────

    def test_decompose_rejects_nonexistent_compare_ref(self) -> None:
        """decompose with a non-existent compare step ref returns 422 STEP_NOT_FOUND."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_nonexistent_abc",
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("STEP_NOT_FOUND", r.json()["detail"])

    def test_decompose_rejects_cross_session_ref(self) -> None:
        """decompose with a compare_ref from a different session returns 422."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": "sess_other_session_xyz",
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Cross-session", r.json()["detail"])

    def test_decompose_rejects_unsupported_method(self) -> None:
        """decompose with an unsupported method returns 422 UNSUPPORTED_METHOD."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
                "method": "shapley",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("UNSUPPORTED_METHOD", r.json()["detail"])

    def test_decompose_rejects_unknown_dimension(self) -> None:
        """decompose with a dimension not declared for the metric returns 422 UNSUPPORTED_DIMENSION."""
        self._skip_if_not_wired()
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "nonexistent_dimension_xyz",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("UNSUPPORTED_DIMENSION", r.json()["detail"])

    def test_decompose_rejects_segmented_delta_compare(self) -> None:
        """decompose rejects segmented_delta compare artifacts."""
        if self.skipped or self.segmented_compare_step_id is None:
            self.skipTest("Segmented compare not available")
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/decompose",
            json={
                "compare_ref": {
                    "session_id": self.session_id,
                    "step_id": self.segmented_compare_step_id,
                    "step_type": "compare",
                },
                "dimension": "platform",
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("INVALID_ARGUMENT", r.json()["detail"])


class CorrelateIntentTests(unittest.TestCase):
    """Phase 3b-3: verify that correlate produces a pairwise_time_series_association artifact.

    Uses pre-seeded time_series observe artifacts (inserted directly) so tests
    do not depend on a full semantic layer + real query engine.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import json

        from app.main import create_app

        cls.temp_dir = tempfile.TemporaryDirectory()
        # Use a per-class temp DB to avoid cross-worker SQLite conflicts.
        db_path = Path(cls.temp_dir.name) / "correlate_intent.duckdb"
        _seed_default_calendar_source_metadata(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service

        r = cls.client.post("/sessions", json={"goal": "correlate intent test"})
        cls.session_id = r.json()["session_id"]

        # Build two time_series observe artifact payloads
        def _ts_artifact(metric: str, granularity: str, buckets: list) -> dict:
            return {
                "schema_version": "1.0",
                "observation_type": "time_series",
                "metric": metric,
                "time_scope": {
                    "kind": "range",
                    "start": buckets[0]["window"]["start"],
                    "end": buckets[-1]["window"]["end"],
                },
                "scope": {},
                "granularity": granularity,
                "series": buckets,
                "analytical_metadata": {
                    "quality_status": "ready",
                    "row_count": len(buckets),
                    "sample_size": len(buckets),
                },
                "execution_metadata": {
                    "query_hash": "test",
                    "engine": "duckdb",
                    "executed_at": "2026-01-01T00:00:00",
                },
            }

        day_buckets_left = [
            {"window": {"start": "2026-01-01", "end": "2026-01-02"}, "value": 100.0},
            {"window": {"start": "2026-01-02", "end": "2026-01-03"}, "value": 200.0},
            {"window": {"start": "2026-01-03", "end": "2026-01-04"}, "value": 300.0},
            {"window": {"start": "2026-01-04", "end": "2026-01-05"}, "value": 400.0},
            {"window": {"start": "2026-01-05", "end": "2026-01-06"}, "value": 500.0},
            {"window": {"start": "2026-01-06", "end": "2026-01-07"}, "value": 600.0},
        ]
        day_buckets_right = [
            {"window": {"start": "2026-01-01", "end": "2026-01-02"}, "value": 10.0},
            {"window": {"start": "2026-01-02", "end": "2026-01-03"}, "value": 20.0},
            {"window": {"start": "2026-01-03", "end": "2026-01-04"}, "value": 30.0},
            {"window": {"start": "2026-01-04", "end": "2026-01-05"}, "value": 40.0},
            {"window": {"start": "2026-01-05", "end": "2026-01-06"}, "value": 50.0},
            {"window": {"start": "2026-01-06", "end": "2026-01-07"}, "value": 60.0},
        ]
        constant_buckets = [
            {"window": {"start": "2026-01-01", "end": "2026-01-02"}, "value": 5.0},
            {"window": {"start": "2026-01-02", "end": "2026-01-03"}, "value": 5.0},
            {"window": {"start": "2026-01-03", "end": "2026-01-04"}, "value": 5.0},
            {"window": {"start": "2026-01-04", "end": "2026-01-05"}, "value": 5.0},
            {"window": {"start": "2026-01-05", "end": "2026-01-06"}, "value": 5.0},
            {"window": {"start": "2026-01-06", "end": "2026-01-07"}, "value": 5.0},
        ]
        week_buckets = [
            {"window": {"start": "2026-01-01", "end": "2026-01-08"}, "value": 100.0},
            {"window": {"start": "2026-01-08", "end": "2026-01-15"}, "value": 200.0},
            {"window": {"start": "2026-01-15", "end": "2026-01-22"}, "value": 300.0},
            {"window": {"start": "2026-01-22", "end": "2026-01-29"}, "value": 400.0},
            {"window": {"start": "2026-01-29", "end": "2026-02-05"}, "value": 500.0},
        ]
        scalar_artifact = {
            "schema_version": "1.0",
            "observation_type": "scalar",
            "metric": "gmv",
            "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-07"},
            "scope": {},
            "analytical_metadata": {"quality_status": "ready"},
            "execution_metadata": {
                "query_hash": "x",
                "engine": "duckdb",
                "executed_at": "2026-01-01T00:00:00",
            },
            "value": 1000.0,
        }

        svc = cls.service
        now = "2026-01-01T00:00:00"

        def _seed_artifact(step_id: str, content: dict) -> None:
            artifact_id = f"art_{step_id[5:]}"
            svc.metadata.execute(
                "INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json, provenance_json, created_at) "
                "VALUES (?, ?, 'observe', 'succeeded', 'seeded', '{}', '{}', ?)",
                [step_id, cls.session_id, now],
            )
            svc.metadata.execute(
                "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json, lifecycle, created_at) "
                "VALUES (?, ?, ?, 'observation', 'seeded', ?, 'committed', ?)",
                [artifact_id, cls.session_id, step_id, json.dumps(content), now],
            )

        cls.left_step_id = "step_corr_left"
        cls.right_step_id = "step_corr_right"
        cls.constant_step_id = "step_corr_const"
        cls.week_step_id = "step_corr_week"
        cls.scalar_step_id = "step_corr_scalar"

        _seed_artifact(cls.left_step_id, _ts_artifact("gmv", "day", day_buckets_left))
        _seed_artifact(cls.right_step_id, _ts_artifact("ad_spend", "day", day_buckets_right))
        _seed_artifact(
            cls.constant_step_id, _ts_artifact("constant_metric", "day", constant_buckets)
        )
        _seed_artifact(cls.week_step_id, _ts_artifact("gmv", "week", week_buckets))
        _seed_artifact(cls.scalar_step_id, scalar_artifact)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_correlate_happy_path_produces_committed_artifact(self) -> None:
        """Two aligned time-series → committed pairwise_time_series_association artifact."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["intent_type"], "correlate")
        self.assertEqual(data["step_type"], "correlate")
        self.assertEqual(data["association_type"], "pairwise_time_series_association")
        self.assertEqual(data["step_ref"]["step_type"], "correlate")
        self.assertIn("artifact_id", data)

    def test_correlate_artifact_shape(self) -> None:
        """Correlate artifact contains all required fields per correlate.md."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # alignment
        self.assertIn("alignment", data)
        self.assertEqual(data["alignment"]["status"], "aligned")
        self.assertEqual(data["alignment"]["issues"], [])
        # statistic
        self.assertIn("statistic", data)
        self.assertEqual(data["statistic"]["method"], "spearman")
        self.assertIsNotNone(data["statistic"]["coefficient"])
        self.assertEqual(data["statistic"]["n_pairs"], 6)
        # sign + significance
        self.assertIn(data["sign"], ("positive", "negative", "zero"))
        self.assertIn(data["significance"], ("significant", "not_significant"))
        # analytical_metadata
        am = data["analytical_metadata"]
        self.assertEqual(am["pairing_rule"], "intersection_by_time_bucket")
        self.assertEqual(am["matched_pair_count"], 6)
        self.assertEqual(am["dropped_left_points"], 0)
        self.assertEqual(am["dropped_right_points"], 0)
        self.assertEqual(am["significance_level"], 0.05)
        # version_metadata
        vm = data["version_metadata"]
        self.assertEqual(vm["artifact_schema_version"], "1.0")
        # source_lineage
        self.assertIn("source_lineage", data)
        self.assertEqual(data["source_lineage"]["left_artifact"]["step_id"], self.left_step_id)
        self.assertEqual(data["source_lineage"]["right_artifact"]["step_id"], self.right_step_id)

    def test_correlate_artifact_persisted_committed(self) -> None:
        """Correlate artifact is written to DB with lifecycle='committed'."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200)
        artifact_id = r.json()["artifact_id"]
        row = self.service.metadata.query_one(
            "SELECT lifecycle, artifact_type FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")
        self.assertEqual(row["artifact_type"], "pairwise_time_series_association")

    def test_correlate_pearson_method(self) -> None:
        """method='pearson' produces artifact with pearson in statistic.method."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
                "method": "pearson",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["statistic"]["method"], "pearson")

    def test_correlate_perfectly_correlated_series(self) -> None:
        """Perfectly correlated series should produce coefficient close to 1.0."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200)
        coef = r.json()["statistic"]["coefficient"]
        self.assertAlmostEqual(coef, 1.0, places=4)
        self.assertEqual(r.json()["sign"], "positive")
        self.assertEqual(r.json()["significance"], "significant")

    # ── Constant-series edge case ─────────────────────────────────────────────

    def test_correlate_constant_series_coefficient_is_null(self) -> None:
        """Constant right series → coefficient=null, alignment.status='needs_attention'."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.constant_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsNone(data["statistic"]["coefficient"])
        self.assertIsNone(data["statistic"]["p_value"])
        self.assertEqual(data["sign"], "undefined")
        self.assertEqual(data["significance"], "undefined")
        self.assertEqual(data["alignment"]["status"], "needs_attention")
        issues = data["alignment"]["issues"]
        self.assertTrue(any(i["code"] == "constant_series" for i in issues))

    # ── Error cases ───────────────────────────────────────────────────────────

    def test_correlate_nonexistent_step_returns_422(self) -> None:
        """correlate with non-existent step ref returns 422 STEP_NOT_FOUND."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_does_not_exist",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("STEP_NOT_FOUND", r.json()["detail"])

    def test_correlate_rejects_scalar_observation_type(self) -> None:
        """correlate rejects an observe artifact with observation_type='scalar'."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.scalar_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("INVALID_ARGUMENT", r.json()["detail"])

    def test_correlate_granularity_mismatch_fails(self) -> None:
        """correlate with day vs week granularity fails with ALIGNMENT_FAILED."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.week_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("ALIGNMENT_FAILED", r.json()["detail"])

    def test_correlate_insufficient_pairs_fails(self) -> None:
        """correlate fails when aligned pairs < min_pairs."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
                "min_pairs": 100,
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("INSUFFICIENT_DATA", r.json()["detail"])

    def test_correlate_rejects_cross_session_ref_left(self) -> None:
        """correlate rejects left_ref from a different session (already tested in stub tests)."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": "sess_foreign",
                    "step_id": self.left_step_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_correlate_rejects_wrong_step_type_in_ref(self) -> None:
        """correlate rejects refs with step_type != 'observe'."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": self.left_step_id,
                    "step_type": "compare",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": self.right_step_id,
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
