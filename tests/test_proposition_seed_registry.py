"""Tests for the proposition seeding template registry (Phase 4e-1).

Acceptance criteria:
- SeedTemplateBase, SingleFindingSeedTemplateSpec, CompositeSeedTemplateSpec,
  SeedSlotSpec have the expected fields.
- register() raises ValueError on duplicate template_id; override=True replaces.
- get() raises KeyError for unknown template_id.
- find_by_finding_type() routes each of the 6 v1 finding types to exactly 1 template.
- find_by_finding_type("observation") returns [].
- find_by_finding_type(unknown) returns [].
- snapshot() is sorted by template_id, contains all required keys.
- default_seed_registry singleton has exactly 6 templates bootstrapped.
- derivation_version is stable across repeated get() calls.
"""

from __future__ import annotations

import unittest

from marivo.runtime.evidence.proposition_seed_registry import (
    CompositeSeedTemplateSpec,
    SeedSlotSpec,
    SeedTemplateBase,
    SeedTemplateRegistry,
    SeedTemplateSpec,
    SingleFindingSeedTemplateSpec,
    TriggerFindingType,
    default_seed_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_TEMPLATE_IDS = sorted(
    [
        "seed.change_from_delta.v1",
        "seed.decomposition_from_item.v1",
        "seed.anomaly_from_candidate.v1",
        "seed.correlation_from_result.v1",
        "seed.test_hypothesis_from_result.v1",
        "seed.forecast_from_point.v1",
    ]
)

_FINDING_TYPE_TO_TEMPLATE_ID = {
    "delta": "seed.change_from_delta.v1",
    "decomposition_item": "seed.decomposition_from_item.v1",
    "anomaly_candidate": "seed.anomaly_from_candidate.v1",
    "correlation_result": "seed.correlation_from_result.v1",
    "test_result": "seed.test_hypothesis_from_result.v1",
    "forecast_point": "seed.forecast_from_point.v1",
}

_REQUIRED_SNAPSHOT_KEYS = {
    "template_id",
    "template_version",
    "derivation_version",
    "proposition_type",
    "assessment_type",
    "schema_version",
    "match_mode",
}


def _make_single_template(
    template_id: str = "test.stub.v1",
    trigger_finding_type: str = "delta",
) -> SingleFindingSeedTemplateSpec:
    return SingleFindingSeedTemplateSpec(
        template_id=template_id,
        template_version="1.0.0",
        derivation_version=f"{template_id}.identity.v1",
        proposition_type="change",
        assessment_type="change_assessment",
        schema_version="v1",
        match_mode="single_finding",
        trigger_finding_type=trigger_finding_type,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# TypedDict field contract
# ---------------------------------------------------------------------------


class TestSeedTemplateBaseFields(unittest.TestCase):
    """SeedTemplateBase declares all required fields."""

    def _make_base(self) -> SeedTemplateBase:
        return SeedTemplateBase(
            template_id="t.base",
            template_version="1.0.0",
            derivation_version="t.base.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version="v1",
            match_mode="single_finding",
        )

    def test_template_id_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["template_id"], "t.base")

    def test_template_version_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["template_version"], "1.0.0")

    def test_derivation_version_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["derivation_version"], "t.base.identity.v1")

    def test_proposition_type_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["proposition_type"], "change")

    def test_assessment_type_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["assessment_type"], "change_assessment")

    def test_schema_version_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["schema_version"], "v1")

    def test_match_mode_field(self) -> None:
        t = self._make_base()
        self.assertEqual(t["match_mode"], "single_finding")


class TestSingleFindingSeedTemplateSpecFields(unittest.TestCase):
    """SingleFindingSeedTemplateSpec adds trigger_finding_type."""

    def test_trigger_finding_type_field(self) -> None:
        t = _make_single_template(trigger_finding_type="delta")
        self.assertEqual(t["trigger_finding_type"], "delta")

    def test_match_mode_is_single_finding(self) -> None:
        t = _make_single_template()
        self.assertEqual(t["match_mode"], "single_finding")

    def test_inherits_base_fields(self) -> None:
        t = _make_single_template(template_id="x.v1")
        self.assertIn("template_id", t)
        self.assertIn("derivation_version", t)
        self.assertIn("proposition_type", t)


