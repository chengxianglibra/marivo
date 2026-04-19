"""Tests for proposition seeding run orchestration (Phase 4e-3).

Acceptance criteria:
- Same trigger_finding_ids + same committed finding state + same registry
  → same affected_proposition_ids in the same order.
- affected_proposition_ids contains BOTH created and existing (hit) propositions.
- Creation condition failures produce no proposition and no error.
- Running the seeding run twice with the same inputs: second run has
  created_proposition_ids = [], affected_proposition_ids unchanged.
- observation findings (no template) → empty SeedingRunResult.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.evidence_engine.proposition_seeding_run import (
    SEEDING_RUN_SCHEMA_VERSION,
    SeedingRunResult,
    SimpleMaterializationContext,
    run_system_seeded_propositions,
)
from app.storage.evidence_repositories import FindingRepository, PropositionRepository
from app.storage.sqlite_metadata import SQLiteMetadataStore

# ---------------------------------------------------------------------------
# Store / DB helpers
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    tmp = tempfile.mkdtemp()
    store = SQLiteMetadataStore(Path(tmp) / "meta.sqlite")
    store.initialize()
    return store


def _insert_session(store: SQLiteMetadataStore, session_id: str = "sess_4e3") -> None:
    store.execute(
        "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [session_id, "test", "{}", "{}", "{}", "open"],
    )


def _insert_artifact(
    store: SQLiteMetadataStore,
    artifact_id: str,
    session_id: str = "sess_4e3",
    step_id: str = "step_001",
    artifact_type: str = "compare_artifact",
    content: dict[str, Any] | None = None,
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            artifact_id,
            session_id,
            step_id,
            artifact_type,
            artifact_id,
            json.dumps(content or {}),
        ],
    )


def _insert_step(
    store: SQLiteMetadataStore,
    *,
    step_id: str,
    session_id: str = "sess_4e3",
    step_type: str,
) -> None:
    store.execute(
        "INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json, provenance_json) "
        "VALUES (?, ?, ?, 'succeeded', ?, ?, ?)",
        [step_id, session_id, step_type, step_type, "{}", "{}"],
    )


def _insert_step_metadata(
    store: SQLiteMetadataStore,
    *,
    step_id: str,
    metric_ref: str,
) -> None:
    store.execute(
        "INSERT INTO step_metadata (step_id, metadata_kind, semantic_snapshot_json) VALUES (?, ?, ?)",
        [
            step_id,
            "typed_semantic_snapshot",
            json.dumps(
                {
                    "schema_version": "step_semantic_metadata.v1",
                    "metadata_kind": "typed_semantic_snapshot",
                    "typed_inputs": {
                        "metric_ref": metric_ref,
                        "process_ref": None,
                        "dimension_refs": [],
                        "filter_time_ref": None,
                        "request_classes": ["root_metric_process"],
                    },
                    "binding_refs": [],
                    "compile_context": {"ir_plan_ids": [], "compiler_summaries": []},
                }
            ),
        ],
    )


def _empty_quality() -> dict[str, Any]:
    return {
        "data_complete": None,
        "sample_size": None,
        "row_count": None,
        "null_rate": None,
        "quality_status": None,
        "quality_warnings": [],
    }


def _insert_finding(
    store: SQLiteMetadataStore,
    finding_id: str,
    session_id: str = "sess_4e3",
    artifact_id: str = "art_001",
    finding_type: str = "delta",
    canonical_item_key: str = "result",
    subject: dict[str, Any] | None = None,
    observed_window: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    step_type: str = "compare",
) -> None:
    default_subject = {
        "metric": "dau",
        "entity": None,
        "slice": {},
        "grain": "day",
        "analysis_axis": "scalar",
    }
    step_ref = {
        "session_id": session_id,
        "step_id": "step_001",
        "step_type": step_type,
    }
    FindingRepository(store).create(
        {
            "finding_id": finding_id,
            "session_id": session_id,
            "artifact_id": artifact_id,
            "step_ref_json": json.dumps(step_ref),
            "finding_type": finding_type,
            "canonical_item_key": canonical_item_key,
            "subject_json": json.dumps(subject or default_subject),
            "observed_window_json": json.dumps(observed_window) if observed_window else None,
            "quality_json": json.dumps(_empty_quality()),
            "provenance_json": json.dumps(
                {
                    "source_step_type": step_type,
                    "extractor_name": "test_extractor",
                    "extractor_version": "v1",
                    "artifact_schema_version": "v1",
                    "canonical_item_key": canonical_item_key,
                    "artifact_item_ref": {"collection": "result", "index": None, "key": None},
                    "projection_ref": None,
                }
            ),
            "payload_json": json.dumps(payload or {}),
            "schema_version": "v1",
        }
    )


# ---------------------------------------------------------------------------
# Standard fixtures
# ---------------------------------------------------------------------------

_SESSION = "sess_4e3"

_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}

_COMPARE_ARTIFACT_CONTENT = {
    "comparison_type": "scalar_delta",
    "metric": "dau",
    "direction": "decrease",
    "resolved_input_summary": {
        "left_scope": {},
        "left_time_scope": _LEFT_WIN,
        "right_time_scope": _RIGHT_WIN,
    },
}

_DELTA_PAYLOAD = {
    "delta_kind": "scalar_delta",
    "left_ref": {
        "artifact_id": "",
        "item_ref": {"collection": "value", "index": None, "key": None},
    },
    "right_ref": {
        "artifact_id": "",
        "item_ref": {"collection": "value", "index": None, "key": None},
    },
    "left_value": 1000.0,
    "right_value": 900.0,
    "absolute_delta": -100.0,
    "relative_delta": -0.1,
    "direction": "decrease",
    "presence": "both",
    "unit": "users",
}

_FORECAST_WIN = {"kind": "range", "start": "2024-02-01", "end": "2024-02-02"}

_FORECAST_PAYLOAD = {
    "bucket_start": "2024-02-01",
    "bucket_end": "2024-02-02",
    "predicted_value": 950.0,
    "prediction_interval": None,
    "horizon_index": 3,
}

_ANOMALY_OBS_WIN = {"kind": "range", "start": "2024-01-05", "end": "2024-01-06"}

_ANOMALY_PAYLOAD = {
    "candidate_ref": {
        "artifact_id": "art_anomaly_001",
        "item_ref": {"collection": "candidates", "index": 0, "key": None},
    },
    "score": 0.95,
    "flag_level": "high",
    "actual_value": 800.0,
    "expected_value": 1000.0,
    "deviation_absolute": -200.0,
    "deviation_relative": -0.2,
}


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class _Base(unittest.TestCase):
    """Set up a fresh in-memory SQLite store with standard fixtures."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        # Compare artifact for delta findings (T1)
        _insert_artifact(
            self.store,
            "art_compare_001",
            content=_COMPARE_ARTIFACT_CONTENT,
        )
        # Forecast artifact (T6 — no artifact dereference needed, but exists for FK)
        _insert_artifact(
            self.store,
            "art_forecast_001",
            artifact_type="forecast_series",
            content={"metric": "dau"},
        )
        # Anomaly artifact (T3)
        _insert_artifact(
            self.store,
            "art_anomaly_001",
            artifact_type="anomaly_candidates",
            content={"metric": "dau"},
        )

        self.finding_repo = FindingRepository(self.store)
        self.prop_repo = PropositionRepository(self.store)
        self.ctx = SimpleMaterializationContext(self.finding_repo, self.store)

    def _run(
        self,
        trigger_finding_ids: list[str],
    ) -> SeedingRunResult:
        return run_system_seeded_propositions(
            session_id=_SESSION,
            trigger_finding_ids=trigger_finding_ids,
            proposition_repo=self.prop_repo,
            finding_repo=self.finding_repo,
            ctx=self.ctx,
        )

    def _insert_delta(
        self,
        finding_id: str = "fnd_delta_001",
        direction: str = "decrease",
        presence: str = "both",
    ) -> None:
        payload = {**_DELTA_PAYLOAD, "direction": direction, "presence": presence}
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_compare_001",
            finding_type="delta",
            canonical_item_key="result",
            payload=payload,
        )

    def _insert_forecast(
        self,
        finding_id: str = "fnd_forecast_001",
        horizon_index: int | None = 3,
        prediction_interval: dict | None = None,
    ) -> None:
        payload = {
            **_FORECAST_PAYLOAD,
            "horizon_index": horizon_index,
            "prediction_interval": prediction_interval,
        }
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_forecast_001",
            finding_type="forecast_point",
            canonical_item_key="points:2024-02-01/2024-02-02",
            subject={
                "metric": "dau",
                "entity": None,
                "slice": {},
                "grain": "day",
                "analysis_axis": "forecast",
            },
            payload=payload,
            step_type="forecast",
        )

    def _insert_anomaly(
        self,
        finding_id: str = "fnd_anomaly_001",
        with_observed_window: bool = True,
    ) -> None:
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_anomaly_001",
            finding_type="anomaly_candidate",
            canonical_item_key="candidates:0",
            subject={
                "metric": "dau",
                "entity": None,
                "slice": {},
                "grain": "day",
                "analysis_axis": "anomaly",
            },
            observed_window=_ANOMALY_OBS_WIN if with_observed_window else None,
            payload=_ANOMALY_PAYLOAD,
            step_type="detect",
        )


