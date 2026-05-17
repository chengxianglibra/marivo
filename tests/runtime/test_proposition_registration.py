"""Tests for proposition registration runtime (Phase 4e-2).

Acceptance criteria:
- ``register_system_seeded_proposition`` raises ``ValueError`` for
  non-system_seeded origin.
- Registration CREATE: proposition persisted, seed refs written, created=True.
- Registration HIT: no writes, same proposition_id returned, created=False.
- Registration HIT: ``seed_finding_refs`` NOT appended (junction count stable).
- ``derivation_version`` bump → new proposition; old proposition preserved.
- Multiple proposition types never share identity_keys.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.adapters.server.evidence_repositories import FindingRepository, PropositionRepository
from marivo.core.evidence.proposition_normalizer import (
    make_proposition_id,
    normalize_proposition_identity,
)
from marivo.runtime.evidence.proposition_registration import (
    PropositionRegistrationResult,
    register_system_seeded_proposition,
)
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


def _insert_session(store: SQLiteMetadataStore, session_id: str = "sess_4e2") -> None:
    store.execute(
        "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [session_id, "test", "{}", "{}", "open"],
    )


def _insert_artifact(
    store: SQLiteMetadataStore,
    artifact_id: str = "art_4e2_001",
    session_id: str = "sess_4e2",
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [artifact_id, session_id, "step_4e2_001", "compare_artifact", "cmp", "{}"],
    )


def _insert_finding(
    store: SQLiteMetadataStore,
    finding_id: str = "fnd_4e2_delta_001",
    session_id: str = "sess_4e2",
    artifact_id: str = "art_4e2_001",
) -> None:
    FindingRepository(store).create(
        {
            "finding_id": finding_id,
            "session_id": session_id,
            "artifact_id": artifact_id,
            "step_ref_json": json.dumps(
                {"session_id": session_id, "step_id": "step_4e2_001", "step_type": "compare"}
            ),
            "finding_type": "delta",
            "canonical_item_key": "result",
            "subject_json": json.dumps(
                {
                    "metric": "dau",
                    "entity": None,
                    "slice": {},
                    "grain": "day",
                    "analysis_axis": "scalar",
                }
            ),
            "observed_window_json": None,
            "quality_json": json.dumps(
                {
                    "data_complete": True,
                    "sample_size": None,
                    "row_count": None,
                    "null_rate": None,
                    "quality_status": "ready",
                    "quality_warnings": [],
                }
            ),
            "provenance_json": json.dumps(
                {
                    "source_step_type": "compare",
                    "extractor_name": "compare_extractor",
                    "extractor_version": "v1",
                    "artifact_schema_version": "v1",
                    "canonical_item_key": "result",
                    "artifact_item_ref": {"collection": "result", "index": None, "key": None},
                    "projection_ref": None,
                }
            ),
            "payload_json": json.dumps(
                {
                    "delta_kind": "scalar_delta",
                    "current_ref": {
                        "artifact_id": artifact_id,
                        "item_ref": {"collection": "result", "index": None, "key": None},
                    },
                    "baseline_ref": {
                        "artifact_id": artifact_id,
                        "item_ref": {"collection": "result", "index": None, "key": None},
                    },
                    "current_value": 1000.0,
                    "baseline_value": 900.0,
                    "absolute_delta": -100.0,
                    "relative_delta": -0.1,
                    "direction": "decrease",
                    "presence": "both",
                    "unit": "users",
                }
            ),
            "schema_version": "v1",
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SESSION = "sess_4e2"
_ART_ID = "art_4e2_001"
_FND_ID = "fnd_4e2_delta_001"

_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}

_SUBJECT_CHANGE: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "change",
}

_SUBJECT_FORECAST: dict[str, Any] = {
    "metric": "dau",
    "entity": None,
    "slice": {},
    "grain": "day",
    "analysis_axis": "forecast",
}

_PAYLOAD_CHANGE: dict[str, Any] = {
    "change_kind": "scalar_change",
    "comparison_window": {"current": _LEFT_WIN, "baseline": _RIGHT_WIN},
    "direction_of_interest": "decrease",
    "dimension_keys": None,
    "comparison_basis": "left_vs_right",
    "unit": "users",
}

_PAYLOAD_FORECAST: dict[str, Any] = {
    "forecast_kind": "point_forecast",
    "forecast_window": {"kind": "range", "start": "2024-02-01", "end": "2024-02-02"},
    "horizon_index": 3,
    "expectation_direction": "open",
    "forecast_basis_ref": None,
}

_ORIGIN_SEEDED: dict[str, Any] = {
    "kind": "system_seeded",
    "template_id": "seed.change_from_delta.v1",
    "template_version": "1.0.0",
}

_ORIGIN_AUTHORED: dict[str, Any] = {
    "kind": "agent_authored",
    "author_type": "agent",
    "authored_label": None,
    "authored_input_ref": None,
}

_ASSESSMENT_ANCHOR: dict[str, Any] = {"assessment_type": "change_assessment"}

_LINEAGE: dict[str, Any] = {
    "creation_mode": "seeded",
    "source_artifact_lineages": [
        {"artifact_id": _ART_ID, "artifact_schema_version": "v1", "extractor_version": "v1"}
    ],
    "source_step_refs": [
        {"session_id": _SESSION, "step_id": "step_4e2_001", "step_type": "compare"}
    ],
    "derived_from_proposition_ref": None,
    "derivation_version": "seed.change_from_delta.identity.v1",
}

_SEED_REFS: list[dict[str, Any]] = [
    {"finding_ref": {"session_id": _SESSION, "finding_id": _FND_ID}, "role": "primary"}
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegistration(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_finding(self.store)
        self.repo = PropositionRepository(self.store)

    def _register(
        self,
        *,
        lineage: dict[str, Any] | None = None,
        subject: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        seed_finding_refs: list[dict[str, Any]] | None = None,
    ) -> PropositionRegistrationResult:
        return register_system_seeded_proposition(
            self.repo,
            session_id=_SESSION,
            proposition_type="change",
            subject=subject or _SUBJECT_CHANGE,
            origin=_ORIGIN_SEEDED,
            assessment_anchor=_ASSESSMENT_ANCHOR,
            lineage=lineage or _LINEAGE,
            payload=payload or _PAYLOAD_CHANGE,
            seed_finding_refs=seed_finding_refs if seed_finding_refs is not None else _SEED_REFS,
        )

    def test_agent_authored_origin_raises(self) -> None:
        """register_system_seeded_proposition must reject non-system_seeded origin."""
        with self.assertRaises(ValueError):
            register_system_seeded_proposition(
                self.repo,
                session_id=_SESSION,
                proposition_type="change",
                subject=_SUBJECT_CHANGE,
                origin=_ORIGIN_AUTHORED,
                assessment_anchor=_ASSESSMENT_ANCHOR,
                lineage=_LINEAGE,
                payload=_PAYLOAD_CHANGE,
                seed_finding_refs=_SEED_REFS,
            )

    def test_create_returns_created_true(self) -> None:
        result = self._register()
        self.assertIs(result["created"], True)

    def test_create_proposition_id_starts_with_prop(self) -> None:
        result = self._register()
        self.assertTrue(result["proposition_id"].startswith("prop_"))

    def test_create_proposition_persisted(self) -> None:
        result = self._register()
        row = self.repo.get(result["proposition_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["proposition_type"], "change")

    def test_create_identity_key_stored(self) -> None:
        result = self._register()
        row = self.repo.get(result["proposition_id"])
        self.assertNotEqual(row["identity_key"], "")

    def test_create_seed_finding_refs_written_to_junction(self) -> None:
        result = self._register()
        refs = self.repo.get_seed_finding_refs(result["proposition_id"])
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["finding_id"], _FND_ID)
        self.assertEqual(refs[0]["role"], "primary")

    def test_create_seed_finding_refs_json_stored(self) -> None:
        result = self._register()
        row = self.repo.get(result["proposition_id"])
        stored = row["seed_finding_refs_json"]  # already deserialized by repository
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["finding_ref"]["finding_id"], _FND_ID)

    def test_hit_returns_created_false(self) -> None:
        self._register()
        result2 = self._register()
        self.assertIs(result2["created"], False)

    def test_hit_same_proposition_id(self) -> None:
        r1 = self._register()
        r2 = self._register()
        self.assertEqual(r1["proposition_id"], r2["proposition_id"])

    def test_hit_does_not_write_seed_finding_refs(self) -> None:
        """Junction table count must be unchanged after a HIT."""
        r1 = self._register()
        count_before = len(self.repo.get_seed_finding_refs(r1["proposition_id"]))

        self._register()
        count_after = len(self.repo.get_seed_finding_refs(r1["proposition_id"]))
        self.assertEqual(count_before, count_after)

    def test_proposition_id_matches_normalizer(self) -> None:
        """proposition_id returned must equal make_proposition_id(normalize_proposition_identity(...))."""
        ik = normalize_proposition_identity(
            session_id=_SESSION,
            origin_kind="system_seeded",
            proposition_type="change",
            derivation_version=_LINEAGE["derivation_version"],
            subject=_SUBJECT_CHANGE,
            payload=_PAYLOAD_CHANGE,
        )
        expected_id = make_proposition_id(ik)
        result = self._register()
        self.assertEqual(result["proposition_id"], expected_id)

    def test_derivation_version_bump_creates_new_proposition(self) -> None:
        """Breaking template upgrade must produce a new proposition_id; old one must remain."""
        r_v1 = self._register()
        lineage_v2 = {**_LINEAGE, "derivation_version": "seed.change_from_delta.identity.v2"}
        r_v2 = self._register(lineage=lineage_v2)

        self.assertNotEqual(r_v1["proposition_id"], r_v2["proposition_id"])
        self.assertIs(r_v2["created"], True)

        # Old proposition still present
        self.assertIsNotNone(self.repo.get(r_v1["proposition_id"]))

    def test_multiple_proposition_types_no_cross_contamination(self) -> None:
        """Propositions of different types must have distinct identity_keys."""
        ik_change = normalize_proposition_identity(
            session_id=_SESSION,
            origin_kind="system_seeded",
            proposition_type="change",
            derivation_version="seed.change_from_delta.identity.v1",
            subject=_SUBJECT_CHANGE,
            payload=_PAYLOAD_CHANGE,
        )
        ik_forecast = normalize_proposition_identity(
            session_id=_SESSION,
            origin_kind="system_seeded",
            proposition_type="forecast",
            derivation_version="seed.forecast_from_point.identity.v1",
            subject=_SUBJECT_FORECAST,
            payload=_PAYLOAD_FORECAST,
        )
        self.assertNotEqual(ik_change, ik_forecast)
        self.assertNotEqual(
            make_proposition_id(ik_change),
            make_proposition_id(ik_forecast),
        )

    def test_hit_does_not_create_new_row(self) -> None:
        self._register()
        count_before = len(self.repo.list_by_session(_SESSION))
        self._register()
        count_after = len(self.repo.list_by_session(_SESSION))
        self.assertEqual(count_before, count_after)

    def test_empty_seed_finding_refs(self) -> None:
        """A proposition may be registered with no seed finding refs."""
        result = self._register(seed_finding_refs=[])
        self.assertIs(result["created"], True)
        refs = self.repo.get_seed_finding_refs(result["proposition_id"])
        self.assertEqual(refs, [])


if __name__ == "__main__":
    unittest.main()
