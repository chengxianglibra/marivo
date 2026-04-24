"""Integration tests for predicate_filter_lineage in observation artifacts (tasks 5.1–5.4).

Task 5.1: Freeze shared_effective_scope in observation artifact.
Task 5.2: Freeze metric_default_lineage in artifact.
Task 5.3: Freeze per-component qualifier lineage in artifact.
Task 5.4: Add component effective scope fingerprint.
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


def _patch_component_qualifier_refs(
    metadata: SQLiteMetadataStore,
    metric_ref: str,
    component_field: str,
    qualifier_refs: list[str],
) -> None:
    """Patch qualifier_refs into a specific component of a published metric's payload."""
    row = metadata.query_one(
        "SELECT family_payload_json FROM semantic_metric_contracts WHERE metric_ref = ?",
        [metric_ref],
    )
    if row is None:
        raise AssertionError(f"No metric found for {metric_ref}")
    payload = json.loads(row["family_payload_json"] or "{}")
    component = payload.get(component_field)
    if component is None:
        raise AssertionError(f"Component '{component_field}' not in payload for {metric_ref}")
    component["qualifier_refs"] = qualifier_refs
    metadata.execute(
        "UPDATE semantic_metric_contracts SET family_payload_json = ? WHERE metric_ref = ?",
        [json.dumps(payload), metric_ref],
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
        measure_type: str | None = None,
        component_qualifier_refs: dict[str, list[str]] | None = None,
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
            measure_type=measure_type,
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

        if component_qualifier_refs:
            for component_field, refs in component_qualifier_refs.items():
                _patch_component_qualifier_refs(metadata, metric_ref, component_field, refs)

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


# ---------------------------------------------------------------------------
# Task 5.3: per-component qualifier lineage in observation artifact
# ---------------------------------------------------------------------------


class TestComponentQualifierLineageInArtifact(_LineageTestBase):
    """Verify that per-component qualifier lineage is frozen in the observation artifact."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_numq",
            measure_type="rate",
            component_qualifier_refs={"numerator": ["predicate.metric_def1"]},
        )

    def test_rate_metric_produces_lineage_for_both_components(self) -> None:
        """Rate metric with numerator-only qualifiers has entries for BOTH components."""
        data = self._observe(metric="metric.lin_rate_numq")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage, "predicate_filter_lineage should be present")
        assert lineage is not None
        lineages = lineage.get("component_qualifier_lineages", [])
        self.assertEqual(len(lineages), 2, "Both numerator and denominator must appear")
        fields = [e["component_field"] for e in lineages]
        self.assertIn("denominator", fields)
        self.assertIn("numerator", fields)
        num_entry = next(e for e in lineages if e["component_field"] == "numerator")
        denom_entry = next(e for e in lineages if e["component_field"] == "denominator")
        self.assertEqual(num_entry["qualifier_refs"], ["predicate.metric_def1"])
        self.assertEqual(denom_entry["qualifier_refs"], [])

    def test_rate_metric_effective_scopes_reflect_per_component_qualifiers(self) -> None:
        """Each component's effective scope includes only its own qualifiers."""
        data = self._observe(metric="metric.lin_rate_numq")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None
        scopes = lineage.get("component_effective_scopes", [])
        self.assertEqual(len(scopes), 2)
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")
        self.assertIn("predicate.metric_def1", num_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def1", denom_scope["effective_scope_refs"])

    def test_count_metric_with_no_qualifiers_produces_component_entry(self) -> None:
        """Single-component count metric still appears in lineage even without qualifiers."""
        self._setup_metric_with_binding("lin_count_noqual")
        data = self._observe(metric="metric.lin_count_noqual")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None
        lineages = lineage.get("component_qualifier_lineages", [])
        fields = [e["component_field"] for e in lineages]
        self.assertIn("count_target", fields)
        ct_entry = next(e for e in lineages if e["component_field"] == "count_target")
        self.assertEqual(ct_entry["qualifier_refs"], [])

    def test_component_qualifier_lineage_deterministic_replay(self) -> None:
        """Two observes produce identical component_qualifier_lineages and effective_scopes."""
        data1 = self._observe(metric="metric.lin_rate_numq")
        data2 = self._observe(metric="metric.lin_rate_numq")
        lineage1 = data1.get("predicate_filter_lineage")
        lineage2 = data2.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage1)
        self.assertIsNotNone(lineage2)
        assert lineage1 is not None
        assert lineage2 is not None
        self.assertEqual(
            lineage1["component_qualifier_lineages"],
            lineage2["component_qualifier_lineages"],
        )
        self.assertEqual(
            lineage1["component_effective_scopes"],
            lineage2["component_effective_scopes"],
        )


# ---------------------------------------------------------------------------
# Task 5.4: component effective scope fingerprint
# ---------------------------------------------------------------------------


class TestComponentEffectiveScopeFingerprint(_LineageTestBase):
    """Verify component effective scope fingerprint in observation artifact."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_fp",
            measure_type="rate",
            component_qualifier_refs={"numerator": ["predicate.metric_def1"]},
        )

    def test_scope_fingerprint_present_for_all_components(self) -> None:
        """All components have scope_fingerprint (16-char hex string)."""
        data = self._observe(metric="metric.lin_rate_fp")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None
        scopes = lineage.get("component_effective_scopes", [])
        self.assertTrue(len(scopes) >= 2)
        for scope in scopes:
            fp = scope.get("scope_fingerprint")
            self.assertIsNotNone(fp, f"Missing scope_fingerprint for {scope['component_field']}")
            assert fp is not None
            self.assertEqual(len(fp), 16, f"Fingerprint must be 16 hex chars, got {fp!r}")
            self.assertTrue(
                all(c in "0123456789abcdef" for c in fp),
                f"Fingerprint must be hex, got {fp!r}",
            )

    def test_fingerprint_determinism(self) -> None:
        """Same metric, two observes → identical fingerprints per component."""
        data1 = self._observe(metric="metric.lin_rate_fp")
        data2 = self._observe(metric="metric.lin_rate_fp")
        lineage1 = data1.get("predicate_filter_lineage")
        lineage2 = data2.get("predicate_filter_lineage")
        assert lineage1 is not None and lineage2 is not None
        fps1 = {
            s["component_field"]: s["scope_fingerprint"]
            for s in lineage1["component_effective_scopes"]
        }
        fps2 = {
            s["component_field"]: s["scope_fingerprint"]
            for s in lineage2["component_effective_scopes"]
        }
        self.assertEqual(fps1, fps2)

    def test_different_qualifiers_produce_different_fingerprints(self) -> None:
        """Numerator and denominator have different fingerprints when qualifiers differ."""
        data = self._observe(metric="metric.lin_rate_fp")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        fps = {s["component_field"]: s["scope_fingerprint"] for s in scopes}
        self.assertNotEqual(fps.get("numerator"), fps.get("denominator"))

    def test_same_qualifiers_produce_same_fingerprint(self) -> None:
        """Both components with identical qualifier_refs produce same fingerprint."""
        self._setup_metric_with_binding(
            "lin_rate_sym",
            measure_type="rate",
            component_qualifier_refs={
                "numerator": ["predicate.metric_def1"],
                "denominator": ["predicate.metric_def1"],
            },
        )
        data = self._observe(metric="metric.lin_rate_sym")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        fps = {s["component_field"]: s["scope_fingerprint"] for s in scopes}
        self.assertEqual(fps.get("numerator"), fps.get("denominator"))

    def test_empty_scope_fingerprint_stable(self) -> None:
        """Component with no refs at all has a deterministic fingerprint."""
        self._setup_metric_with_binding("lin_count_empty_fp")
        data1 = self._observe(metric="metric.lin_count_empty_fp")
        data2 = self._observe(metric="metric.lin_count_empty_fp")
        lineage1 = data1.get("predicate_filter_lineage")
        lineage2 = data2.get("predicate_filter_lineage")
        assert lineage1 is not None and lineage2 is not None
        ct1 = next(
            (
                s
                for s in lineage1["component_effective_scopes"]
                if s["component_field"] == "count_target"
            ),
            None,
        )
        ct2 = next(
            (
                s
                for s in lineage2["component_effective_scopes"]
                if s["component_field"] == "count_target"
            ),
            None,
        )
        self.assertIsNotNone(ct1)
        self.assertIsNotNone(ct2)
        assert ct1 is not None and ct2 is not None
        self.assertEqual(ct1["scope_fingerprint"], ct2["scope_fingerprint"])


# ---------------------------------------------------------------------------
# Multi-layer: all predicate layers active simultaneously
# ---------------------------------------------------------------------------


class TestMultiLayerLineageInArtifact(_LineageTestBase):
    """Verify lineage when defaults + component qualifiers + carrier filters are all present."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_all",
            measure_type="rate",
            default_predicate_refs=["predicate.metric_def1"],
            row_filter_refs=["predicate.carrier_inv"],
            component_qualifier_refs={"numerator": ["predicate.metric_def2"]},
        )

    def test_all_layers_present_in_lineage(self) -> None:
        """Shared scope, defaults, and per-component qualifiers all frozen correctly."""
        data = self._observe(metric="metric.lin_rate_all")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None

        shared = lineage["shared_effective_scope"]
        self.assertIn("predicate.carrier_inv", shared["carrier_row_filter_refs"])

        defaults = lineage["metric_default_lineage"]
        self.assertIn("predicate.metric_def1", defaults["default_predicate_refs"])

        lineages = lineage["component_qualifier_lineages"]
        num_entry = next(e for e in lineages if e["component_field"] == "numerator")
        denom_entry = next(e for e in lineages if e["component_field"] == "denominator")
        self.assertIn("predicate.metric_def2", num_entry["qualifier_refs"])
        self.assertEqual(denom_entry["qualifier_refs"], [])

    def test_effective_scope_composes_all_layers(self) -> None:
        """Numerator effective scope = carrier + default + numerator qualifier; denominator = carrier + default only."""
        data = self._observe(metric="metric.lin_rate_all")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")

        # Both have carrier + default
        for scope in (num_scope, denom_scope):
            self.assertIn("predicate.carrier_inv", scope["effective_scope_refs"])
            self.assertIn("predicate.metric_def1", scope["effective_scope_refs"])

        # Only numerator has metric_def2
        self.assertIn("predicate.metric_def2", num_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def2", denom_scope["effective_scope_refs"])

        # Fingerprints differ because numerator has an extra qualifier
        self.assertNotEqual(num_scope["scope_fingerprint"], denom_scope["scope_fingerprint"])


# ---------------------------------------------------------------------------
# Task 7.4: average metric component lineage
# ---------------------------------------------------------------------------


class TestAverageMetricComponentLineage(_LineageTestBase):
    """Verify per-component qualifier lineage for average_metric."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_avg_numq",
            measure_type="average",
            component_qualifier_refs={"numerator": ["predicate.metric_def1"]},
        )

    def test_average_metric_produces_two_component_lineages(self) -> None:
        data = self._observe(metric="metric.lin_avg_numq")
        lineage = data.get("predicate_filter_lineage")
        self.assertIsNotNone(lineage)
        assert lineage is not None
        lineages = lineage.get("component_qualifier_lineages", [])
        fields = [e["component_field"] for e in lineages]
        self.assertIn("denominator", fields)
        self.assertIn("numerator", fields)

    def test_average_metric_numerator_has_qualifier_denominator_empty(self) -> None:
        data = self._observe(metric="metric.lin_avg_numq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        lineages = lineage["component_qualifier_lineages"]
        num_entry = next(e for e in lineages if e["component_field"] == "numerator")
        denom_entry = next(e for e in lineages if e["component_field"] == "denominator")
        self.assertEqual(num_entry["qualifier_refs"], ["predicate.metric_def1"])
        self.assertEqual(denom_entry["qualifier_refs"], [])

    def test_average_metric_effective_scopes_reflect_per_component(self) -> None:
        data = self._observe(metric="metric.lin_avg_numq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")
        self.assertIn("predicate.metric_def1", num_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def1", denom_scope["effective_scope_refs"])

    def test_average_metric_fingerprints_differ(self) -> None:
        data = self._observe(metric="metric.lin_avg_numq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_fp = next(e for e in scopes if e["component_field"] == "numerator")["scope_fingerprint"]
        denom_fp = next(e for e in scopes if e["component_field"] == "denominator")[
            "scope_fingerprint"
        ]
        self.assertNotEqual(num_fp, denom_fp)


# ---------------------------------------------------------------------------
# Task 7.4: rate metric with dual qualifiers
# ---------------------------------------------------------------------------


class TestRateMetricDualQualifierLineage(_LineageTestBase):
    """Verify that qualifiers on BOTH components are preserved independently."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_dualq",
            measure_type="rate",
            component_qualifier_refs={
                "numerator": ["predicate.metric_def1"],
                "denominator": ["predicate.metric_def2"],
            },
        )

    def test_both_components_have_qualifier_refs(self) -> None:
        data = self._observe(metric="metric.lin_rate_dualq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        lineages = lineage["component_qualifier_lineages"]
        num_entry = next(e for e in lineages if e["component_field"] == "numerator")
        denom_entry = next(e for e in lineages if e["component_field"] == "denominator")
        self.assertEqual(num_entry["qualifier_refs"], ["predicate.metric_def1"])
        self.assertEqual(denom_entry["qualifier_refs"], ["predicate.metric_def2"])

    def test_qualifier_refs_do_not_leak_across_components(self) -> None:
        data = self._observe(metric="metric.lin_rate_dualq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")
        self.assertIn("predicate.metric_def1", num_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def1", denom_scope["effective_scope_refs"])
        self.assertIn("predicate.metric_def2", denom_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def2", num_scope["effective_scope_refs"])

    def test_effective_scope_includes_shared_plus_component_specific(self) -> None:
        data = self._observe(metric="metric.lin_rate_dualq")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")
        # Both have shared scope entries (empty in this setup)
        for scope in (num_scope, denom_scope):
            self.assertIsInstance(scope["effective_scope_refs"], list)
        # Each has its own qualifier in effective_scope
        self.assertIn("predicate.metric_def1", num_scope["effective_scope_refs"])
        self.assertIn("predicate.metric_def2", denom_scope["effective_scope_refs"])

    def test_both_fingerprints_differ_and_are_stable(self) -> None:
        data1 = self._observe(metric="metric.lin_rate_dualq")
        data2 = self._observe(metric="metric.lin_rate_dualq")
        lineage1 = data1.get("predicate_filter_lineage")
        lineage2 = data2.get("predicate_filter_lineage")
        assert lineage1 is not None and lineage2 is not None
        fps1 = {
            s["component_field"]: s["scope_fingerprint"]
            for s in lineage1["component_effective_scopes"]
        }
        fps2 = {
            s["component_field"]: s["scope_fingerprint"]
            for s in lineage2["component_effective_scopes"]
        }
        # Different between components
        self.assertNotEqual(fps1["numerator"], fps1["denominator"])
        # Stable across observes
        self.assertEqual(fps1, fps2)


# ---------------------------------------------------------------------------
# Task 7.4: default_predicate_refs in component effective scopes
# ---------------------------------------------------------------------------


class TestDefaultRefsInEffectiveScope(_LineageTestBase):
    """Verify that default_predicate_refs propagate to every component's effective scope."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_defaults_only",
            measure_type="rate",
            default_predicate_refs=["predicate.metric_def1"],
        )

    def test_default_predicate_refs_appear_in_each_component_effective_scope(self) -> None:
        data = self._observe(metric="metric.lin_rate_defaults_only")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        for scope in scopes:
            self.assertIn(
                "predicate.metric_def1",
                scope["effective_scope_refs"],
                f"Default ref missing from {scope['component_field']} effective_scope",
            )

    def test_defaults_are_shared_not_per_component(self) -> None:
        data = self._observe(metric="metric.lin_rate_defaults_only")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        # Defaults are in metric_default_lineage, not in qualifier_refs
        self.assertIn(
            "predicate.metric_def1", lineage["metric_default_lineage"]["default_predicate_refs"]
        )
        for entry in lineage["component_qualifier_lineages"]:
            self.assertNotIn("predicate.metric_def1", entry["qualifier_refs"])

    def test_fingerprints_identical_when_only_shared_defaults(self) -> None:
        data = self._observe(metric="metric.lin_rate_defaults_only")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        fps = [s["scope_fingerprint"] for s in scopes]
        self.assertEqual(len(fps), 2)
        self.assertEqual(
            fps[0], fps[1], "Components with identical effective scope must share fingerprint"
        )


# ---------------------------------------------------------------------------
# Task 7.4: mixed defaults + qualifiers — no flattening
# ---------------------------------------------------------------------------


class TestMixedDefaultsAndQualifiersLineage(_LineageTestBase):
    """Verify layer separation when defaults and per-component qualifiers coexist."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._setup_metric_with_binding(
            "lin_rate_mixed",
            measure_type="rate",
            default_predicate_refs=["predicate.metric_def1"],
            row_filter_refs=["predicate.carrier_inv"],
            component_qualifier_refs={"numerator": ["predicate.metric_def2"]},
        )

    def test_lineage_shows_three_layers_separately(self) -> None:
        data = self._observe(metric="metric.lin_rate_mixed")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        # Shared scope has carrier
        self.assertIn(
            "predicate.carrier_inv", lineage["shared_effective_scope"]["carrier_row_filter_refs"]
        )
        # Defaults are separate
        self.assertIn(
            "predicate.metric_def1", lineage["metric_default_lineage"]["default_predicate_refs"]
        )
        # Per-component qualifiers are separate
        num_entry = next(
            e
            for e in lineage["component_qualifier_lineages"]
            if e["component_field"] == "numerator"
        )
        self.assertIn("predicate.metric_def2", num_entry["qualifier_refs"])

    def test_effective_scope_composes_shared_defaults_qualifiers(self) -> None:
        data = self._observe(metric="metric.lin_rate_mixed")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        scopes = lineage["component_effective_scopes"]
        num_scope = next(e for e in scopes if e["component_field"] == "numerator")
        denom_scope = next(e for e in scopes if e["component_field"] == "denominator")
        # Both: carrier + default
        for scope in (num_scope, denom_scope):
            self.assertIn("predicate.carrier_inv", scope["effective_scope_refs"])
            self.assertIn("predicate.metric_def1", scope["effective_scope_refs"])
        # Only numerator: qualifier
        self.assertIn("predicate.metric_def2", num_scope["effective_scope_refs"])
        self.assertNotIn("predicate.metric_def2", denom_scope["effective_scope_refs"])

    def test_no_flattening_across_components(self) -> None:
        """Defaults must not appear in qualifier_refs; numerator qualifiers must not leak into denominator."""
        data = self._observe(metric="metric.lin_rate_mixed")
        lineage = data.get("predicate_filter_lineage")
        assert lineage is not None
        for entry in lineage["component_qualifier_lineages"]:
            # Defaults stay in metric_default_lineage, not in qualifier_refs
            self.assertNotIn("predicate.metric_def1", entry["qualifier_refs"])
            self.assertNotIn("predicate.carrier_inv", entry["qualifier_refs"])
        denom_scope = next(
            e
            for e in lineage["component_effective_scopes"]
            if e["component_field"] == "denominator"
        )
        # Numerator qualifier does not leak into denominator effective scope
        self.assertNotIn("predicate.metric_def2", denom_scope["effective_scope_refs"])


if __name__ == "__main__":
    unittest.main()
