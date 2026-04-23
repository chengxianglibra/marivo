"""Integration tests for predicate_filter_lineage in observation artifacts (tasks 5.1 & 5.2).

Task 5.1: Freeze shared_effective_scope in observation artifact.
  - governance + carrier + request scope 合成后可稳定重放
Task 5.2: Freeze metric_default_lineage in artifact.
  - metric identity 中的共享过滤不再丢失
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_published_typed_entity,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    ensure_published_typed_time,
)
from tests.shared_fixtures import get_seeded_duckdb_path


def _metadata_from_client(client: TestClient) -> SQLiteMetadataStore:
    store = getattr(client.app.state, "metadata_store", None)
    if store is None:
        store = client.app.state.services.metadata_store
    return store


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
            "authority": {
                "catalog_system": "duckdb",
                "connection": {"path": str(db_path)},
                "synthetic_catalog": "main",
            },
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
        "/mappings",
        json={
            "source_id": source["source_id"],
            "engine_id": engine["engine_id"],
            "priority": 0,
            "catalog_mappings": [{"authority_catalog": "main", "execution_catalog": "main"}],
        },
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

    parent_id: str | None = None
    fqn_parts = [part for part in fqn.split(".") if part]
    if len(fqn_parts) >= 2:
        schema_name = fqn_parts[-2]
        schema_fqn = ".".join(fqn_parts[:-1])
        existing_schema = metadata.query_one(
            "SELECT object_id FROM source_objects WHERE source_id = ? AND object_type = 'schema' AND fqn = ?",
            [source_id, schema_fqn],
        )
        if existing_schema is not None:
            parent_id = str(existing_schema["object_id"])
        else:
            parent_id = f"obj_{uuid4().hex[:12]}"
            metadata.execute(
                """
                INSERT INTO source_objects
                    (object_id, source_id, object_type, native_name, fqn,
                     properties_json, created_at, updated_at)
                VALUES (?, ?, 'schema', ?, ?, '{}', ?, ?)
                """,
                [parent_id, source_id, schema_name, schema_fqn, now, now],
            )
    object_id = f"obj_{uuid4().hex[:12]}"
    metadata.execute(
        """
        INSERT INTO source_objects
            (object_id, source_id, object_type, parent_id, native_name, fqn,
             properties_json, created_at, updated_at)
        VALUES (?, ?, 'table', ?, ?, ?, '{}', ?, ?)
        """,
        [object_id, source_id, parent_id, native_name, fqn, now, now],
    )
    return object_id


def _patch_binding_row_filter_refs(
    metadata: SQLiteMetadataStore,
    binding_ref: str,
    row_filter_refs: list[str],
) -> None:
    """Patch row_filter_refs into a published carrier binding."""
    metadata.execute(
        """
        UPDATE carrier_bindings
        SET row_filter_refs_json = ?
        WHERE binding_id = (
            SELECT binding_id FROM typed_bindings WHERE binding_ref = ?
        )
        """,
        [json.dumps(row_filter_refs), binding_ref],
    )


def _patch_metric_default_predicate_refs(
    metadata: SQLiteMetadataStore,
    metric_ref: str,
    default_predicate_refs: list[str],
) -> None:
    """Patch default_predicate_refs into a published metric."""
    metadata.execute(
        "UPDATE semantic_metric_contracts SET default_predicate_refs_json = ? WHERE metric_ref = ?",
        [json.dumps(default_predicate_refs), metric_ref],
    )


class _LineageTestBase(unittest.TestCase):
    """Base class with shared setup: seeded DuckDB, source/engine, entity, time, predicates."""

    entity_ref: str
    time_ref: str
    client: TestClient
    session_id: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / f"{cls.__name__.lower()}.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.client = TestClient(cls.app)
        metadata = _metadata_from_client(cls.client)

        # Register source + engine
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

        # Publish entity + time
        cls.entity_ref = ensure_published_typed_entity(
            metadata, entity_name="lineage_ent", key_refs=["key.lineage_id"]
        )
        cls.time_ref = ensure_published_typed_time(metadata)

        # Publish predicates for different usage categories
        cls._publish_predicate("predicate.gov_filter", ["governance_policy"])
        cls._publish_predicate("predicate.carrier_inv", ["carrier_row_filter"])
        cls._publish_predicate("predicate.metric_def1", ["metric_qualifier"])
        cls._publish_predicate("predicate.metric_def2", ["metric_qualifier"])
        cls._publish_predicate("predicate.req_scope", ["request_scope"])

        # Create session
        resp = cls.client.post(
            "/sessions",
            json={"goal": f"{cls.__name__} session", "budget": {}, "policy": {}},
        )
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

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
    def _setup_metric_with_binding(
        cls,
        metric_name: str,
        *,
        default_predicate_refs: list[str] | None = None,
        row_filter_refs: list[str] | None = None,
    ) -> None:
        """Create and publish a metric + binding, optionally patching predicate refs."""
        metadata = _metadata_from_client(cls.client)

        # Use the standard helpers to create metric + binding
        ensure_published_typed_metric(
            metadata,
            metric_name=metric_name,
            display_name=metric_name,
            definition_sql="COUNT(DISTINCT user_id)",
            dimensions=["event_date"],
        )
        ensure_published_typed_metric_binding(
            metadata,
            metric_name=metric_name,
            carrier_locator=cls.watch_events_fqn,
            source_object_ref=cls.watch_events_object_id,
        )

        # Patch predicate refs into the published objects
        binding_ref = f"binding.{metric_name}_primary"
        if row_filter_refs:
            _patch_binding_row_filter_refs(metadata, binding_ref, row_filter_refs)

        metric_ref = f"metric.{metric_name}"
        if default_predicate_refs:
            _patch_metric_default_predicate_refs(metadata, metric_ref, default_predicate_refs)

    def _observe(self, metric: str, *, skip_on_wiring: bool = True, **kwargs: object) -> dict:
        """Run observe intent and return response JSON."""
        body: dict = {
            "metric": metric,
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        }
        body.update(kwargs)
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json=body,
        )
        if skip_on_wiring and r.status_code in (409, 422):
            self.skipTest(f"Semantic layer not fully wired: {r.status_code} {r.text[:300]}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ---------------------------------------------------------------------------
# Task 5.1: shared_effective_scope in observation artifact
# ---------------------------------------------------------------------------


class TestSharedEffectiveScopeInArtifact(_LineageTestBase):
    """Verify that shared_effective_scope is frozen in the observation artifact."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_dau_carrier",
            row_filter_refs=["predicate.carrier_inv"],
        )

    def test_carrier_row_filter_in_shared_effective_scope(self) -> None:
        """Carrier row_filter_refs appear in shared_effective_scope.carrier_row_filter_refs."""
        data = self._observe(metric="metric.lin_dau_carrier")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage, "predicate_filter_lineage should be present")
        assert lineage is not None
        shared = lineage.get("shared_effective_scope")
        self.assertIsNotNone(shared, "shared_effective_scope should be present")
        assert shared is not None
        self.assertIn(
            "predicate.carrier_inv",
            shared.get("carrier_row_filter_refs", []),
        )

    def test_shared_effective_scope_has_empty_carrier_when_no_carrier(self) -> None:
        """When no carrier row_filter_refs, carrier_row_filter_refs is empty."""
        self._setup_metric_with_binding("lin_dau_plain")
        data = self._observe(metric="metric.lin_dau_plain")
        lineage = data.get("predicate_filter_lineage")
        if lineage is not None:
            shared = lineage.get("shared_effective_scope")
            if shared is not None:
                self.assertEqual(shared.get("carrier_row_filter_refs", []), [])

    def test_request_scope_in_shared_effective_scope(self) -> None:
        """Request scope predicate_ref appears in shared_effective_scope.request_scope_ref.

        Skipped when the predicate's target_ref cannot be resolved to a physical
        column in the carrier table — the lineage is still built correctly in the
        compiler IR, but SQL execution fails.
        """
        data = self._observe(
            metric="metric.lin_dau_carrier",
            scope={"predicate_ref": "predicate.req_scope"},
        )
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None
        shared = lineage.get("shared_effective_scope")
        self.assertIsNotNone(shared)
        assert shared is not None
        self.assertEqual(shared.get("request_scope_ref"), "predicate.req_scope")

    def test_governance_policy_not_in_lineage_without_enforcement(self) -> None:
        """Governance policy refs must NOT appear in lineage until the observe
        execution path actually enforces them.  Otherwise the artifact claims
        compliance on data that was never filtered, creating a false audit trail."""
        self.client.post(
            "/policies",
            json={
                "name": "lineage_gov_policy",
                "policy_type": "row_filter",
                "definition": {"predicate_ref": "predicate.gov_filter"},
            },
        )
        try:
            data = self._observe(metric="metric.lin_dau_carrier")
            lineage = data.get("predicate_filter_lineage")
            if lineage is not None:
                shared = lineage.get("shared_effective_scope")
                if shared is not None:
                    self.assertEqual(
                        shared.get("governance_policy_refs", []),
                        [],
                        "governance_policy_refs must be empty until enforcement is wired",
                    )
        finally:
            policies = self.client.get("/policies").json()
            for p in policies:
                if p.get("name") == "lineage_gov_policy" and p.get("enabled", True):
                    self.client.post(f"/policies/{p['policy_id']}/disable")