# ---------------------------------------------------------------------------
# TestSeedingRunResultContract — TypedDict shape and schema_version
# ---------------------------------------------------------------------------


class TestSeedingRunResultContract(_Base):
    def test_empty_trigger_list(self) -> None:
        result = self._run([])
        self.assertEqual(result["created_proposition_ids"], [])
        self.assertEqual(result["existing_proposition_ids"], [])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_schema_version(self) -> None:
        result = self._run([])
        self.assertEqual(result["schema_version"], SEEDING_RUN_SCHEMA_VERSION)
        self.assertEqual(result["schema_version"], "finding_proposition_seeding_run.v1")

    def test_affected_is_sorted_union(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_delta_001", "fnd_forecast_001"])
        expected = sorted(
            set(result["created_proposition_ids"]) | set(result["existing_proposition_ids"])
        )
        self.assertEqual(result["affected_proposition_ids"], expected)

    def test_missing_finding_id_skipped(self) -> None:
        """A non-existent finding_id is silently skipped."""
        result = self._run(["nonexistent_fnd"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_observation_finding_no_template(self) -> None:
        """observation findings have no registered template → empty result."""
        _insert_artifact(self.store, "art_obs_001", artifact_type="observation")
        _insert_finding(
            self.store,
            finding_id="fnd_obs_001",
            artifact_id="art_obs_001",
            finding_type="observation",
            canonical_item_key="value",
        )
        result = self._run(["fnd_obs_001"])
        self.assertEqual(result["affected_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestSeedingRunStability — replay, ordering invariants
# ---------------------------------------------------------------------------


class TestSeedingRunStability(_Base):
    def test_same_inputs_same_affected_ids(self) -> None:
        self._insert_delta("fnd_delta_001")
        r1 = self._run(["fnd_delta_001"])
        r2 = self._run(["fnd_delta_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])

    def test_second_run_all_hits(self) -> None:
        self._insert_delta("fnd_delta_001")
        r1 = self._run(["fnd_delta_001"])
        self.assertEqual(len(r1["created_proposition_ids"]), 1)

        r2 = self._run(["fnd_delta_001"])
        self.assertEqual(r2["created_proposition_ids"], [])
        self.assertEqual(len(r2["existing_proposition_ids"]), 1)

    def test_input_order_does_not_affect_affected_ids(self) -> None:
        """Trigger finding ID order does not affect affected_proposition_ids."""
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")

        r_ab = self._run(["fnd_delta_001", "fnd_forecast_001"])
        # Register the hit by running first run.
        # Re-run with reversed order — should produce same affected_ids.
        r_ba = self._run(["fnd_forecast_001", "fnd_delta_001"])
        self.assertEqual(r_ab["affected_proposition_ids"], r_ba["affected_proposition_ids"])

    def test_affected_ids_are_sorted(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_delta_001", "fnd_forecast_001"])
        self.assertEqual(
            result["affected_proposition_ids"],
            sorted(result["affected_proposition_ids"]),
        )


# ---------------------------------------------------------------------------
# TestAffectedPropositionIds — create/hit both go into affected
# ---------------------------------------------------------------------------


class TestAffectedPropositionIds(_Base):
    def test_created_in_affected(self) -> None:
        self._insert_delta("fnd_delta_001")
        result = self._run(["fnd_delta_001"])
        self.assertTrue(len(result["created_proposition_ids"]) > 0)
        for pid in result["created_proposition_ids"]:
            self.assertIn(pid, result["affected_proposition_ids"])

    def test_existing_in_affected(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._run(["fnd_delta_001"])  # prime with first run
        result2 = self._run(["fnd_delta_001"])
        self.assertTrue(len(result2["existing_proposition_ids"]) > 0)
        for pid in result2["existing_proposition_ids"]:
            self.assertIn(pid, result2["affected_proposition_ids"])

    def test_affected_same_across_runs(self) -> None:
        self._insert_delta("fnd_delta_001")
        r1 = self._run(["fnd_delta_001"])
        r2 = self._run(["fnd_delta_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])

    def test_affected_deduped(self) -> None:
        """Passing the same finding_id twice does not duplicate propositions."""
        self._insert_delta("fnd_delta_001")
        result = self._run(["fnd_delta_001", "fnd_delta_001"])
        affected = result["affected_proposition_ids"]
        self.assertEqual(len(affected), len(set(affected)))


# ---------------------------------------------------------------------------
# TestCreationCondition — creation condition failures
# ---------------------------------------------------------------------------


class TestCreationConditionT1(_Base):
    def test_flat_direction_no_proposition(self) -> None:
        self._insert_delta("fnd_flat", direction="flat")
        result = self._run(["fnd_flat"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_undefined_direction_both_presence_no_proposition(self) -> None:
        self._insert_delta("fnd_undef_both", direction="undefined", presence="both")
        result = self._run(["fnd_undef_both"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_undefined_direction_left_only_creates_proposition(self) -> None:
        self._insert_delta("fnd_undef_left", direction="undefined", presence="left_only")
        result = self._run(["fnd_undef_left"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_undefined_direction_right_only_creates_proposition(self) -> None:
        self._insert_delta("fnd_undef_right", direction="undefined", presence="right_only")
        result = self._run(["fnd_undef_right"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_no_artifact_payload_no_proposition(self) -> None:
        """delta finding pointing to artifact with no resolved_input_summary."""
        _insert_artifact(
            self.store,
            "art_no_summary_001",
            artifact_type="compare_artifact",
            content={
                "comparison_type": "scalar_delta",
                "metric": "dau",
            },  # no resolved_input_summary
        )
        _insert_finding(
            self.store,
            finding_id="fnd_no_summary",
            artifact_id="art_no_summary_001",
            finding_type="delta",
            canonical_item_key="result",
            payload=_DELTA_PAYLOAD,
        )
        result = self._run(["fnd_no_summary"])
        self.assertEqual(result["affected_proposition_ids"], [])


class TestCreationConditionT3(_Base):
    def test_no_observed_window_no_proposition(self) -> None:
        self._insert_anomaly("fnd_anomaly_no_win", with_observed_window=False)
        result = self._run(["fnd_anomaly_no_win"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_no_candidate_ref_no_proposition(self) -> None:
        _insert_finding(
            self.store,
            finding_id="fnd_anomaly_no_ref",
            artifact_id="art_anomaly_001",
            finding_type="anomaly_candidate",
            canonical_item_key="candidates:0",
            observed_window=_ANOMALY_OBS_WIN,
            payload={"score": 0.9},  # missing candidate_ref
            step_type="detect",
        )
        result = self._run(["fnd_anomaly_no_ref"])
        self.assertEqual(result["affected_proposition_ids"], [])


class TestCreationConditionT6(_Base):
    def test_none_horizon_index_no_proposition(self) -> None:
        self._insert_forecast("fnd_forecast_no_hi", horizon_index=None)
        result = self._run(["fnd_forecast_no_hi"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_negative_horizon_index_no_proposition(self) -> None:
        self._insert_forecast("fnd_forecast_neg_hi", horizon_index=-1)
        result = self._run(["fnd_forecast_neg_hi"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_empty_bucket_start_no_proposition(self) -> None:
        payload = {**_FORECAST_PAYLOAD, "bucket_start": "", "bucket_end": "2024-02-02"}
        _insert_finding(
            self.store,
            finding_id="fnd_forecast_no_bucket",
            artifact_id="art_forecast_001",
            finding_type="forecast_point",
            canonical_item_key="points:x",
            payload=payload,
            step_type="forecast",
        )
        result = self._run(["fnd_forecast_no_bucket"])
        self.assertEqual(result["affected_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestSeedingRunT1Change — T1 specific
# ---------------------------------------------------------------------------


class TestSeedingRunT1Change(_Base):
    def test_scalar_delta_creates_change_proposition(self) -> None:
        self._insert_delta("fnd_delta_001", direction="decrease")
        result = self._run(["fnd_delta_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_increase_direction_creates_proposition(self) -> None:
        payload = {**_DELTA_PAYLOAD, "direction": "increase"}
        _insert_finding(
            self.store,
            finding_id="fnd_delta_inc",
            artifact_id="art_compare_001",
            finding_type="delta",
            canonical_item_key="result",
            payload=payload,
        )
        result = self._run(["fnd_delta_inc"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_change_proposition_payload_direction(self) -> None:
        self._insert_delta("fnd_delta_001", direction="decrease")
        result = self._run(["fnd_delta_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        self.assertIsNotNone(row)
        payload = row["payload_json"]
        self.assertEqual(payload["direction_of_interest"], "decrease")
        self.assertEqual(payload["change_kind"], "scalar_change")

    def test_change_proposition_comparison_window(self) -> None:
        self._insert_delta("fnd_delta_001", direction="decrease")
        result = self._run(["fnd_delta_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        payload = row["payload_json"]
        self.assertEqual(payload["comparison_window"]["left"], _LEFT_WIN)
        self.assertEqual(payload["comparison_window"]["right"], _RIGHT_WIN)

    def test_change_proposition_subject_analysis_axis(self) -> None:
        self._insert_delta("fnd_delta_001", direction="decrease")
        result = self._run(["fnd_delta_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        self.assertEqual(row["subject_json"]["analysis_axis"], "change")

    def test_any_non_flat_direction_for_undefined_left_only(self) -> None:
        self._insert_delta("fnd_undef_left", direction="undefined", presence="left_only")
        result = self._run(["fnd_undef_left"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        self.assertEqual(row["payload_json"]["direction_of_interest"], "any_non_flat")

    def test_change_proposition_seed_refs(self) -> None:
        self._insert_delta("fnd_delta_001")
        result = self._run(["fnd_delta_001"])
        pid = result["created_proposition_ids"][0]
        refs = self.prop_repo.get_seed_finding_refs(pid)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["finding_id"], "fnd_delta_001")
        self.assertEqual(refs[0]["role"], "primary")


# ---------------------------------------------------------------------------
# TestSeedingRunT6Forecast — T6 specific
# ---------------------------------------------------------------------------


class TestSeedingRunT6Forecast(_Base):
    def test_forecast_creates_proposition(self) -> None:
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_forecast_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_forecast_proposition_type(self) -> None:
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_forecast_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        self.assertEqual(row["proposition_type"], "forecast")

    def test_point_forecast_kind(self) -> None:
        self._insert_forecast("fnd_forecast_001", prediction_interval=None)
        result = self._run(["fnd_forecast_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["forecast_kind"], "point_forecast")

    def test_interval_forecast_kind(self) -> None:
        self._insert_forecast(
            "fnd_forecast_iv",
            prediction_interval={"lower": 900.0, "upper": 1000.0, "level": 0.9},
        )
        result = self._run(["fnd_forecast_iv"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["forecast_kind"], "interval_forecast")

    def test_forecast_window_from_payload(self) -> None:
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_forecast_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["forecast_window"]["start"], "2024-02-01")
        self.assertEqual(payload["forecast_window"]["end"], "2024-02-02")
        self.assertEqual(payload["horizon_index"], 3)

    def test_forecast_expectation_direction_open(self) -> None:
        self._insert_forecast("fnd_forecast_001")
        result = self._run(["fnd_forecast_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["expectation_direction"], "open")

    def test_forecast_horizon_zero_allowed(self) -> None:
        self._insert_forecast("fnd_forecast_h0", horizon_index=0)
        result = self._run(["fnd_forecast_h0"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)


# ---------------------------------------------------------------------------
# TestSeedingRunT3Anomaly — T3 specific
# ---------------------------------------------------------------------------


class TestSeedingRunT3Anomaly(_Base):
    def test_anomaly_creates_proposition(self) -> None:
        self._insert_anomaly("fnd_anomaly_001")
        result = self._run(["fnd_anomaly_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_anomaly_proposition_payload(self) -> None:
        self._insert_anomaly("fnd_anomaly_001")
        result = self._run(["fnd_anomaly_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        payload = row["payload_json"]
        self.assertEqual(payload["anomaly_kind"], "candidate")
        self.assertEqual(payload["validation_goal"], "validate_candidate")
        self.assertIsNone(payload["expected_behavior_ref"])


# ---------------------------------------------------------------------------
# TestMultiFindingFanout — multiple findings → stable fan-out
# ---------------------------------------------------------------------------


class TestMultiFindingFanout(_Base):
    def test_multiple_findings_multiple_propositions(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")
        self._insert_anomaly("fnd_anomaly_001")
        result = self._run(["fnd_delta_001", "fnd_forecast_001", "fnd_anomaly_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 3)

    def test_multiple_findings_affected_ids_stable_sorted(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")
        r1 = self._run(["fnd_delta_001", "fnd_forecast_001"])
        r2 = self._run(["fnd_forecast_001", "fnd_delta_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])

    def test_two_delta_findings_produce_two_propositions(self) -> None:
        """Two distinct delta findings → two distinct propositions."""
        _insert_artifact(
            self.store,
            "art_compare_002",
            content={
                **_COMPARE_ARTIFACT_CONTENT,
                "metric": "revenue",
            },
        )
        payload2 = {**_DELTA_PAYLOAD, "unit": "usd"}
        subject2 = {
            "metric": "revenue",
            "entity": None,
            "slice": {},
            "grain": "day",
            "analysis_axis": "scalar",
        }
        _insert_finding(
            self.store,
            finding_id="fnd_delta_rev",
            artifact_id="art_compare_002",
            finding_type="delta",
            canonical_item_key="result",
            subject=subject2,
            payload=payload2,
        )
        self._insert_delta("fnd_delta_001")
        result = self._run(["fnd_delta_001", "fnd_delta_rev"])
        self.assertEqual(len(result["created_proposition_ids"]), 2)
        # All affected
        self.assertEqual(len(result["affected_proposition_ids"]), 2)


# ---------------------------------------------------------------------------
# TestIdempotency — crash recovery via idempotent re-run
# ---------------------------------------------------------------------------


class TestIdempotency(_Base):
    def test_double_run_no_duplicate_propositions(self) -> None:
        self._insert_delta("fnd_delta_001")
        self._run(["fnd_delta_001"])
        count_after_first = len(self.prop_repo.list_by_session(_SESSION))

        self._run(["fnd_delta_001"])
        count_after_second = len(self.prop_repo.list_by_session(_SESSION))
        self.assertEqual(count_after_first, count_after_second)

    def test_partial_run_then_complete(self) -> None:
        """Pre-register one proposition, run seeding → one create, one hit."""
        self._insert_delta("fnd_delta_001")
        self._insert_forecast("fnd_forecast_001")

        # Pre-register the delta proposition via first run
        r1 = self._run(["fnd_delta_001"])
        self.assertEqual(len(r1["created_proposition_ids"]), 1)

        # Full run: delta should be a hit, forecast should be new
        r2 = self._run(["fnd_delta_001", "fnd_forecast_001"])
        self.assertEqual(len(r2["existing_proposition_ids"]), 1)
        self.assertEqual(len(r2["created_proposition_ids"]), 1)
        # Both go into affected
        self.assertEqual(len(r2["affected_proposition_ids"]), 2)

    def test_three_runs_same_affected_ids(self) -> None:
        self._insert_delta("fnd_delta_001")
        r1 = self._run(["fnd_delta_001"])
        r2 = self._run(["fnd_delta_001"])
        r3 = self._run(["fnd_delta_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])
        self.assertEqual(r2["affected_proposition_ids"], r3["affected_proposition_ids"])


# ---------------------------------------------------------------------------
# Shared fixtures for T2/T4/T5
# ---------------------------------------------------------------------------

_CORR_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-14"}

_CORRELATE_ARTIFACT_CONTENT = {
    "left_metric": "dau",
    "right_metric": "revenue",
    "statistic": {"method": "pearson", "coefficient": 0.8, "p_value": 0.02, "n_pairs": 14},
    "analytical_metadata": {
        "pairing_rule": {"kind": "time_aligned", "grain": "day", "key_fields": []},
        "matched_time_scope": {"start": "2024-01-01", "end": "2024-01-14"},
    },
    "source_lineage": {
        "left_artifact": {"artifact_id": "art_obs_left_001"},
        "right_artifact": {"artifact_id": "art_obs_right_001"},
    },
}

_CORRELATION_PAYLOAD = {
    "method": "pearson",
    "coefficient": 0.8,
    "p_value": 0.02,
    "n": 14,
    "join_basis": {"kind": "time_aligned", "grain": "day", "key_fields": []},
    "left_ref": {
        "artifact_id": "art_obs_left_001",
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "right_ref": {
        "artifact_id": "art_obs_right_001",
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
}

_TEST_PAYLOAD = {
    "method": "welch_t",
    "alpha": 0.05,
    "estimate_value": 100.0,
    "statistic_name": "t",
    "statistic_value": 2.5,
    "p_value": 0.02,
    "reject_null": True,
    "left_ref": {
        "artifact_id": "art_obs_left_001",
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "right_ref": {
        "artifact_id": "art_obs_right_001",
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
}

_DECOMP_SUBJECT = {
    "metric": "dau",
    "entity": None,
    "slice": {"country": "US"},
    "grain": None,
    "analysis_axis": "decomposition",
}

_DECOMP_PAYLOAD = {
    "dimension": "country",
    "keys": {"country": "US"},
    "contribution_value": -50.0,
    "contribution_share": 0.5,
    "rank": 1,
    "direction": "decrease",
    "scope_delta_ref": {"session_id": _SESSION, "finding_id": "fnd_delta_001"},
}


class _T245Base(_Base):
    """Extends _Base with artifacts needed for T2, T4, and T5 tests."""

    def setUp(self) -> None:
        super().setUp()
        # T2: decomposition artifact
        _insert_artifact(
            self.store,
            "art_decompose_001",
            artifact_type="delta_decomposition",
            content={
                "dimension": "country",
                "metric": "dau",
                "compare_ref": {"artifact_id": "art_compare_001"},
            },
        )
        # T4/T5: observation artifacts (left=dau, right=revenue)
        _insert_step(self.store, step_id="step_obs_left", step_type="observe")
        _insert_step(self.store, step_id="step_obs_right", step_type="observe")
        _insert_step_metadata(self.store, step_id="step_obs_left", metric_ref="metric.dau")
        _insert_step_metadata(self.store, step_id="step_obs_right", metric_ref="metric.revenue")
        _insert_artifact(
            self.store,
            "art_obs_left_001",
            step_id="step_obs_left",
            artifact_type="observation",
            content={"metric": "dau"},
        )
        _insert_artifact(
            self.store,
            "art_obs_right_001",
            step_id="step_obs_right",
            artifact_type="observation",
            content={"metric": "revenue"},
        )
        # T4: correlate artifact
        _insert_artifact(
            self.store,
            "art_correlate_001",
            artifact_type="pairwise_time_series_association",
            content=_CORRELATE_ARTIFACT_CONTENT,
        )
        # T5: hypothesis_test artifact
        _insert_artifact(
            self.store,
            "art_hypo_001",
            artifact_type="hypothesis_test",
            content={"method": "welch_t"},
        )

    def _insert_decomp_item(
        self,
        finding_id: str = "fnd_decomp_001",
        contribution_value: float | None = -50.0,
        contribution_share: float | None = 0.5,
        scope_delta_finding_id: str = "fnd_delta_001",
        include_scope_delta_ref: bool = True,
    ) -> None:
        payload = {
            **_DECOMP_PAYLOAD,
            "contribution_value": contribution_value,
            "contribution_share": contribution_share,
        }
        if not include_scope_delta_ref:
            payload = {k: v for k, v in payload.items() if k != "scope_delta_ref"}
        else:
            payload["scope_delta_ref"] = {
                "session_id": _SESSION,
                "finding_id": scope_delta_finding_id,
            }
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_decompose_001",
            finding_type="decomposition_item",
            canonical_item_key="country:US",
            subject=_DECOMP_SUBJECT,
            payload=payload,
            step_type="decompose",
        )

    def _insert_correlation(
        self,
        finding_id: str = "fnd_correlate_001",
        coefficient: float | None = 0.8,
        join_basis: Any = None,
        with_observed_window: bool = True,
    ) -> None:
        payload = {
            **_CORRELATION_PAYLOAD,
            "coefficient": coefficient,
            "join_basis": join_basis
            if join_basis is not None
            else _CORRELATION_PAYLOAD["join_basis"],
        }
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_correlate_001",
            finding_type="correlation_result",
            canonical_item_key="result",
            observed_window=_CORR_WIN if with_observed_window else None,
            payload=payload,
            step_type="correlate",
        )

    def _insert_test_result(
        self,
        finding_id: str = "fnd_test_001",
        alpha: float | None = 0.05,
        left_artifact_id: str = "art_obs_left_001",
        right_artifact_id: str = "art_obs_right_001",
    ) -> None:
        payload = {
            **_TEST_PAYLOAD,
            "alpha": alpha,
            "left_ref": {
                "artifact_id": left_artifact_id,
                "item_ref": {"collection": "result", "index": None, "key": None},
            },
            "right_ref": {
                "artifact_id": right_artifact_id,
                "item_ref": {"collection": "result", "index": None, "key": None},
            },
        }
        _insert_finding(
            self.store,
            finding_id=finding_id,
            artifact_id="art_hypo_001",
            finding_type="test_result",
            canonical_item_key="result",
            payload=payload,
            step_type="test",
        )


# ---------------------------------------------------------------------------
# TestCreationConditionT2 — decomposition_item creation conditions
# ---------------------------------------------------------------------------


class TestCreationConditionT2(_T245Base):
    def test_zero_contribution_no_proposition(self) -> None:
        """contribution_value=0 and contribution_share=0 → creation condition false."""
        self._insert_delta("fnd_delta_001")
        self._insert_decomp_item("fnd_decomp_zero", contribution_value=0.0, contribution_share=0.0)
        result = self._run(["fnd_decomp_zero"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_null_contribution_no_proposition(self) -> None:
        """Both contribution_value=None and contribution_share=None → creation condition false."""
        self._insert_delta("fnd_delta_001")
        self._insert_decomp_item(
            "fnd_decomp_null", contribution_value=None, contribution_share=None
        )
        result = self._run(["fnd_decomp_null"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_missing_scope_delta_ref_no_proposition(self) -> None:
        """scope_delta_ref absent → creation condition false."""
        self._insert_delta("fnd_delta_001")
        self._insert_decomp_item("fnd_decomp_noref", include_scope_delta_ref=False)
        result = self._run(["fnd_decomp_noref"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_unresolvable_scope_delta_ref_no_proposition(self) -> None:
        """scope_delta_ref.finding_id points to non-existent finding → no proposition."""
        self._insert_delta("fnd_delta_001")
        # Override scope_delta_ref to a non-existent finding_id
        payload = {
            **_DECOMP_PAYLOAD,
            "scope_delta_ref": {"session_id": _SESSION, "finding_id": "fnd_nonexistent"},
        }
        _insert_finding(
            self.store,
            finding_id="fnd_decomp_badref",
            artifact_id="art_decompose_001",
            finding_type="decomposition_item",
            canonical_item_key="country:US",
            subject=_DECOMP_SUBJECT,
            payload=payload,
            step_type="decompose",
        )
        result = self._run(["fnd_decomp_badref"])
        self.assertEqual(result["affected_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestSeedingRunT2Decomposition — T2 end-to-end
# ---------------------------------------------------------------------------


class TestSeedingRunT2Decomposition(_T245Base):
    def setUp(self) -> None:
        super().setUp()
        self._insert_delta("fnd_delta_001")
        self._insert_decomp_item("fnd_decomp_001")

    def test_decomposition_creates_proposition(self) -> None:
        result = self._run(["fnd_decomp_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_decomposition_proposition_type(self) -> None:
        result = self._run(["fnd_decomp_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        self.assertEqual(row["proposition_type"], "decomposition")

    def test_decomposition_payload_fields(self) -> None:
        result = self._run(["fnd_decomp_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["dimension"], "country")
        self.assertEqual(payload["contribution_role"], "primary_driver")
        self.assertEqual(payload["scope_delta_ref"]["finding_id"], "fnd_delta_001")
        self.assertEqual(payload["comparison_window"]["left"], _LEFT_WIN)
        self.assertEqual(payload["comparison_window"]["right"], _RIGHT_WIN)

    def test_decomposition_subject_from_trigger_finding(self) -> None:
        """Subject must come from the trigger (decomposition_item) finding, not delta finding."""
        result = self._run(["fnd_decomp_001"])
        pid = result["created_proposition_ids"][0]
        row = self.prop_repo.get(pid)
        subject = row["subject_json"]
        self.assertEqual(subject["analysis_axis"], "decomposition")
        # Trigger finding subject has slice={"country": "US"} (not the delta's {})
        self.assertEqual(subject["slice"], {"country": "US"})
        self.assertEqual(subject["metric"], "dau")

    def test_decomposition_lineage_two_artifacts_sorted(self) -> None:
        """Lineage must include both artifacts (decompose + compare), sorted lexically."""
        result = self._run(["fnd_decomp_001"])
        pid = result["created_proposition_ids"][0]
        lineage = self.prop_repo.get(pid)["lineage_json"]
        artifact_ids = [e["artifact_id"] for e in lineage["source_artifact_lineages"]]
        # art_compare_001 < art_decompose_001 lexically
        self.assertEqual(artifact_ids, sorted(artifact_ids))
        self.assertIn("art_compare_001", artifact_ids)
        self.assertIn("art_decompose_001", artifact_ids)
        self.assertEqual(len(artifact_ids), 2)

    def test_decomposition_seed_refs(self) -> None:
        """Seed refs: primary = decomposition_item, context = delta."""
        result = self._run(["fnd_decomp_001"])
        pid = result["created_proposition_ids"][0]
        refs = self.prop_repo.get_seed_finding_refs(pid)
        roles = {r["finding_id"]: r["role"] for r in refs}
        self.assertEqual(roles.get("fnd_decomp_001"), "primary")
        self.assertEqual(roles.get("fnd_delta_001"), "context")

    def test_decomposition_replay_stable(self) -> None:
        r1 = self._run(["fnd_decomp_001"])
        r2 = self._run(["fnd_decomp_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])
        self.assertEqual(r2["created_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestCreationConditionT4 — correlation_result creation conditions
# ---------------------------------------------------------------------------


class TestCreationConditionT4(_T245Base):
    def test_string_join_basis_no_proposition(self) -> None:
        """Plain string join_basis (unstructured) → creation condition false."""
        self._insert_correlation("fnd_corr_str_jb", join_basis="time_aligned")
        result = self._run(["fnd_corr_str_jb"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_missing_observed_window_no_proposition(self) -> None:
        """No observed_window (aligned_window) → creation condition false."""
        self._insert_correlation("fnd_corr_no_win", with_observed_window=False)
        result = self._run(["fnd_corr_no_win"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_missing_left_metric_in_artifact_no_proposition(self) -> None:
        """Artifact missing left_metric → creation condition false."""
        _insert_artifact(
            self.store,
            "art_corr_no_metric",
            artifact_type="pairwise_time_series_association",
            content={
                "right_metric": "revenue",  # no left_metric
                "analytical_metadata": {
                    "pairing_rule": {"kind": "time_aligned", "grain": "day", "key_fields": []},
                    "matched_time_scope": {"start": "2024-01-01", "end": "2024-01-14"},
                },
            },
        )
        _insert_finding(
            self.store,
            finding_id="fnd_corr_no_lm",
            artifact_id="art_corr_no_metric",
            finding_type="correlation_result",
            canonical_item_key="result",
            observed_window=_CORR_WIN,
            payload=_CORRELATION_PAYLOAD,
            step_type="correlate",
        )
        result = self._run(["fnd_corr_no_lm"])
        self.assertEqual(result["affected_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestSeedingRunT4Correlation — T4 end-to-end
# ---------------------------------------------------------------------------


class TestSeedingRunT4Correlation(_T245Base):
    def setUp(self) -> None:
        super().setUp()
        self._insert_correlation("fnd_correlate_001")

    def test_correlation_creates_proposition(self) -> None:
        result = self._run(["fnd_correlate_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_correlation_proposition_type(self) -> None:
        result = self._run(["fnd_correlate_001"])
        pid = result["created_proposition_ids"][0]
        self.assertEqual(self.prop_repo.get(pid)["proposition_type"], "correlation")

    def test_correlation_payload_join_basis(self) -> None:
        result = self._run(["fnd_correlate_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["join_basis"]["kind"], "time_aligned")
        self.assertEqual(payload["join_basis"]["grain"], "day")

    def test_positive_relationship_of_interest(self) -> None:
        """coefficient > 0 → relationship_of_interest = 'positive_association'."""
        result = self._run(["fnd_correlate_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["relationship_of_interest"], "positive_association")

    def test_negative_relationship_of_interest(self) -> None:
        """coefficient < 0 → relationship_of_interest = 'negative_association'."""
        # Use a separate artifact so the UNIQUE(artifact_id, finding_type, canonical_item_key)
        # constraint doesn't conflict with fnd_correlate_001.
        _insert_artifact(
            self.store,
            "art_corr_neg_001",
            artifact_type="pairwise_time_series_association",
            content={
                **_CORRELATE_ARTIFACT_CONTENT,
                "left_metric": "revenue",
                "right_metric": "new_users",
            },
        )
        neg_payload = {**_CORRELATION_PAYLOAD, "coefficient": -0.6}
        _insert_finding(
            self.store,
            finding_id="fnd_corr_neg",
            artifact_id="art_corr_neg_001",
            finding_type="correlation_result",
            canonical_item_key="result",
            observed_window=_CORR_WIN,
            payload=neg_payload,
            step_type="correlate",
        )
        result = self._run(["fnd_corr_neg"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["relationship_of_interest"], "negative_association")

    def test_bilateral_subjects_from_artifact(self) -> None:
        """left_subject / right_subject come from the correlate artifact metrics."""
        result = self._run(["fnd_correlate_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["left_subject"]["metric"], "dau")
        self.assertEqual(payload["right_subject"]["metric"], "revenue")

    def test_bilateral_subjects_prefer_step_metadata_over_artifact_summary(self) -> None:
        self.store.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [
                json.dumps(
                    {
                        **_CORRELATE_ARTIFACT_CONTENT,
                        "left_metric": "wrong_left",
                        "right_metric": "wrong_right",
                    }
                ),
                "art_correlate_001",
            ],
        )
        result = self._run(["fnd_correlate_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["left_subject"]["metric"], "dau")
        self.assertEqual(payload["right_subject"]["metric"], "revenue")

    def test_correlation_replay_stable(self) -> None:
        r1 = self._run(["fnd_correlate_001"])
        r2 = self._run(["fnd_correlate_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])
        self.assertEqual(r2["created_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestCreationConditionT5 — test_result creation conditions
# ---------------------------------------------------------------------------


class TestCreationConditionT5(_T245Base):
    def test_missing_alpha_no_proposition(self) -> None:
        """alpha=None → creation condition false."""
        self._insert_test_result("fnd_test_no_alpha", alpha=None)
        result = self._run(["fnd_test_no_alpha"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_missing_left_artifact_id_no_proposition(self) -> None:
        """left_ref.artifact_id empty → ctx.get_artifact_payload returns None → false."""
        self._insert_test_result("fnd_test_no_lid", left_artifact_id="")
        result = self._run(["fnd_test_no_lid"])
        self.assertEqual(result["affected_proposition_ids"], [])

    def test_unresolvable_right_artifact_no_proposition(self) -> None:
        """right_ref points to non-existent artifact → no metric → false."""
        self._insert_test_result("fnd_test_bad_ra", right_artifact_id="art_nonexistent_999")
        result = self._run(["fnd_test_bad_ra"])
        self.assertEqual(result["affected_proposition_ids"], [])


# ---------------------------------------------------------------------------
# TestSeedingRunT5TestHypothesis — T5 end-to-end
# ---------------------------------------------------------------------------


class TestSeedingRunT5TestHypothesis(_T245Base):
    def setUp(self) -> None:
        super().setUp()
        self._insert_test_result("fnd_test_001")

    def test_test_hypothesis_creates_proposition(self) -> None:
        result = self._run(["fnd_test_001"])
        self.assertEqual(len(result["created_proposition_ids"]), 1)

    def test_test_hypothesis_proposition_type(self) -> None:
        result = self._run(["fnd_test_001"])
        pid = result["created_proposition_ids"][0]
        self.assertEqual(self.prop_repo.get(pid)["proposition_type"], "test_hypothesis")

    def test_test_hypothesis_payload_fields(self) -> None:
        result = self._run(["fnd_test_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["hypothesis_family"], "difference")
        self.assertEqual(payload["alternative"], "two_sided")
        self.assertAlmostEqual(payload["alpha"], 0.05)
        self.assertEqual(payload["method_family"], "welch_t")
        self.assertIsNone(payload["hypothesis_label"])

    def test_test_hypothesis_bilateral_subjects_from_observation_artifacts(self) -> None:
        """left_subject / right_subject metrics come from upstream observation artifacts."""
        result = self._run(["fnd_test_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["left_subject"]["metric"], "dau")
        self.assertEqual(payload["right_subject"]["metric"], "revenue")
        self.assertEqual(payload["left_subject"]["analysis_axis"], "test")
        self.assertEqual(payload["right_subject"]["analysis_axis"], "test")

    def test_test_hypothesis_prefers_step_metadata_over_observation_artifact_metric(self) -> None:
        self.store.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [json.dumps({"metric": "wrong_left"}), "art_obs_left_001"],
        )
        self.store.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [json.dumps({"metric": "wrong_right"}), "art_obs_right_001"],
        )
        result = self._run(["fnd_test_001"])
        pid = result["created_proposition_ids"][0]
        payload = self.prop_repo.get(pid)["payload_json"]
        self.assertEqual(payload["left_subject"]["metric"], "dau")
        self.assertEqual(payload["right_subject"]["metric"], "revenue")

    def test_test_hypothesis_replay_stable(self) -> None:
        r1 = self._run(["fnd_test_001"])
        r2 = self._run(["fnd_test_001"])
        self.assertEqual(r1["affected_proposition_ids"], r2["affected_proposition_ids"])
        self.assertEqual(r2["created_proposition_ids"], [])


if __name__ == "__main__":
    unittest.main()