class TestCompositeSeedTemplateSpecFields(unittest.TestCase):
    """CompositeSeedTemplateSpec includes slots, trigger_slot, group_key."""

    def _make_composite(self) -> CompositeSeedTemplateSpec:
        return CompositeSeedTemplateSpec(
            template_id="test.composite.v1",
            template_version="1.0.0",
            derivation_version="test.composite.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version="v1",
            match_mode="composite",
            trigger_slot="primary_delta",
            slots=[],
            group_key="subject.metric",
        )

    def test_match_mode_is_composite(self) -> None:
        t = self._make_composite()
        self.assertEqual(t["match_mode"], "composite")

    def test_trigger_slot_field(self) -> None:
        t = self._make_composite()
        self.assertEqual(t["trigger_slot"], "primary_delta")

    def test_group_key_field(self) -> None:
        t = self._make_composite()
        self.assertEqual(t["group_key"], "subject.metric")

    def test_slots_field_is_list(self) -> None:
        t = self._make_composite()
        self.assertIsInstance(t["slots"], list)


class TestSeedSlotSpecFields(unittest.TestCase):
    """SeedSlotSpec carries the required slot declaration fields."""

    def _make_slot(self) -> SeedSlotSpec:
        return SeedSlotSpec(
            slot_name="primary_delta",
            finding_type="delta",
            required=True,
            cardinality="one",
            role="primary",
            match_predicates=["subject.metric == trigger.subject.metric"],
            sort_key="finding_id",
        )

    def test_slot_name(self) -> None:
        s = self._make_slot()
        self.assertEqual(s["slot_name"], "primary_delta")

    def test_finding_type(self) -> None:
        s = self._make_slot()
        self.assertEqual(s["finding_type"], "delta")

    def test_required(self) -> None:
        s = self._make_slot()
        self.assertTrue(s["required"])

    def test_cardinality(self) -> None:
        s = self._make_slot()
        self.assertEqual(s["cardinality"], "one")

    def test_role(self) -> None:
        s = self._make_slot()
        self.assertEqual(s["role"], "primary")

    def test_match_predicates_is_list(self) -> None:
        s = self._make_slot()
        self.assertIsInstance(s["match_predicates"], list)

    def test_sort_key(self) -> None:
        s = self._make_slot()
        self.assertEqual(s["sort_key"], "finding_id")


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