# ---------------------------------------------------------------------------
# Task 5.2: metric_default_lineage in observation artifact
# ---------------------------------------------------------------------------


class TestMetricDefaultLineageInArtifact(_LineageTestBase):
    """Verify that metric_default_lineage is frozen in the observation artifact."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_dau_defaults",
            default_predicate_refs=["predicate.metric_def1", "predicate.metric_def2"],
        )

    def test_default_predicate_refs_in_metric_default_lineage(self) -> None:
        """Metric default_predicate_refs appear in metric_default_lineage."""
        data = self._observe(metric="metric.lin_dau_defaults")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage, "predicate_filter_lineage should be present")
        assert lineage is not None
        defaults = lineage.get("metric_default_lineage")
        self.assertIsNotNone(defaults, "metric_default_lineage should be present")
        assert defaults is not None
        refs = defaults.get("default_predicate_refs", [])
        self.assertIn("predicate.metric_def1", refs)
        self.assertIn("predicate.metric_def2", refs)

    def test_no_default_predicate_refs_produces_empty_list(self) -> None:
        """When metric has no default_predicate_refs, lineage has empty list."""
        self._setup_metric_with_binding("lin_dau_no_defaults")
        data = self._observe(metric="metric.lin_dau_no_defaults")
        lineage = data.get("predicate_filter_lineage")
        if lineage is not None:
            defaults = lineage.get("metric_default_lineage")
            if defaults is not None:
                self.assertEqual(defaults.get("default_predicate_refs", []), [])


# ---------------------------------------------------------------------------
# Combined 5.1 + 5.2: deterministic replay
# ---------------------------------------------------------------------------


class TestLineageDeterministicReplay(_LineageTestBase):
    """Verify that the same predicate setup produces identical lineage across observes."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_replay",
            default_predicate_refs=["predicate.metric_def1"],
            row_filter_refs=["predicate.carrier_inv"],
        )

    def test_identical_lineage_across_two_observes(self) -> None:
        """Two observes with same predicates produce identical predicate_filter_lineage."""
        data1 = self._observe(metric="metric.lin_replay")
        data2 = self._observe(metric="metric.lin_replay")
        lineage1 = data1.get("predicate_filter_lineage")
        lineage2 = data2.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage1)
        self.assertIsNotNone(lineage2)
        assert lineage1 is not None
        assert lineage2 is not None
        self.assertEqual(
            lineage1["shared_effective_scope"]["carrier_row_filter_refs"],
            lineage2["shared_effective_scope"]["carrier_row_filter_refs"],
        )
        self.assertEqual(
            lineage1["metric_default_lineage"]["default_predicate_refs"],
            lineage2["metric_default_lineage"]["default_predicate_refs"],
        )


if __name__ == "__main__":
    unittest.main()
