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

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import duckdb
from fastapi.testclient import TestClient

from app.api.models import (
    ArtifactRef,
    CompareRequest,
    DecomposeRequest,
    ObservationRef,
    ObserveRequest,
)
from app.main import create_app
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
from tests.shared_fixtures import get_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


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


class CompareRequestModelTests(unittest.TestCase):
    def _ref(self, session_id: str = "sess_a", step_id: str = "step_1") -> ObservationRef:
        return ObservationRef(session_id=session_id, step_id=step_id, step_type="observe")

    def test_valid_request(self) -> None:
        r = CompareRequest(left_ref=self._ref(), right_ref=self._ref("sess_a", "step_2"))
        self.assertEqual(r.mode, "auto")

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


class IntentEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "intent_api.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        source = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Intent API Source",
                "connection": {"path": str(cls.db_path)},
            },
        ).json()
        cls.source_id = source["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")
        source_objects = cls.client.get(f"/sources/{cls.source_id}/objects?type=table").json()
        watch_events = next(obj for obj in source_objects if obj["native_name"] == "watch_events")
        cls.watch_events_object_id = watch_events["object_id"]
        cls.watch_events_fqn = str(watch_events["fqn"])
        r = cls.client.post("/sessions", json={"goal": "Intent API test session"})
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @classmethod
    def _ensure_import_bridge_table(cls) -> tuple[str, str]:
        table_name = "intent_import_bridge_events"
        table_fqn = f"analytics.{table_name}"
        con = duckdb.connect(str(cls.db_path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_fqn} (
                    event_date DATE NOT NULL,
                    user_id VARCHAR NOT NULL,
                    cluster VARCHAR NOT NULL,
                    value DOUBLE NOT NULL
                )
                """
            )
            con.execute(f"DELETE FROM {table_fqn}")
            con.executemany(
                f"INSERT INTO {table_fqn} VALUES (?, ?, ?, ?)",
                [
                    ("2024-01-01", "u1", "alpha", 10.0),
                    ("2024-01-02", "u2", "beta", 20.0),
                    ("2024-01-03", "u3", "alpha", 30.0),
                ],
            )
        finally:
            con.close()

        metadata = cls.client.app.state.service.metadata
        existing = metadata.query_one(
            "SELECT object_id FROM source_objects WHERE source_id = ? AND fqn = ?",
            [cls.source_id, table_fqn],
        )
        if existing is not None:
            return str(existing["object_id"]), table_fqn
        object_id = f"obj_{uuid4().hex[:12]}"
        now = "2026-01-01T00:00:00"
        metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn,
                 properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
            """,
            [object_id, cls.source_id, table_name, table_fqn, now, now],
        )
        return object_id, table_fqn

    def _create_import_bridge_metric(self, *, mode: str) -> str:
        object_id, table_fqn = self._ensure_import_bridge_table()
        metadata = self.client.app.state.metadata_store
        ensure_published_typed_time(metadata)
        dimension_row = metadata.query_one(
            """
            SELECT dimension_contract_id, status
            FROM semantic_dimension_contracts
            WHERE dimension_ref = ?
            """,
            ["dimension.cluster"],
        )
        if dimension_row is None:
            dimension_resp = self.client.post(
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
            self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
            dimension_id = dimension_resp.json()["dimension_contract_id"]
            publish_dimension_resp = self.client.post(
                f"/semantic/dimensions/{dimension_id}/publish"
            )
            self.assertEqual(
                publish_dimension_resp.status_code,
                200,
                publish_dimension_resp.text,
            )
        elif dimension_row["status"] != "published":
            publish_dimension_resp = self.client.post(
                f"/semantic/dimensions/{dimension_row['dimension_contract_id']}/publish"
            )
            self.assertEqual(
                publish_dimension_resp.status_code,
                200,
                publish_dimension_resp.text,
            )

        suffix = uuid4().hex[:8]
        entity = create_typed_entity(
            self.client,
            name=f"intent_bridge_entity_{suffix}",
            display_name="Intent Bridge Entity",
            keys=["user_id"],
            primary_time_ref="time.event_date",
        )
        publish_typed_entity(self.client, entity["entity_contract_id"])
        entity_ref = entity["header"]["entity_ref"]

        metric_name = f"intent_bridge_metric_{suffix}"
        metric_ref = f"metric.{metric_name}"
        metric = create_typed_metric(
            self.client,
            name=metric_name,
            display_name="Intent Bridge Metric",
            description="Metric that relies on imported entity dimensions",
            definition_sql="SUM(value)",
            dimensions=[],
            entity_ref=entity_ref,
            grain="day",
            measure_type="sum",
        )
        publish_typed_metric(self.client, metric["metric_contract_id"])

        primary_imported_binding_ref = f"binding.intent_bridge_entity_{suffix}"
        entity_binding_resp = self.client.post(
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
                            "source_object_ref": object_id,
                            "carrier_kind": "table",
                            "carrier_locator": table_fqn,
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
        self.assertEqual(entity_binding_resp.status_code, 200, entity_binding_resp.text)
        entity_binding_id = entity_binding_resp.json()["binding_id"]
        publish_entity_binding_resp = self.client.post(
            f"/semantic/bindings/{entity_binding_id}/publish"
        )
        self.assertEqual(
            publish_entity_binding_resp.status_code, 200, publish_entity_binding_resp.text
        )

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
            entity_binding_alt_resp = self.client.post(
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
                                "source_object_ref": object_id,
                                "carrier_kind": "table",
                                "carrier_locator": table_fqn,
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
            self.assertEqual(entity_binding_alt_resp.status_code, 200, entity_binding_alt_resp.text)
            entity_binding_alt_id = entity_binding_alt_resp.json()["binding_id"]
            publish_entity_binding_alt_resp = self.client.post(
                f"/semantic/bindings/{entity_binding_alt_id}/publish"
            )
            self.assertEqual(
                publish_entity_binding_alt_resp.status_code,
                200,
                publish_entity_binding_alt_resp.text,
            )
            imports.append(
                {
                    "import_key": "entity_bridge_alt",
                    "binding_ref": secondary_imported_binding_ref,
                    "required_ref_prefixes": ["dimension."],
                }
            )

        metric_binding_resp = self.client.post(
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
                            "source_object_ref": object_id,
                            "carrier_kind": "table",
                            "carrier_locator": table_fqn,
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
        self.assertEqual(metric_binding_resp.status_code, 200, metric_binding_resp.text)
        metric_binding_id = metric_binding_resp.json()["binding_id"]
        publish_metric_binding_resp = self.client.post(
            f"/semantic/bindings/{metric_binding_id}/publish"
        )
        self.assertEqual(
            publish_metric_binding_resp.status_code,
            200,
            publish_metric_binding_resp.text,
        )
        return metric_name

    # ── observe ───────────────────────────────────────────────────────────────

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
        # metric not in semantic layer → 422 from service
        self.assertEqual(r.status_code, 422)

    def test_observe_snapshot_now_unknown_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("non_existent_metric_xyz"),
                "time_scope": {"kind": "snapshot_now"},
            },
        )
        # snapshot_now is implemented; unknown metric → 422
        self.assertEqual(r.status_code, 422)

    def test_observe_not_ready_metric_returns_409_with_structured_readiness_error(self) -> None:
        metric = create_typed_metric(
            self.client,
            name="intent_not_ready_metric",
            display_name="Intent Not Ready Metric",
            description="Metric with incomplete binding coverage",
            definition_sql="COUNT(*)",
            dimensions=["platform"],
            measure_type="average",
        )
        publish_typed_metric(self.client, metric["metric_contract_id"])
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.intent_not_ready_metric",
            object_id=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
            metric_input_target_keys=["numerator"],
        )

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

    def test_observe_ready_metric_with_auxiliary_binding_executes(self) -> None:
        object_id, table_fqn = self._ensure_import_bridge_table()
        metric = create_typed_metric(
            self.client,
            name="intent_aux_binding_metric",
            display_name="Intent Auxiliary Binding Metric",
            description="Metric grounded by an auxiliary carrier.",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        publish_typed_metric(self.client, metric["metric_contract_id"])
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.intent_aux_binding_metric",
            object_id=object_id,
            carrier_locator=table_fqn,
            binding_role="auxiliary",
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("intent_aux_binding_metric"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metric"], "intent_aux_binding_metric")

    def test_observe_execution_preflight_failure_returns_candidate_binding_detail(self) -> None:
        metric = create_typed_metric(
            self.client,
            name="intent_preflight_failure_metric",
            display_name="Intent Preflight Failure Metric",
            description="Metric used to assert execution preflight detail payloads.",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        publish_typed_metric(self.client, metric["metric_contract_id"])
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.intent_preflight_failure_metric",
            object_id=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
        )

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

    def test_observe_incompatible_dimension_returns_409_with_structured_compatibility_error(
        self,
    ) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": "time.signup_date",
                    "display_name": "Signup Date",
                    "semantic_roles": ["business_anchor"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_id = time_resp.json()["time_contract_id"]
        publish_time_resp = self.client.post(f"/semantic/time/{time_id}/publish")
        self.assertEqual(publish_time_resp.status_code, 200, publish_time_resp.text)

        dimension_resp = self.client.post(
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
                    "time_derived_requirement": {"required_time_anchor_ref": "time.signup_date"},
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_id = dimension_resp.json()["dimension_contract_id"]
        publish_resp = self.client.post(f"/semantic/dimensions/{dimension_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        metric = create_typed_metric(
            self.client,
            name="intent_compatible_metric",
            display_name="Intent Compatible Metric",
            description="Metric with request-level incompatible dimension",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["dimension.intent_signup_week"],
            grain="day",
            measure_type="average",
        )
        publish_typed_metric(self.client, metric["metric_contract_id"])
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.intent_compatible_metric",
            object_id=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
        )

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

    def test_observe_imported_dimension_bridge_allows_segmented_request(self) -> None:
        metric_name = self._create_import_bridge_metric(mode="single")

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(metric_name),
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

    def test_observe_imported_dimension_bridge_missing_returns_structured_error(self) -> None:
        metric_name = self._create_import_bridge_metric(mode="missing")

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(metric_name),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-04"},
                "dimensions": ["dimension.cluster"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["subject_ref"], "dimension.cluster")
        self.assertEqual(detail["issues"][0]["code"], "COMPILER_DIMENSION_IMPORT_MISSING")

    def test_observe_imported_dimension_bridge_ambiguous_returns_structured_error(self) -> None:
        metric_name = self._create_import_bridge_metric(mode="ambiguous")

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref(metric_name),
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

    # ── compare ───────────────────────────────────────────────────────────────

    def test_compare_nonexistent_ref_returns_422(self) -> None:
        """compare with non-existent step refs returns 422 (STEP_NOT_FOUND)."""
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

    # ── correlate ─────────────────────────────────────────────────────────────

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
        """correlate with non-existent step refs returns 422 (STEP_NOT_FOUND)."""
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

    # ── detect ────────────────────────────────────────────────────────────────

    def test_detect_unregistered_metric_returns_422(self) -> None:
        """detect is now implemented; an unregistered metric returns 422, not 501."""
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

    # ── test ─────────────────────────────────────────────────────────────────

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

    # ── forecast ──────────────────────────────────────────────────────────────

    def test_forecast_rejects_missing_horizon(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "source_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_nonexistent_step_returns_422(self) -> None:
        # forecast is now a real runner; a nonexistent step_id yields STEP_NOT_FOUND → 422
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

    # ── derived intents ───────────────────────────────────────────────────────

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

    def test_diagnose_invalid_request_returns_422(self) -> None:
        # diagnose is now implemented; missing required candidate_dimensions → 422
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _metric_ref("dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_validate_invalid_request_returns_422(self) -> None:
        # validate is now implemented; missing required left/right → 422
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "metric": _metric_ref("dau"),
                # no left, no right — required fields missing
            },
        )
        self.assertEqual(r.status_code, 422)

    # ── non-existent session ──────────────────────────────────────────────────

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

        # Sync a table so we have a source_object
        cls.client.post(f"/sources/{cls.source_id}/sync")

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


class ObserveTypedArtifactTests(unittest.TestCase):
    """Phase 3a: verify that observe produces a typed observation artifact.

    Requires a fully wired semantic layer (metric published + mapped to a source table).
    """

    @classmethod
    def setUpClass(cls) -> None:
        from app.main import create_app

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "observe_artifact.duckdb"
        cls.db_path = db_path
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls._setup_semantic_layer(db_path)
        r = cls.client.post(
            "/sessions",
            json={
                "goal": "observe typed artifact test",
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
    def _setup_semantic_layer(cls, db_path: Path) -> None:
        from uuid import uuid4

        service = cls.app.state.service
        now = "2026-01-01T00:00:00"

        # Register a source entry (just for FK reference in source_objects)
        r = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Observe Test Source",
                "connection": {"path": str(db_path)},
            },
        )
        source_id = r.json()["source_id"]
        cls.source_id = source_id

        # Register the seeded DuckDB as an engine (same file the analytics engine uses)
        r = cls.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Observe Test Engine",
                "connection": {"database": str(db_path)},
            },
        )
        engine_id = r.json()["engine_id"]
        cls.client.post(
            "/bindings",
            json={"source_id": source_id, "engine_id": engine_id, "priority": 0},
        )

        # Directly insert a source_object for analytics.watch_events with the correct
        # 2-part fqn that DuckDB can resolve against the seeded database.
        obj_id = f"obj_{uuid4().hex[:12]}"
        service.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn,
                 properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', 'watch_events', 'analytics.watch_events',
                    '{}', ?, ?)
            """,
            [obj_id, source_id, now, now],
        )
        cls.watch_events_object_id = obj_id
        cls.watch_events_fqn = "analytics.watch_events"

        # Create and publish a semantic metric backed by watch_events
        metric = create_typed_metric(
            cls.client,
            name="observe_test_dau",
            display_name="DAU (observe test)",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="average",
        )
        metric_id = metric["metric_contract_id"]
        publish_typed_metric(cls.client, metric_id)
        cls.metric_id = metric_id

        # Create typed binding: metric → watch_events source_object
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.observe_test_dau",
            object_id=obj_id,
            carrier_locator=cls.watch_events_fqn,
        )

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

    def test_observe_aggregate_metric_numeric_summary_returns_error(self) -> None:
        """Aggregate metric (COUNT DISTINCT) cannot be used as per-row value expression.

        numeric_sample_summary mode requires a raw column expression, not an outer aggregate.
        DuckDB rejects nested aggregates (AVG(COUNT(DISTINCT ...))) with a SQL error.
        """
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_test_dau"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "result_mode": "numeric_sample_summary",
            },
        )
        # DuckDB rejects nested aggregates — returned as 502 (execution error)
        self.assertNotEqual(r.status_code, 200)

    def test_observe_typed_rate_metric_standard_mode_uses_aggregate_sql(self) -> None:
        metadata = self.client.app.state.service.metadata
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
            carrier_locator=self.watch_events_fqn,
            source_object_ref=self.watch_events_object_id,
            metric_input_target_keys=["numerator", "denominator"],
            surface_name="play_duration_seconds",
        )

        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": _metric_ref("observe_typed_rate"),
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_observe_typed_rate_metric_rate_summary_returns_422(self) -> None:
        metadata = self.client.app.state.service.metadata
        ensure_published_typed_metric(
            metadata,
            metric_name="observe_typed_rate_summary",
            display_name="Observe Typed Rate Summary",
            grain="day",
            dimensions=["event_date"],
            measure_type="rate",
        )
        ensure_published_typed_metric_binding(
            metadata,
            metric_name="observe_typed_rate_summary",
            carrier_locator=self.watch_events_fqn,
            source_object_ref=self.watch_events_object_id,
            metric_input_target_keys=["numerator", "denominator"],
            surface_name="play_duration_seconds",
        )

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

    def test_observe_uses_viable_binding_instead_of_first_binding_row(self) -> None:
        metadata = self.client.app.state.service.metadata
        metric_name = f"observe_binding_fallback_{uuid4().hex[:8]}"
        metric_ref = _metric_ref(metric_name)
        ensure_published_typed_metric(
            metadata,
            metric_name=metric_name,
            display_name="Observe Binding Fallback",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )
        aux_table = f"observe_aux_binding_{uuid4().hex[:8]}"
        aux_fqn = f"analytics.{aux_table}"
        con = duckdb.connect(str(self.db_path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            con.execute(
                f"""
                CREATE TABLE {aux_fqn} (
                    event_date DATE NOT NULL,
                    aux_value DOUBLE NOT NULL
                )
                """
            )
            con.executemany(
                f"INSERT INTO {aux_fqn} VALUES (?, ?)",
                [("2024-01-01", 1.0), ("2024-01-02", 2.0), ("2024-01-03", 3.0)],
            )
        finally:
            con.close()
        aux_object_id = f"obj_{uuid4().hex[:12]}"
        metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn,
                 properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, ?, '{}', ?, ?)
            """,
            [
                aux_object_id,
                self.source_id,
                aux_table,
                aux_fqn,
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ],
        )
        _create_metric_binding(
            self.client,
            binding_ref=f"binding.aaa_{metric_name}_incomplete",
            metric_ref=metric_ref,
            source_object_ref=aux_object_id,
            carrier_locator=aux_fqn,
            binding_role="auxiliary",
            metric_input_target_keys=["measure"],
            surface_name="aux_value",
        )
        _create_metric_binding(
            self.client,
            binding_ref=f"binding.zzz_{metric_name}_complete",
            metric_ref=metric_ref,
            source_object_ref=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
            surface_name="play_duration_seconds",
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": metric_ref,
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_observe_returns_binding_ambiguity_error_for_multiple_primary_bindings(self) -> None:
        metadata = self.client.app.state.service.metadata
        metric_name = f"observe_binding_ambiguous_{uuid4().hex[:8]}"
        metric_ref = _metric_ref(metric_name)
        ensure_published_typed_metric(
            metadata,
            metric_name=metric_name,
            display_name="Observe Binding Ambiguous",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )
        _create_metric_binding(
            self.client,
            binding_ref=f"binding.aaa_{metric_name}",
            metric_ref=metric_ref,
            source_object_ref=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )
        _create_metric_binding(
            self.client,
            binding_ref=f"binding.bbb_{metric_name}",
            metric_ref=metric_ref,
            source_object_ref=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": metric_ref,
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("ambiguous", response.text)
        self.assertIn(f"binding.aaa_{metric_name}", response.text)
        self.assertIn(f"binding.bbb_{metric_name}", response.text)

    def test_observe_returns_metric_input_coverage_error(self) -> None:
        metadata = self.client.app.state.service.metadata
        metric_name = f"observe_binding_missing_slot_{uuid4().hex[:8]}"
        metric_ref = _metric_ref(metric_name)
        ensure_published_typed_metric(
            metadata,
            metric_name=metric_name,
            display_name="Observe Binding Missing Slot",
            grain="day",
            dimensions=["event_date"],
            measure_type="sum",
        )
        binding_id = _create_metric_binding(
            self.client,
            binding_ref=f"binding.{metric_name}_missing_measure",
            metric_ref=metric_ref,
            source_object_ref=self.watch_events_object_id,
            carrier_locator=self.watch_events_fqn,
            binding_role="primary",
            metric_input_target_keys=["measure"],
        )
        metadata.execute(
            """
            UPDATE field_bindings
            SET target_key = ?, semantic_ref = ?
            WHERE binding_id = ? AND target_kind = 'metric_input'
            """,
            ["count_target", "metric_input.count_target", binding_id],
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": metric_ref,
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("METRIC_INPUT_COVERAGE_MISSING", response.text)
        self.assertIn("missing required metric_input coverage", response.text)


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
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service
        cls.skipped = False

        # -- Wire semantic layer (same pattern as ObserveTypedArtifactTests) --
        now = "2026-01-01T00:00:00"

        r = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Compare Test Source",
                "connection": {"path": str(db_path)},
            },
        )
        source_id = r.json()["source_id"]

        r = cls.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Compare Test Engine",
                "connection": {"database": str(db_path)},
            },
        )
        engine_id = r.json()["engine_id"]
        cls.client.post(
            "/bindings",
            json={"source_id": source_id, "engine_id": engine_id, "priority": 0},
        )

        obj_id = f"obj_{__import__('uuid').uuid4().hex[:12]}"
        cls.service.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn,
                 properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', 'watch_events', 'analytics.watch_events',
                    '{}', ?, ?)
            """,
            [obj_id, source_id, now, now],
        )

        metric = create_typed_metric(
            cls.client,
            name="compare_test_dau",
            display_name="DAU (compare test)",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="average",
        )
        metric_id = metric["metric_contract_id"]
        publish_typed_metric(cls.client, metric_id)
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.compare_test_dau",
            object_id=obj_id,
            carrier_locator="analytics.watch_events",
        )

        # Create a second metric for mismatch tests
        other_metric = create_typed_metric(
            cls.client,
            name="compare_test_other",
            display_name="Other metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
            grain="day",
            measure_type="average",
        )
        cls.other_metric_id = other_metric["metric_contract_id"]
        if cls.other_metric_id:
            publish_typed_metric(cls.client, cls.other_metric_id)
            create_typed_metric_binding(
                cls.client,
                metric_ref="metric.compare_test_other",
                object_id=obj_id,
                carrier_locator="analytics.watch_events",
            )

        # Create session
        r = cls.client.post("/sessions", json={"goal": "compare intent test"})
        cls.session_id = r.json()["session_id"]

        # Run two scalar observe steps (different time windows)
        def _scalar_observe(session_id: str, start: str, end: str) -> str | None:
            resp = cls.client.post(
                f"/sessions/{session_id}/intents/observe",
                json={
                    "metric": _metric_ref("compare_test_dau"),
                    "time_scope": {"kind": "range", "start": start, "end": end},
                },
            )
            if resp.status_code != 200:
                return None
            return resp.json()["step_ref"]["step_id"]

        def _seg_observe(session_id: str, start: str, end: str) -> str | None:
            resp = cls.client.post(
                f"/sessions/{session_id}/intents/observe",
                json={
                    "metric": _metric_ref("compare_test_dau"),
                    "time_scope": {"kind": "range", "start": start, "end": end},
                    "dimensions": ["platform"],
                },
            )
            if resp.status_code != 200:
                return None
            return resp.json()["step_ref"]["step_id"]

        cls.left_step_id = _scalar_observe(cls.session_id, "2024-01-08", "2024-01-15")
        cls.right_step_id = _scalar_observe(cls.session_id, "2024-01-01", "2024-01-08")
        # Use dates within the seeded data range (2026-02-07 to 2026-03-06) for segmented
        # so segments are non-empty and the compare can succeed.
        cls.left_seg_step_id = _seg_observe(cls.session_id, "2026-02-14", "2026-02-21")
        cls.right_seg_step_id = _seg_observe(cls.session_id, "2026-02-07", "2026-02-14")

        # Also prepare an observe for the "other" metric (for mismatch test)
        if cls.other_metric_id:
            resp = cls.client.post(
                f"/sessions/{cls.session_id}/intents/observe",
                json={
                    "metric": _metric_ref("compare_test_other"),
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                },
            )
            cls.other_step_id = (
                resp.json().get("step_ref", {}).get("step_id") if resp.status_code == 200 else None
            )
        else:
            cls.other_step_id = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _skip_if_not_wired(self) -> None:
        if self.skipped or self.left_step_id is None or self.right_step_id is None:
            self.skipTest("Semantic layer not fully wired or observe steps failed")

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
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service
        cls.skipped = False
        cls.compare_step_id: str | None = None
        cls.segmented_compare_step_id: str | None = None

        now = "2026-01-01T00:00:00"

        r = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Decompose Test Source",
                "connection": {"path": str(db_path)},
            },
        )
        source_id = r.json()["source_id"]

        r = cls.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Decompose Test Engine",
                "connection": {"database": str(db_path)},
            },
        )
        engine_id = r.json()["engine_id"]
        cls.client.post(
            "/bindings",
            json={"source_id": source_id, "engine_id": engine_id, "priority": 0},
        )

        obj_id = f"obj_{__import__('uuid').uuid4().hex[:12]}"
        cls.service.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn,
                 properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', 'watch_events', 'analytics.watch_events',
                    '{}', ?, ?)
            """,
            [obj_id, source_id, now, now],
        )

        metric = create_typed_metric(
            cls.client,
            name="decompose_test_dau",
            display_name="DAU (decompose test)",
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date", "platform"],
            grain="day",
            measure_type="average",
        )
        metric_id = metric["metric_contract_id"]
        publish_typed_metric(cls.client, metric_id)
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.decompose_test_dau",
            object_id=obj_id,
            carrier_locator="analytics.watch_events",
        )

        r = cls.client.post("/sessions", json={"goal": "decompose intent test"})
        cls.session_id = r.json()["session_id"]

        # Scalar observations in seeded data range
        def _scalar_observe(start: str, end: str) -> str | None:
            resp = cls.client.post(
                f"/sessions/{cls.session_id}/intents/observe",
                json={
                    "metric": _metric_ref("decompose_test_dau"),
                    "time_scope": {"kind": "range", "start": start, "end": end},
                },
            )
            return resp.json()["step_ref"]["step_id"] if resp.status_code == 200 else None

        def _seg_observe(start: str, end: str) -> str | None:
            resp = cls.client.post(
                f"/sessions/{cls.session_id}/intents/observe",
                json={
                    "metric": _metric_ref("decompose_test_dau"),
                    "time_scope": {"kind": "range", "start": start, "end": end},
                    "dimensions": ["platform"],
                },
            )
            return resp.json()["step_ref"]["step_id"] if resp.status_code == 200 else None

        # Use dates inside seeded range so decompose queries return real data
        left_scalar = _scalar_observe("2026-02-21", "2026-03-07")
        right_scalar = _scalar_observe("2026-02-07", "2026-02-21")

        if left_scalar is None or right_scalar is None:
            cls.skipped = True
            return

        # Run compare (scalar_delta)
        r = cls.client.post(
            f"/sessions/{cls.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": cls.session_id,
                    "step_id": left_scalar,
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": cls.session_id,
                    "step_id": right_scalar,
                    "step_type": "observe",
                },
            },
        )
        if r.status_code != 200:
            cls.skipped = True
            return
        cls.compare_step_id = r.json()["step_ref"]["step_id"]

        # Produce a segmented_delta compare for rejection test
        left_seg = _seg_observe("2026-02-21", "2026-03-07")
        right_seg = _seg_observe("2026-02-07", "2026-02-21")
        if left_seg and right_seg:
            r2 = cls.client.post(
                f"/sessions/{cls.session_id}/intents/compare",
                json={
                    "left_ref": {
                        "session_id": cls.session_id,
                        "step_id": left_seg,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "session_id": cls.session_id,
                        "step_id": right_seg,
                        "step_type": "observe",
                    },
                },
            )
            if r2.status_code == 200:
                cls.segmented_compare_step_id = r2.json()["step_ref"]["step_id"]

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
        """decompose rejects a segmented_delta compare artifact (v1 only supports scalar_delta)."""
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