class TestSeedTemplateRegistryBasic(unittest.TestCase):
    """register / get / registered_template_ids invariants on a fresh registry."""

    def setUp(self) -> None:
        self.registry = SeedTemplateRegistry()

    def test_register_and_get_roundtrip(self) -> None:
        t = _make_single_template("t.roundtrip.v1")
        self.registry.register(t)
        retrieved = self.registry.get("t.roundtrip.v1")
        self.assertEqual(retrieved["template_id"], "t.roundtrip.v1")

    def test_register_duplicate_raises_value_error(self) -> None:
        t = _make_single_template("t.dup.v1")
        self.registry.register(t)
        with self.assertRaises(ValueError) as ctx:
            self.registry.register(t)
        self.assertIn("t.dup.v1", str(ctx.exception))

    def test_register_duplicate_with_override_succeeds(self) -> None:
        t1 = _make_single_template("t.override.v1")
        t2 = SingleFindingSeedTemplateSpec(
            template_id="t.override.v1",
            template_version="2.0.0",
            derivation_version="t.override.identity.v2",
            proposition_type="anomaly",
            assessment_type="anomaly_assessment",
            schema_version="v1",
            match_mode="single_finding",
            trigger_finding_type="anomaly_candidate",
        )
        self.registry.register(t1)
        self.registry.register(t2, override=True)
        retrieved = self.registry.get("t.override.v1")
        self.assertEqual(retrieved["proposition_type"], "anomaly")

    def test_get_unknown_raises_key_error(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            self.registry.get("nonexistent.template.v1")
        self.assertIn("nonexistent.template.v1", str(ctx.exception))

    def test_registered_template_ids_empty_initially(self) -> None:
        self.assertEqual(self.registry.registered_template_ids(), [])

    def test_registered_template_ids_sorted(self) -> None:
        self.registry.register(_make_single_template("z.v1"))
        self.registry.register(_make_single_template("a.v1"))
        ids = self.registry.registered_template_ids()
        self.assertEqual(ids, sorted(ids))

    def test_registered_template_ids_unique(self) -> None:
        self.registry.register(_make_single_template("u.v1"))
        self.registry.register(_make_single_template("v.v1"))
        ids = self.registry.registered_template_ids()
        self.assertEqual(len(ids), len(set(ids)))


# ---------------------------------------------------------------------------
# find_by_finding_type routing
# ---------------------------------------------------------------------------


class TestFindByFindingType(unittest.TestCase):
    """find_by_finding_type routes correctly on a fresh registry and default_seed_registry."""

    def setUp(self) -> None:
        self.registry = SeedTemplateRegistry()

    def test_empty_registry_returns_empty_list(self) -> None:
        self.assertEqual(self.registry.find_by_finding_type("delta"), [])

    def test_registered_template_appears_in_routing(self) -> None:
        t = _make_single_template("t.route.v1", trigger_finding_type="delta")
        self.registry.register(t)
        result = self.registry.find_by_finding_type("delta")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "t.route.v1")

    def test_observation_always_returns_empty(self) -> None:
        # Even after registering templates for other types, observation stays empty.
        self.registry.register(_make_single_template("t.obs.v1", trigger_finding_type="delta"))
        self.assertEqual(self.registry.find_by_finding_type("observation"), [])

    def test_unknown_finding_type_returns_empty(self) -> None:
        self.assertEqual(self.registry.find_by_finding_type("nonexistent_type"), [])

    def test_result_is_stable_sorted_by_template_id(self) -> None:
        # Register two templates for the same finding_type; result should be sorted.
        t1 = SingleFindingSeedTemplateSpec(
            template_id="z.route.v1",
            template_version="1.0.0",
            derivation_version="z.route.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version="v1",
            match_mode="single_finding",
            trigger_finding_type="delta",
        )
        t2 = SingleFindingSeedTemplateSpec(
            template_id="a.route.v1",
            template_version="1.0.0",
            derivation_version="a.route.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version="v1",
            match_mode="single_finding",
            trigger_finding_type="delta",
        )
        self.registry.register(t1)
        self.registry.register(t2)
        result = self.registry.find_by_finding_type("delta")
        ids = [t["template_id"] for t in result]
        self.assertEqual(ids, sorted(ids))

    def test_override_updates_secondary_index(self) -> None:
        t1 = _make_single_template("t.idx.v1", trigger_finding_type="delta")
        t2 = SingleFindingSeedTemplateSpec(
            template_id="t.idx.v1",
            template_version="2.0.0",
            derivation_version="t.idx.identity.v2",
            proposition_type="anomaly",
            assessment_type="anomaly_assessment",
            schema_version="v1",
            match_mode="single_finding",
            trigger_finding_type="anomaly_candidate",
        )
        self.registry.register(t1)
        self.registry.register(t2, override=True)

        # Old delta entry must be gone.
        self.assertEqual(self.registry.find_by_finding_type("delta"), [])
        # New anomaly_candidate entry must be present.
        result = self.registry.find_by_finding_type("anomaly_candidate")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "t.idx.v1")


# ---------------------------------------------------------------------------
# snapshot() stability
# ---------------------------------------------------------------------------


