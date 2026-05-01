"""Predicate usage validation integration tests.

Covers usage-aware validation at every predicate consumption point:
- CarrierBinding.row_filter_refs → carrier_row_filter usage
- MeasurementComponent.qualifier_refs → metric_qualifier usage
- MetricHeader.default_predicate_refs → metric_qualifier usage
- ObserveScope.predicate_ref → request_scope usage
- Governance policy predicate_ref → governance_policy usage
- Draft binding row_filter_refs existence validation
- Predicate ref resolution to SQL filter expression
- Request scope usage enforcement for all wrong-usage predicates (task 7.2)
- Governance usage enforcement for all wrong-usage predicates (task 7.2)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.models.intents import ObserveScope
from app.main import create_app
from app.semantic_service.errors import SemanticValidationError
from tests.semantic_test_helpers import ensure_published_typed_entity, ensure_published_typed_time
from tests.shared_fixtures import get_seeded_duckdb_path


def _metadata_from_client(client: TestClient):
    store = getattr(client.app.state, "metadata_store", None)
    if store is None:
        store = client.app.state.services.metadata_store
    return store


# =============================================================================
# Task 3.3: Binding row_filter_refs and metric predicate refs
# =============================================================================


class BindingRowFilterUsageTests(unittest.TestCase):
    """Test that row_filter_refs requires carrier_row_filter usage."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_row_filter_usage.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="rf_test", key_refs=["key.rf_test_id"]
        )
        cls._publish_predicate("predicate.carrier_inv", ["carrier_row_filter"])
        cls._publish_predicate("predicate.metric_biz", ["metric_qualifier"])
        cls._publish_predicate("predicate.dual_use", ["metric_qualifier", "carrier_row_filter"])
        cls._publish_predicate("predicate.gov_only", ["governance_policy"])

    @classmethod
    def _publish_predicate(cls, ref: str, usage: list[str]) -> None:
        resp = cls.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": cls.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"target_ref": cls.entity_ref, "op": "is_not_null"},
                    "allowed_usage": usage,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["predicate_contract_id"]
        pub = cls.client.post(f"/semantic/predicates/{pid}/publish")
        assert pub.status_code == 200, pub.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _make_binding(self, binding_ref: str, row_filter_refs: list[str] | None = None):
        entity_name = self.entity_ref.split(".")[-1]
        carrier: dict = {
            "binding_key": "primary",
            "carrier_kind": "table",
            "carrier_locator": "analytics.test",
            "binding_role": "primary",
            "field_surfaces": [
                {"surface_ref": "field.test_id", "physical_name": "test_id"},
            ],
        }
        if row_filter_refs is not None:
            carrier["row_filter_refs"] = row_filter_refs
        return self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": binding_ref,
                    "display_name": binding_ref.split(".")[-1].replace("_", " "),
                    "binding_scope": "entity",
                    "bound_object_ref": self.entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [carrier],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.{entity_name}_id",
                            },
                            "semantic_ref": f"key.{entity_name}_id",
                            "surface_ref": "field.test_id",
                        },
                    ],
                },
            },
        )

    def test_row_filter_refs_accept_carrier_row_filter_usage(self) -> None:
        resp = self._make_binding("binding.rf_ok", ["predicate.carrier_inv"])
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_row_filter_refs_reject_metric_qualifier_usage(self) -> None:
        resp = self._make_binding("binding.rf_bad", ["predicate.metric_biz"])
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn("carrier_row_filter", resp.text)

    def test_row_filter_refs_accept_dual_usage_predicate(self) -> None:
        resp = self._make_binding("binding.rf_dual", ["predicate.dual_use"])
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_empty_row_filter_refs_no_error(self) -> None:
        resp = self._make_binding("binding.rf_none")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_governance_only_predicate_rejected_in_row_filter(self) -> None:
        resp = self._make_binding("binding.rf_gov", ["predicate.gov_only"])
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn("carrier_row_filter", resp.text)

    def test_draft_binding_rejects_nonexistent_row_filter_ref(self) -> None:
        resp = self._make_binding("binding.rf_typo", ["predicate.nonexistent"])
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn("predicate", resp.text.lower())


