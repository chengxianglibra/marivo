"""Predicate CRUD integration tests.

Covers create, read, list, update, validate, activate, deprecate lifecycle,
plus ref-resolvability validation and readiness evaluation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class PredicateCrudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_predicate_crud.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    # -- Helpers --

    def _create_entity(self, entity_ref: str, publish: bool = False) -> str:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": entity_ref.split(".")[-1].replace("_", " ").title(),
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.{entity_ref.split('.')[-1]}_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        entity_id = resp.json()["entity_contract_id"]
        if publish:
            pub = self.client.post(f"/semantic/entities/{entity_id}/publish")
            self.assertEqual(pub.status_code, 200, pub.text)
        return entity_id

    def _create_dimension(self, dimension_ref: str, publish: bool = False) -> str:
        resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": dimension_ref,
                    "display_name": dimension_ref.split(".")[-1].replace("_", " ").title(),
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
        self.assertEqual(resp.status_code, 200, resp.text)
        dim_id = resp.json()["dimension_contract_id"]
        if publish:
            pub = self.client.post(f"/semantic/dimensions/{dim_id}/publish")
            self.assertEqual(pub.status_code, 200, pub.text)
        return dim_id

    def _create_predicate(
        self,
        predicate_ref: str = "predicate.test_filter",
        subject_ref: str = "entity.user",
        allowed_usage: list[str] | None = None,
        expression: dict | None = None,
        publish: bool = False,
    ) -> dict:
        if allowed_usage is None:
            allowed_usage = ["metric_qualifier"]
        if expression is None:
            expression = {"target_ref": "entity.user", "op": "is_not_null"}
        resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": predicate_ref,
                    "display_name": predicate_ref.split(".")[-1].replace("_", " ").title(),
                    "subject_ref": subject_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": expression,
                    "allowed_usage": allowed_usage,
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        if publish:
            pid = data["predicate_contract_id"]
            pub = self.client.post(f"/semantic/predicates/{pid}/publish")
            self.assertEqual(pub.status_code, 200, pub.text)
            data = pub.json()
        return data

    # -- Tests --

    def test_create_predicate(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.create_test")
        self.assertEqual(data["header"]["predicate_ref"], "predicate.create_test")
        self.assertEqual(data["header"]["subject_ref"], "entity.user")
        self.assertEqual(data["status"], "draft")
        self.assertIn("expression", data["interface_contract"])

    def test_read_predicate_by_id(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.read_by_id")
        pid = data["predicate_contract_id"]
        resp = self.client.get(f"/semantic/predicates/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["predicate_contract_id"], pid)

    def test_read_predicate_by_ref(self) -> None:
        self._create_predicate(predicate_ref="predicate.read_by_ref")
        resp = self.client.get("/semantic/predicates/predicate.read_by_ref")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["header"]["predicate_ref"], "predicate.read_by_ref")

    def test_list_predicates(self) -> None:
        self._create_predicate(predicate_ref="predicate.list_test_1")
        resp = self.client.get("/semantic/predicates")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertGreaterEqual(len(items), 1)

    def test_list_predicates_filter_by_status(self) -> None:
        self._create_predicate(predicate_ref="predicate.list_draft")
        resp = self.client.get("/semantic/predicates", params={"status": "draft"})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertGreaterEqual(len(items), 1)
        for item in items:
            self.assertEqual(item["status"], "draft")

    def test_update_predicate(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.update_test")
        pid = data["predicate_contract_id"]
        resp = self.client.put(
            f"/semantic/predicates/{pid}",
            json={"display_name": "Updated Name"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["header"]["display_name"], "Updated Name")

    def test_update_predicate_interface_contract(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.update_ic")
        pid = data["predicate_contract_id"]
        resp = self.client.put(
            f"/semantic/predicates/{pid}",
            json={
                "interface_contract": {
                    "expression": {"target_ref": "entity.user", "op": "is_null"},
                    "allowed_usage": ["carrier_row_filter"],
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        ic = resp.json()["interface_contract"]
        self.assertEqual(ic["allowed_usage"], ["carrier_row_filter"])

    def test_update_published_predicate_fails(self) -> None:
        self._create_entity("entity.update_published", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.update_published",
            subject_ref="entity.update_published",
            expression={"target_ref": "entity.update_published", "op": "is_not_null"},
            publish=True,
        )
        pid = data["predicate_contract_id"]
        resp = self.client.put(
            f"/semantic/predicates/{pid}",
            json={"display_name": "Should Fail"},
        )
        self.assertNotEqual(resp.status_code, 200)

    def test_validate_predicate(self) -> None:
        self._create_entity("entity.validate_test", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.validate_test",
            subject_ref="entity.validate_test",
            expression={"target_ref": "entity.validate_test", "op": "is_not_null"},
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/validate")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["action"], "validate")

    def test_activate_predicate(self) -> None:
        self._create_entity("entity.activate_test", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.activate_test",
            subject_ref="entity.activate_test",
            expression={"target_ref": "entity.activate_test", "op": "is_not_null"},
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/activate")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

    def test_activate_with_dimension_target(self) -> None:
        self._create_entity("entity.dim_target", publish=True)
        self._create_dimension("dimension.country", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.dim_target",
            subject_ref="entity.dim_target",
            expression={"target_ref": "dimension.country", "op": "eq", "value": "US"},
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/activate")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_activate_predicate_with_unpublished_subject_fails(self) -> None:
        self._create_entity("entity.unpublished_test", publish=False)
        data = self._create_predicate(
            predicate_ref="predicate.unpublished_subject",
            subject_ref="entity.unpublished_test",
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/activate")
        self.assertNotEqual(resp.status_code, 200)

    def test_activate_with_unpublished_dimension_target_fails(self) -> None:
        self._create_entity("entity.dim_unpublished", publish=True)
        self._create_dimension("dimension.unpub_dim", publish=False)
        data = self._create_predicate(
            predicate_ref="predicate.dim_unpublished",
            subject_ref="entity.dim_unpublished",
            expression={"target_ref": "dimension.unpub_dim", "op": "eq", "value": "X"},
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/activate")
        self.assertNotEqual(resp.status_code, 200)

    def test_publish_is_alias_for_activate(self) -> None:
        self._create_entity("entity.publish_test", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.publish_test",
            subject_ref="entity.publish_test",
            expression={"target_ref": "entity.publish_test", "op": "is_not_null"},
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/publish")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "published")

    def test_deprecate_predicate(self) -> None:
        self._create_entity("entity.deprecate_test", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.deprecate_test",
            subject_ref="entity.deprecate_test",
            expression={"target_ref": "entity.deprecate_test", "op": "is_not_null"},
            publish=True,
        )
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/deprecate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deprecated")

    def test_deprecate_draft_fails(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.deprecate_draft")
        pid = data["predicate_contract_id"]
        resp = self.client.post(f"/semantic/predicates/{pid}/deprecate")
        self.assertNotEqual(resp.status_code, 200)

    def test_conjunction_expression(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.conjunction_test",
            expression={
                "op": "and",
                "items": [
                    {"target_ref": "entity.user", "op": "is_not_null"},
                    {"target_ref": "entity.user", "op": "is_null"},
                ],
            },
        )
        expr = data["interface_contract"]["expression"]
        self.assertEqual(expr["op"], "and")
        self.assertEqual(len(expr["items"]), 2)

    def test_multiple_allowed_usage(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.multi_usage",
            allowed_usage=["metric_qualifier", "request_scope"],
        )
        self.assertEqual(
            data["interface_contract"]["allowed_usage"],
            ["metric_qualifier", "request_scope"],
        )

    def test_readiness_draft_is_not_ready(self) -> None:
        data = self._create_predicate(predicate_ref="predicate.readiness_draft")
        self.assertEqual(data["readiness_status"], "not_ready")
        self.assertEqual(data["lifecycle_status"], "draft")

    def test_readiness_published_is_ready(self) -> None:
        self._create_entity("entity.readiness_ready", publish=True)
        data = self._create_predicate(
            predicate_ref="predicate.readiness_ready",
            subject_ref="entity.readiness_ready",
            expression={"target_ref": "entity.readiness_ready", "op": "is_not_null"},
            publish=True,
        )
        self.assertEqual(data["readiness_status"], "ready")
        self.assertEqual(data["lifecycle_status"], "active")

    def test_list_detail_mode(self) -> None:
        self._create_predicate(predicate_ref="predicate.detail_mode")
        resp = self.client.get("/semantic/predicates", params={"detail": True})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertGreaterEqual(len(items), 1)
        detail_items = [i for i in items if i["header"]["predicate_ref"] == "predicate.detail_mode"]
        self.assertGreaterEqual(len(detail_items), 1)
        self.assertIn("interface_contract", detail_items[0])

    def test_list_non_detail_mode_excludes_contract(self) -> None:
        self._create_predicate(predicate_ref="predicate.no_detail")
        resp = self.client.get("/semantic/predicates", params={"detail": False})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        no_detail = [i for i in items if i["header"]["predicate_ref"] == "predicate.no_detail"]
        self.assertGreaterEqual(len(no_detail), 1)
        self.assertNotIn("interface_contract", no_detail[0])

    def test_create_with_subject_ref_prefix(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.subject_prefix",
            subject_ref="subject.analysis",
        )
        self.assertEqual(data["header"]["subject_ref"], "subject.analysis")

    def test_is_null_operator(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.is_null_test",
            expression={"target_ref": "entity.user", "op": "is_null"},
        )
        expr = data["interface_contract"]["expression"]
        self.assertEqual(expr["op"], "is_null")

    def test_between_operator(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.between_test",
            expression={"target_ref": "entity.user", "op": "between", "value": [1, 100]},
        )
        expr = data["interface_contract"]["expression"]
        self.assertEqual(expr["op"], "between")
        self.assertEqual(expr["value"], [1, 100])

    def test_governance_policy_usage(self) -> None:
        data = self._create_predicate(
            predicate_ref="predicate.governance_test",
            allowed_usage=["governance_policy"],
            expression={"target_ref": "entity.user", "op": "is_not_null"},
        )
        self.assertEqual(data["interface_contract"]["allowed_usage"], ["governance_policy"])