class TestSnapshot(unittest.TestCase):
    """snapshot() returns a sorted, complete representation."""

    def setUp(self) -> None:
        self.registry = SeedTemplateRegistry()

    def test_empty_registry_snapshot_is_empty_list(self) -> None:
        self.assertEqual(self.registry.snapshot(), [])

    def test_snapshot_is_sorted_by_template_id(self) -> None:
        self.registry.register(_make_single_template("z.snap.v1"))
        self.registry.register(_make_single_template("a.snap.v1"))
        snap = self.registry.snapshot()
        ids = [e["template_id"] for e in snap]
        self.assertEqual(ids, sorted(ids))

    def test_snapshot_entry_contains_required_keys(self) -> None:
        self.registry.register(_make_single_template("t.keys.v1"))
        snap = self.registry.snapshot()
        self.assertEqual(len(snap), 1)
        for key in _REQUIRED_SNAPSHOT_KEYS:
            self.assertIn(key, snap[0], f"Missing snapshot key: {key}")

    def test_snapshot_single_finding_entry_has_trigger_finding_type(self) -> None:
        self.registry.register(_make_single_template("t.sftype.v1", trigger_finding_type="delta"))
        snap = self.registry.snapshot()
        self.assertIn("trigger_finding_type", snap[0])
        self.assertEqual(snap[0]["trigger_finding_type"], "delta")

    def test_snapshot_composite_entry_has_trigger_slot_group_key_and_slots(self) -> None:
        slot: SeedSlotSpec = SeedSlotSpec(
            slot_name="primary_delta",
            finding_type="delta",
            required=True,
            cardinality="one",
            role="primary",
            match_predicates=[],
            sort_key="finding_id",
        )
        comp = CompositeSeedTemplateSpec(
            template_id="t.comp.v1",
            template_version="1.0.0",
            derivation_version="t.comp.identity.v1",
            proposition_type="change",
            assessment_type="change_assessment",
            schema_version="v1",
            match_mode="composite",
            trigger_slot="primary",
            slots=[slot],
            group_key="subject.metric",
        )
        self.registry.register(comp)
        snap = self.registry.snapshot()
        self.assertIn("trigger_slot", snap[0])
        self.assertIn("group_key", snap[0])
        self.assertIn("slots", snap[0])
        self.assertEqual(snap[0]["slots"], [slot])


# ---------------------------------------------------------------------------
# default_seed_registry singleton
# ---------------------------------------------------------------------------


class TestDefaultSeedRegistrySingleton(unittest.TestCase):
    """default_seed_registry is importable and has exactly 6 v1 templates."""

    def test_is_instance_of_seed_template_registry(self) -> None:
        self.assertIsInstance(default_seed_registry, SeedTemplateRegistry)

    def test_has_exactly_six_templates(self) -> None:
        self.assertEqual(len(default_seed_registry.registered_template_ids()), 6)

    def test_registered_ids_match_expected(self) -> None:
        self.assertEqual(
            default_seed_registry.registered_template_ids(),
            _EXPECTED_TEMPLATE_IDS,
        )

    def test_snapshot_has_six_entries(self) -> None:
        snap = default_seed_registry.snapshot()
        self.assertEqual(len(snap), 6)

    def test_snapshot_all_entries_have_required_keys(self) -> None:
        for entry in default_seed_registry.snapshot():
            for key in _REQUIRED_SNAPSHOT_KEYS:
                self.assertIn(key, entry, f"Entry {entry.get('template_id')!r} missing key {key!r}")


# ---------------------------------------------------------------------------
# v1 routing: each finding type routes to exactly 1 template
# ---------------------------------------------------------------------------


class TestV1RoutingAllFindingTypes(unittest.TestCase):
    """Each of the 6 v1 trigger finding types routes to exactly one template."""

    def test_delta_routes_to_change_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("delta")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.change_from_delta.v1")

    def test_decomposition_item_routes_to_decomposition_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("decomposition_item")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.decomposition_from_item.v1")

    def test_anomaly_candidate_routes_to_anomaly_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("anomaly_candidate")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.anomaly_from_candidate.v1")

    def test_correlation_result_routes_to_correlation_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("correlation_result")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.correlation_from_result.v1")

    def test_test_result_routes_to_test_hypothesis_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("test_result")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.test_hypothesis_from_result.v1")

    def test_forecast_point_routes_to_forecast_template(self) -> None:
        result = default_seed_registry.find_by_finding_type("forecast_point")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["template_id"], "seed.forecast_from_point.v1")

    def test_observation_returns_empty_list(self) -> None:
        result = default_seed_registry.find_by_finding_type("observation")
        self.assertEqual(result, [])

    def test_unknown_finding_type_returns_empty_list(self) -> None:
        result = default_seed_registry.find_by_finding_type("not_a_real_type")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# v1 template field contract