class MetricPredicateUsageTests(unittest.TestCase):
    """Test that qualifier_refs and default_predicate_refs require metric_qualifier usage."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_metric_pred_usage.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="mp_test", key_refs=["key.mp_test_id"]
        )
        cls.time_ref = ensure_published_typed_time(metadata)
        cls._publish_predicate("predicate.mp_qual", ["metric_qualifier"])
        cls._publish_predicate("predicate.mp_carrier", ["carrier_row_filter"])

    @classmethod
    def _publish_predicate(cls, ref: str, usage: list[str]) -> None:
        resp = cls.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": cls.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"target_ref": cls.entity_ref, "op": "is_not_null"},
                    "allowed_usage": usage,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["predicate_contract_id"]
        pub = cls.client.post(f"/semantic/predicates/{pid}/publish")
        assert pub.status_code == 200, pub.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_count_metric(
        self,
        metric_ref: str,
        qualifier_refs: list[str] | None = None,
        default_predicate_refs: list[str] | None = None,
    ):
        header: dict = {
            "metric_ref": metric_ref,
            "display_name": metric_ref.split(".")[-1].replace("_", " "),
            "metric_family": "count_metric",
            "observed_entity_ref": self.entity_ref,
            "observation_grain_ref": "grain.row",
            "sample_kind": "numeric",
            "value_semantics": "count",
            "aggregation_scope": "event",
            "primary_time_ref": self.time_ref,
            "additivity_constraints": {
                "dimension_policy": "none",
                "time_axis_policy": "non_additive",
            },
            "metric_contract_version": "metric.v1",
        }
        if default_predicate_refs is not None:
            header["default_predicate_refs"] = default_predicate_refs
        payload: dict = {
            "metric_family": "count_metric",
            "count_target": {
                "name": "events",
                "semantics": "count of events",
                "aggregation": "count",
            },
        }
        if qualifier_refs is not None:
            payload["count_target"]["qualifier_refs"] = qualifier_refs
        return self.client.post(
            "/semantic/metrics",
            json={"header": header, "payload": payload},
        )

    def test_qualifier_refs_accept_metric_qualifier_usage(self) -> None:
        resp = self._create_count_metric("metric.qual_ok", qualifier_refs=["predicate.mp_qual"])
        self.assertEqual(resp.status_code, 200, resp.text)
        mid = resp.json()["metric_contract_id"]
        val = self.client.post(f"/semantic/metrics/{mid}/validate")
        self.assertEqual(val.status_code, 200, val.text)

    def test_qualifier_refs_reject_carrier_row_filter_usage(self) -> None:
        resp = self._create_count_metric("metric.qual_bad", qualifier_refs=["predicate.mp_carrier"])
        self.assertEqual(resp.status_code, 200, resp.text)
        mid = resp.json()["metric_contract_id"]
        val = self.client.post(f"/semantic/metrics/{mid}/validate")
        self.assertNotEqual(val.status_code, 200)
        self.assertIn("metric_qualifier", val.text)

    def test_default_predicate_refs_require_metric_qualifier(self) -> None:
        resp = self._create_count_metric(
            "metric.default_bad",
            default_predicate_refs=["predicate.mp_carrier"],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        mid = resp.json()["metric_contract_id"]
        val = self.client.post(f"/semantic/metrics/{mid}/validate")
        self.assertNotEqual(val.status_code, 200)
        self.assertIn("metric_qualifier", val.text)

    def test_default_predicate_refs_persisted(self) -> None:
        resp = self._create_count_metric(
            "metric.default_persist",
            default_predicate_refs=["predicate.mp_qual"],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["header"].get("default_predicate_refs"), ["predicate.mp_qual"])


# =============================================================================
# Task 3.4: Request scope predicate_ref
# =============================================================================


class ObserveScopePredicateRefTests(unittest.TestCase):
    """Test ObserveScope.predicate_ref model-level validation."""

    def test_predicate_ref_accepts_valid_ref(self) -> None:
        scope = ObserveScope(predicate_ref="predicate.test_scope")
        self.assertEqual(scope.predicate_ref, "predicate.test_scope")

    def test_predicate_ref_rejects_non_predicate_prefix(self) -> None:
        with self.assertRaises(Exception):
            ObserveScope(predicate_ref="entity.user")

    def test_predicate_and_predicate_ref_mutual_exclusion(self) -> None:
        with self.assertRaises(Exception):
            ObserveScope(
                predicate={"target_ref": "entity.user", "op": "is_not_null"},
                predicate_ref="predicate.test_scope",
            )

    def test_none_both_is_valid(self) -> None:
        scope = ObserveScope()
        self.assertIsNone(scope.predicate)
        self.assertIsNone(scope.predicate_ref)


# =============================================================================
# Task 3.5: Governance filter boundary
# =============================================================================


class GovernanceFilterBoundaryTests(unittest.TestCase):
    """Test governance_policy usage is properly isolated from other contexts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_gov_boundary.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="gov_test", key_refs=["key.gov_test_id"]
        )
        cls.time_ref = ensure_published_typed_time(metadata)
        cls._publish_predicate("predicate.gov_filter", ["governance_policy"])
        cls._publish_predicate("predicate.gov_carrier", ["governance_policy", "carrier_row_filter"])

    @classmethod
    def _publish_predicate(cls, ref: str, usage: list[str]) -> None:
        resp = cls.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": cls.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"target_ref": cls.entity_ref, "op": "is_not_null"},
                    "allowed_usage": usage,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["predicate_contract_id"]
        pub = cls.client.post(f"/semantic/predicates/{pid}/publish")
        assert pub.status_code == 200, pub.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_governance_only_predicate_rejected_in_metric_qualifier(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.gov_qual_bad",
                    "display_name": "Gov Qual Bad",
                    "metric_family": "count_metric",
                    "observed_entity_ref": self.entity_ref,
                    "observation_grain_ref": "grain.row",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "aggregation_scope": "event",
                    "primary_time_ref": self.time_ref,
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "events",
                        "semantics": "count of events",
                        "aggregation": "count",
                        "qualifier_refs": ["predicate.gov_filter"],
                    },
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        mid = resp.json()["metric_contract_id"]
        val = self.client.post(f"/semantic/metrics/{mid}/validate")
        self.assertNotEqual(val.status_code, 200)
        self.assertIn("metric_qualifier", val.text)

    def test_multi_usage_with_governance_accepted_in_carrier(self) -> None:
        entity_name = self.entity_ref.split(".")[-1]
        carrier: dict = {
            "binding_key": "primary",
            "carrier_kind": "table",
            "carrier_locator": "analytics.test",
            "binding_role": "primary",
            "row_filter_refs": ["predicate.gov_carrier"],
            "field_surfaces": [
                {"surface_ref": "field.test_id", "physical_name": "test_id"},
            ],
        }
        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.gov_carrier_ok",
                    "display_name": "Gov Carrier OK",
                    "binding_scope": "entity",
                    "bound_object_ref": self.entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [carrier],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.{entity_name}_id",
                            },
                            "semantic_ref": f"key.{entity_name}_id",
                            "surface_ref": "field.test_id",
                        },
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_governance_policy_with_predicate_ref(self) -> None:
        resp = self.client.post(
            "/policies",
            json={
                "name": "test_gov_predicate_ref",
                "policy_type": "row_filter",
                "definition": {"predicate_ref": "predicate.gov_filter"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["definition"]["predicate_ref"], "predicate.gov_filter")


# =============================================================================
# P1: Predicate ref resolution to SQL filter
# =============================================================================


class PredicateRefResolutionTests(unittest.TestCase):
    """Test that predicate_ref resolves to a SQL expression, not a raw ref string."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_pred_ref_resolve.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="pr_test", key_refs=["key.pr_test_id"]
        )
        cls.svc = cls.client.app.state.service

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _publish_predicate(self, ref: str, usage: list[str], expression: dict) -> str:
        resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": self.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": expression,
                    "allowed_usage": usage,
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        pid = resp.json()["predicate_contract_id"]
        pub = self.client.post(f"/semantic/predicates/{pid}/publish")
        self.assertEqual(pub.status_code, 200, pub.text)
        return pid

    def test_resolve_atom_to_sql(self) -> None:
        self._publish_predicate(
            "predicate.resolve_atom",
            ["request_scope"],
            {"target_ref": self.entity_ref, "op": "is_not_null"},
        )
        result = self.svc._resolve_predicate_ref_to_filter("predicate.resolve_atom")
        self.assertIn("IS NOT NULL", result)

    def test_resolve_nonexistent_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.svc._resolve_predicate_ref_to_filter("predicate.nonexistent")

    def test_normalize_scope_predicate_ref_not_copied_to_predicate(self) -> None:
        from app.time_scope import _normalize_scope

        scope = _normalize_scope({"predicate_ref": "predicate.test", "constraints": {}})
        self.assertEqual(scope.predicate_ref, "predicate.test")
        self.assertIsNone(scope.predicate)


# =============================================================================
# Task 7.2: Request scope usage enforcement
# =============================================================================


class RequestScopeUsageEnforcementTests(unittest.TestCase):
    """Test that only request_scope usage predicates are accepted as scope.predicate_ref."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_scope_usage_enforce.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="scope_test", key_refs=["key.scope_test_id"]
        )
        cls.semantic_svc = cls.client.app.state.semantic_service
        cls._publish_predicate("predicate.scope_req", ["request_scope"])
        cls._publish_predicate("predicate.scope_gov", ["governance_policy"])
        cls._publish_predicate("predicate.scope_carrier", ["carrier_row_filter"])
        cls._publish_predicate("predicate.scope_metric", ["metric_qualifier"])

    @classmethod
    def _publish_predicate(cls, ref: str, usage: list[str]) -> None:
        resp = cls.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": cls.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"target_ref": cls.entity_ref, "op": "is_not_null"},
                    "allowed_usage": usage,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["predicate_contract_id"]
        pub = cls.client.post(f"/semantic/predicates/{pid}/publish")
        assert pub.status_code == 200, pub.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_governance_predicate_rejected_as_request_scope(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_request_scope_predicate_ref(
                "predicate.scope_gov"
            )

    def test_carrier_row_filter_rejected_as_request_scope(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_request_scope_predicate_ref(
                "predicate.scope_carrier"
            )

    def test_metric_qualifier_rejected_as_request_scope(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_request_scope_predicate_ref(
                "predicate.scope_metric"
            )

    def test_request_scope_predicate_accepted_in_scope_context(self) -> None:
        self.semantic_svc.typed_objects._validate_request_scope_predicate_ref("predicate.scope_req")


# =============================================================================
# Task 7.2: Governance usage enforcement
# =============================================================================


class GovernanceUsageEnforcementTests(unittest.TestCase):
    """Test that only governance_policy usage predicates are accepted in governance context."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_gov_usage_enforce.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="gov_ue_test", key_refs=["key.gov_ue_test_id"]
        )
        cls.semantic_svc = cls.client.app.state.semantic_service
        cls._publish_predicate("predicate.gov_req", ["governance_policy"])
        cls._publish_predicate("predicate.gov_scope", ["request_scope"])
        cls._publish_predicate("predicate.gov_carrier", ["carrier_row_filter"])
        cls._publish_predicate("predicate.gov_metric", ["metric_qualifier"])

    @classmethod
    def _publish_predicate(cls, ref: str, usage: list[str]) -> None:
        resp = cls.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": ref,
                    "display_name": ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": cls.entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"target_ref": cls.entity_ref, "op": "is_not_null"},
                    "allowed_usage": usage,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["predicate_contract_id"]
        pub = cls.client.post(f"/semantic/predicates/{pid}/publish")
        assert pub.status_code == 200, pub.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_request_scope_predicate_rejected_as_governance(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_governance_predicate_refs(
                ["predicate.gov_scope"]
            )

    def test_carrier_row_filter_predicate_rejected_as_governance(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_governance_predicate_refs(
                ["predicate.gov_carrier"]
            )

    def test_metric_qualifier_predicate_rejected_as_governance(self) -> None:
        with self.assertRaises(SemanticValidationError):
            self.semantic_svc.typed_objects._validate_governance_predicate_refs(
                ["predicate.gov_metric"]
            )

    def test_governance_policy_predicate_accepted(self) -> None:
        self.semantic_svc.typed_objects._validate_governance_predicate_refs(["predicate.gov_req"])
