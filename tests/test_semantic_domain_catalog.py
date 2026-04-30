from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticDomainCatalogApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_semantic_domain_catalog.duckdb"
        get_seeded_duckdb_path(db_path)
        self.client = TestClient(create_app(db_path))

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _create_domain(self, domain_ref: str = "domain.growth") -> dict:
        resp = self.client.post(
            "/semantic/domains",
            json={
                "domain_ref": domain_ref,
                "display_name": "Growth",
                "description": "Growth analytics catalog domain",
                "aliases": ["growth", "acquisition"],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _create_entity(
        self,
        *,
        entity_ref: str,
        domain_ref: str = "domain.growth",
        related_domain_refs: list[str] | None = None,
    ) -> dict:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "catalog_metadata": {
                    "domain_ref": domain_ref,
                    "related_domain_refs": related_domain_refs
                    if related_domain_refs is not None
                    else ["domain.core"],
                    "aliases": ["Account"],
                },
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": "Account",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.account_id",
                            "value_type": "string",
                            "physical_column": "account_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _create_metric(self, *, metric_ref: str, entity_ref: str) -> dict:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": metric_ref,
                    "display_name": "Account Count",
                    "metric_family": "count_metric",
                    "observed_entity_ref": entity_ref,
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "account_id",
                        "semantics": "Account identifier",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _create_compatibility_profile(self, *, profile_ref: str, metric_ref: str) -> dict:
        resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {"entity_refs": ["entity.account_domain_profile"]},
                "catalog_metadata": {
                    "domain_ref": "domain.growth",
                    "related_domain_refs": ["domain.core"],
                    "aliases": ["Profile"],
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_domain_crud_and_deprecate(self) -> None:
        created = self._create_domain()

        self.assertEqual(created["domain_ref"], "domain.growth")
        self.assertEqual(created["status"], "active")
        self.assertEqual(created["aliases"], ["growth", "acquisition"])

        get_resp = self.client.get("/semantic/domains/domain.growth")
        self.assertEqual(get_resp.status_code, 200, get_resp.text)
        self.assertEqual(get_resp.json()["domain_ref"], "domain.growth")
        self.assertEqual(get_resp.json()["aliases"], ["growth", "acquisition"])

        update_resp = self.client.put(
            "/semantic/domains/domain.growth",
            json={"display_name": "Growth Analytics", "aliases": ["growth"]},
        )
        self.assertEqual(update_resp.status_code, 200, update_resp.text)
        self.assertEqual(update_resp.json()["display_name"], "Growth Analytics")
        self.assertEqual(update_resp.json()["aliases"], ["growth"])

        list_resp = self.client.get("/semantic/domains?q=analytics")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        self.assertEqual(list_resp.json()["total"], 1)

        deprecate_resp = self.client.post("/semantic/domains/domain.growth/deprecate")
        self.assertEqual(deprecate_resp.status_code, 200, deprecate_resp.text)
        self.assertEqual(deprecate_resp.json()["status"], "deprecated")

        deprecated_list_resp = self.client.get("/semantic/domains?status=deprecated")
        self.assertEqual(deprecated_list_resp.status_code, 200, deprecated_list_resp.text)
        self.assertEqual(deprecated_list_resp.json()["items"][0]["domain_ref"], "domain.growth")

    def test_semantic_object_domain_search(self) -> None:
        self._create_domain("domain.growth")
        self._create_entity(entity_ref="entity.account_domain_search", domain_ref="domain.growth")

        entity_resp = self.client.get("/semantic/entities/entity.account_domain_search")
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        self.assertEqual(
            entity_resp.json()["catalog_metadata"],
            {
                "domain_ref": "domain.growth",
                "related_domain_refs": ["domain.core"],
                "aliases": ["Account"],
            },
        )

        search_resp = self.client.get(
            "/semantic/domain-objects",
            params={
                "domain_ref": "domain.growth",
                "object_type": "entity",
                "status": "draft",
                "q": "account",
            },
        )
        self.assertEqual(search_resp.status_code, 200, search_resp.text)
        payload = search_resp.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["object_type"], "entity")
        self.assertEqual(payload["items"][0]["ref"], "entity.account_domain_search")
        self.assertEqual(payload["items"][0]["lifecycle_status"], "draft")
        self.assertEqual(payload["items"][0]["readiness_status"], "not_ready")
        self.assertEqual(payload["items"][0]["catalog_metadata"]["domain_ref"], "domain.growth")

        related_resp = self.client.get(
            "/semantic/domain-objects",
            params=[
                ("related_domain_refs", "domain.core"),
                ("object_type", "entity"),
                ("lifecycle_status", "draft"),
                ("readiness_status", "not_ready"),
            ],
        )
        self.assertEqual(related_resp.status_code, 200, related_resp.text)
        related_payload = related_resp.json()
        self.assertEqual(related_payload["total"], 1)
        self.assertEqual(related_payload["items"][0]["ref"], "entity.account_domain_search")

    def test_domain_object_search_filters_primary_domain_by_related_domains(
        self,
    ) -> None:
        self._create_domain("domain.growth")
        self._create_domain("domain.core")
        self._create_entity(
            entity_ref="entity.account_primary_domain",
            domain_ref="domain.growth",
            related_domain_refs=["domain.core"],
        )
        self._create_entity(
            entity_ref="entity.account_growth_without_related_domain",
            domain_ref="domain.growth",
            related_domain_refs=[],
        )
        self._create_entity(
            entity_ref="entity.account_other_primary_domain",
            domain_ref="domain.core",
            related_domain_refs=["domain.growth"],
        )

        related_resp = self.client.get(
            "/semantic/domain-objects",
            params=[
                ("domain_ref", "domain.growth"),
                ("related_domain_refs", "domain.core"),
                ("object_type", "entity"),
                ("q", "account"),
            ],
        )
        self.assertEqual(related_resp.status_code, 200, related_resp.text)
        refs = {item["ref"] for item in related_resp.json()["items"]}
        self.assertEqual(refs, {"entity.account_primary_domain"})

    def test_domain_object_search_includes_compatibility_profiles_and_relationships(self) -> None:
        self._create_domain("domain.growth")
        self._create_entity(
            entity_ref="entity.account_domain_profile",
            domain_ref="domain.growth",
        )
        self._create_entity(entity_ref="entity.snapshot_domain_profile")
        self._create_metric(
            metric_ref="metric.account_domain_profile",
            entity_ref="entity.account_domain_profile",
        )
        profile = self._create_compatibility_profile(
            profile_ref="compiler_profile.account_domain_profile",
            metric_ref="metric.account_domain_profile",
        )
        relationship_resp = self.client.post(
            "/semantic/relationships",
            json={
                "relationship_ref": "relationship.account_domain_profile",
                "display_name": "Account Domain Profile",
                "left_entity_ref": "entity.account_domain_profile",
                "right_entity_ref": "entity.snapshot_domain_profile",
                "key_alignment": {
                    "left_field_ref": "entity.account_domain_profile.field.account_id",
                    "right_field_ref": "entity.snapshot_domain_profile.field.account_id",
                },
                "cardinality": "many_to_one",
                "catalog_metadata": {"domain_ref": "domain.growth", "aliases": ["Profile"]},
            },
        )
        self.assertEqual(relationship_resp.status_code, 200, relationship_resp.text)

        search_resp = self.client.get(
            "/semantic/domain-objects",
            params={
                "domain_ref": "domain.growth",
                "object_type": "compatibility_profile",
                "status": "draft",
                "q": "profile",
            },
        )
        self.assertEqual(search_resp.status_code, 200, search_resp.text)
        payload = search_resp.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["object_type"], "compatibility_profile")
        self.assertEqual(payload["items"][0]["object_id"], profile["profile_id"])
        self.assertEqual(payload["items"][0]["ref"], "compiler_profile.account_domain_profile")
        self.assertEqual(payload["items"][0]["catalog_metadata"]["domain_ref"], "domain.growth")

        relationship_search_resp = self.client.get(
            "/semantic/domain-objects",
            params={
                "domain_ref": "domain.growth",
                "object_type": "relationship",
                "status": "draft",
                "q": "profile",
            },
        )
        self.assertEqual(relationship_search_resp.status_code, 200, relationship_search_resp.text)
        relationship_payload = relationship_search_resp.json()
        self.assertEqual(relationship_payload["total"], 1)
        self.assertEqual(relationship_payload["items"][0]["object_type"], "relationship")
        self.assertEqual(
            relationship_payload["items"][0]["ref"], "relationship.account_domain_profile"
        )

        binding_resp = self.client.get(
            "/semantic/domain-objects",
            params={"object_type": "binding"},
        )
        self.assertEqual(binding_resp.status_code, 422, binding_resp.text)
        self.assertIn("Unsupported semantic object_type: binding", binding_resp.text)

    def test_domain_object_search_rejects_invalid_status(self) -> None:
        self._create_domain("domain.growth")

        resp = self.client.get(
            "/semantic/domain-objects",
            params={"domain_ref": "domain.growth", "status": "active"},
        )

        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("Unsupported status filter", resp.text)

    def test_domain_object_search_rejects_invalid_related_domain_ref(self) -> None:
        self._create_domain("domain.growth")

        resp = self.client.get(
            "/semantic/domain-objects",
            params={"related_domain_refs": "core"},
        )

        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("related_domain_refs must contain domain.* refs", resp.text)