# ---------------------------------------------------------------------------


class TestV1TemplateFieldContract(unittest.TestCase):
    """Each v1 template has the correct proposition_type, assessment_type, derivation_version."""

    def _get(self, tid: str) -> SeedTemplateSpec:
        return default_seed_registry.get(tid)

    def test_change_template_proposition_type(self) -> None:
        t = self._get("seed.change_from_delta.v1")
        self.assertEqual(t["proposition_type"], "change")

    def test_change_template_assessment_type(self) -> None:
        t = self._get("seed.change_from_delta.v1")
        self.assertEqual(t["assessment_type"], "change_assessment")

    def test_change_template_derivation_version(self) -> None:
        t = self._get("seed.change_from_delta.v1")
        self.assertEqual(t["derivation_version"], "seed.change_from_delta.identity.v1")

    def test_decomposition_template_proposition_type(self) -> None:
        t = self._get("seed.decomposition_from_item.v1")
        self.assertEqual(t["proposition_type"], "decomposition")

    def test_anomaly_template_proposition_type(self) -> None:
        t = self._get("seed.anomaly_from_candidate.v1")
        self.assertEqual(t["proposition_type"], "anomaly")

    def test_correlation_template_proposition_type(self) -> None:
        t = self._get("seed.correlation_from_result.v1")
        self.assertEqual(t["proposition_type"], "correlation")

    def test_test_hypothesis_template_proposition_type(self) -> None:
        t = self._get("seed.test_hypothesis_from_result.v1")
        self.assertEqual(t["proposition_type"], "test_hypothesis")

    def test_forecast_template_proposition_type(self) -> None:
        t = self._get("seed.forecast_from_point.v1")
        self.assertEqual(t["proposition_type"], "forecast")

    def test_all_v1_templates_have_schema_version_v1(self) -> None:
        for tid in _EXPECTED_TEMPLATE_IDS:
            t = self._get(tid)
            self.assertEqual(t["schema_version"], "v1", f"{tid} schema_version != 'v1'")

    def test_all_v1_templates_are_single_finding(self) -> None:
        for tid in _EXPECTED_TEMPLATE_IDS:
            t = self._get(tid)
            self.assertEqual(
                t["match_mode"], "single_finding", f"{tid} match_mode != 'single_finding'"
            )


# ---------------------------------------------------------------------------
# TriggerFindingType type alias
# ---------------------------------------------------------------------------


class TestTriggerFindingType(unittest.TestCase):
    """TriggerFindingType excludes observation and covers all 6 trigger types."""

    def _literal_args(self) -> set[str]:
        import typing

        return set(typing.get_args(TriggerFindingType))

    def test_observation_excluded(self) -> None:
        self.assertNotIn("observation", self._literal_args())

    def test_all_six_trigger_types_present(self) -> None:
        expected = {
            "delta",
            "decomposition_item",
            "anomaly_candidate",
            "correlation_result",
            "test_result",
            "forecast_point",
        }
        self.assertEqual(self._literal_args(), expected)


# ---------------------------------------------------------------------------
# derivation_version stability (replay/audit invariant)
# ---------------------------------------------------------------------------


class TestDerivationVersionStability(unittest.TestCase):
    """derivation_version is stable across repeated get() calls."""

    def test_derivation_version_stable_across_calls(self) -> None:
        tid = "seed.change_from_delta.v1"
        v1 = default_seed_registry.get(tid)["derivation_version"]
        v2 = default_seed_registry.get(tid)["derivation_version"]
        self.assertEqual(v1, v2)

    def test_derivation_version_unique_across_templates(self) -> None:
        versions = [
            default_seed_registry.get(tid)["derivation_version"] for tid in _EXPECTED_TEMPLATE_IDS
        ]
        self.assertEqual(len(versions), len(set(versions)))

    def test_snapshot_derivation_version_matches_get(self) -> None:
        for entry in default_seed_registry.snapshot():
            tid = entry["template_id"]
            expected = default_seed_registry.get(tid)["derivation_version"]
            self.assertEqual(entry["derivation_version"], expected)
