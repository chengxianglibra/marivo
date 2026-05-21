"""Evidence pipeline family behavior integration tests (§8.3).

These are integration tests that exercise the *real* registered extractors
through the ``commit_artifact_with_extraction`` commit path with a real
SQLite store.

Unlike ``test_artifact_commit_boundary.py`` (stub extractors, real DB) and
``test_*_extractor.py`` (real extractors, no DB), these tests verify the full
chain:

    real extractor → validate_for_commit → DB writes

Coverage:
 §8.3 family behavior scenarios
   1. ``observe`` empty time_series / segmented → success, 0 findings in DB
   2. ``detect`` zero candidates → success, 0 findings in DB
   3. ``compare`` empty segmented rows → FamilyEmptyError, no artifact in DB
   4. ``decompose`` empty rows → FamilyEmptyError, no artifact in DB
   5. ``correlate`` valid pairs → success, exactly 1 finding in DB
   6. ``forecast`` N buckets → success, N findings in DB

 §8.3 identity and replay stability
   7. success-empty ``observe`` replay (same payload, same artifact_id) →
      still 0 findings; no synthetic finding generated
   8. success-empty ``detect`` replay → still 0 findings
   9. finding_id is stable across re-extraction for the same artifact_id
      and payload (correlate, observe scalar)
"""

from __future__ import annotations

import contextlib
import unittest
from typing import Any

# Registry import first — bootstraps all real extractors.
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.core.evidence.canonical_finding import StepRef
from marivo.core.evidence.family_contract import FamilyEmptyError
from marivo.runtime.evidence.finding_extractor_registry import (
    FindingExtractorRegistry,
    default_finding_registry,
    validate_for_commit,
)
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Store / service factory
# ---------------------------------------------------------------------------

_SESSION = "sess_ep_family_001"


def _make_store() -> SQLiteMetadataStore:
    store = make_temp_metadata_store()
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [_SESSION, "evidence pipeline family test", "{}", "{}", "open"],
    )
    return store


def _commit_artifact_with_extraction(
    store: SQLiteMetadataStore,
    session_id: str,
    step_id: str,
    artifact_type: str,
    name: str,
    content: Any,
    *,
    step_type: str | None = None,
    artifact_schema_version: str | None = None,
    _registry: FindingExtractorRegistry | None = None,
) -> str:
    """Commit boundary for test artifacts, operating directly on the metadata store.

    Replicates the logic from SemanticLayerService._commit_artifact_with_extraction
    so that tests don't depend on the adapter path (which requires app.findings).
    """
    import json as _json
    from uuid import uuid4

    from marivo.core.evidence.canonical_finding import StepRef as _StepRef

    registry = _registry if _registry is not None else default_finding_registry
    extractor = registry.find(artifact_type, artifact_schema_version)

    if extractor is None:
        # Non-mandatory family: insert as committed directly.
        artifact_id = f"art_{uuid4().hex[:12]}"
        store.execute(
            """
            INSERT INTO artifacts
                (artifact_id, session_id, step_id, artifact_type, name,
                 content_json, lifecycle, artifact_schema_version)
            VALUES (?, ?, ?, ?, ?, ?, 'committed', ?)
            """,
            [
                artifact_id,
                session_id,
                step_id,
                artifact_type,
                name,
                _json.dumps(content, default=str, sort_keys=True),
                artifact_schema_version,
            ],
        )
        return artifact_id

    # Mandatory extraction family.
    artifact_id = f"art_{uuid4().hex[:12]}"
    effective_step_ref = _StepRef(
        session_id=session_id,
        step_id=step_id,
        step_type=step_type or artifact_type,
    )
    result = extractor.extract(artifact_id, content, effective_step_ref, session_id)
    validate_for_commit(extractor.family, result)

    # All writes in a single transaction.
    with store.connect() as con:
        store.execute_sql(
            con,
            """
            INSERT INTO artifacts
                (artifact_id, session_id, step_id, artifact_type, name,
                 content_json, lifecycle, artifact_schema_version)
            VALUES (?, ?, ?, ?, ?, ?, 'staged', ?)
            """,
            [
                artifact_id,
                session_id,
                step_id,
                artifact_type,
                name,
                _json.dumps(content, default=str, sort_keys=True),
                artifact_schema_version,
            ],
        )
        for f in result["findings"]:
            store.execute_sql(
                con,
                store.insert_ignore_sql(
                    "findings",
                    [
                        "finding_id",
                        "session_id",
                        "artifact_id",
                        "step_ref_json",
                        "finding_type",
                        "canonical_item_key",
                        "subject_json",
                        "observed_window_json",
                        "quality_json",
                        "provenance_json",
                        "payload_json",
                        "schema_version",
                    ],
                ),
                [
                    f["finding_id"],
                    session_id,
                    artifact_id,
                    _json.dumps(f["step_ref"]),
                    f["finding_type"],
                    f["provenance"]["canonical_item_key"],
                    _json.dumps(f["subject"]),
                    _json.dumps(f["observed_window"])
                    if f.get("observed_window") is not None
                    else None,
                    _json.dumps(f["quality"]),
                    _json.dumps(f["provenance"]),
                    _json.dumps(f["payload"]),
                    "v1",
                ],
            )
        store.execute_sql(
            con,
            "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
            [artifact_id],
        )
        con.commit()

    return artifact_id


_STEP_ID = "step_ep_001"

# ---------------------------------------------------------------------------
# Artifact payload builders (minimal valid payloads for each family)
# ---------------------------------------------------------------------------


def _observe_time_series_payload(series: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if series is None:
        series = [
            {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 100.0},
            {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 200.0},
        ]
    return {
        "artifact_family": "metric_frame",
        "shape": "time_series",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.daily_users",
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {},
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": series}]},
    }


def _observe_segmented_payload(segments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if segments is None:
        segments = [
            {"keys": {"country": "US"}, "value": 500.0},
            {"keys": {"country": "UK"}, "value": 300.0},
        ]
    series = [
        {"keys": segment.get("keys") or {}, "points": [{"value": segment.get("value")}]}
        for segment in segments
    ]
    return {
        "artifact_family": "metric_frame",
        "shape": "segmented",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {},
        },
        "axes": [{"kind": "dimension", "name": "country"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": "usd"}],
        "payload": {"series": series},
    }


def _detect_payload(candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if candidates is None:
        candidates = []
    return {
        "artifact_type": "anomaly_candidates",
        "artifact_schema_version": "v1",
        "metric": "daily_users",
        "scope": {},
        "time_scope": {
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2024-01-01", "end": "2024-01-08"},
        },
        "candidates": candidates,
        "scan_summary": {"total_candidate_count": len(candidates)},
        "analytical_metadata": {"baseline_method": "zscore"},
    }


def _compare_segmented_payload(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if rows is None:
        rows = [
            {
                "keys": {"country": "US"},
                "current_value": 500.0,
                "baseline_value": 400.0,
                "absolute_delta": 100.0,
                "relative_delta": 0.25,
                "direction": "increase",
                "presence": "both",
            },
        ]
    return {
        "artifact_type": "compare_artifact",
        "schema_version": "1.0",
        "comparison_type": "segmented_delta",
        "metric": "daily_users",
        "current_ref": {"session_id": _SESSION, "step_id": "step_obs_l", "step_type": "observe"},
        "baseline_ref": {
            "session_id": _SESSION,
            "step_id": "step_obs_r",
            "step_type": "observe",
        },
        "unit": None,
        "rows": rows,
        "resolved_input_summary": {
            "current_scope": {},
            "baseline_scope": {},
            "current_time_scope": {
                "field": "time",
                "start": "2024-01-01",
                "end": "2024-01-08",
            },
            "baseline_time_scope": {
                "field": "time",
                "start": "2023-12-25",
                "end": "2024-01-01",
            },
        },
        "comparability": {"status": "comparable", "issues": []},
        "analytical_metadata": {},
    }


def _decompose_payload(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if rows is None:
        rows = [
            {
                "key": "ios",
                "current_value": 600.0,
                "baseline_value": 500.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
        ]
    return {
        "decomposition_type": "delta_decomposition",
        "metric": "daily_users",
        "dimension": "platform",
        "unit": None,
        "compare_ref": {
            "step_type": "compare",
            "session_id": _SESSION,
            "step_id": "step_cmp_up",
            "artifact_id": "art_upstream_001",
            "comparison_type": "scalar_delta",
        },
        "current_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_l",
            "artifact_id": None,
        },
        "baseline_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_r",
            "artifact_id": None,
        },
        "rows": rows,
        "scope_absolute_delta": 200.0,
        "scope_relative_delta": 0.25,
        "scope_direction": "increase",
        "unexplained_absolute_delta": 0.0,
        "unexplained_share": 0.0,
        "unexplained_reason": "rounding",
        "attribution": {"status": "attributable", "issues": []},
        "analytical_metadata": {},
    }


def _correlate_payload(
    left_artifact_id: str = "art_obs_left_001",
    right_artifact_id: str = "art_obs_right_001",
) -> dict[str, Any]:
    return {
        "association_type": "pairwise_time_series_association",
        "left_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_l",
            "artifact_id": left_artifact_id,
        },
        "right_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_r",
            "artifact_id": right_artifact_id,
        },
        "left_metric": "dau",
        "right_metric": "revenue",
        "statistic": {
            "method": "spearman",
            "coefficient": 0.85,
            "p_value": 0.02,
            "n_pairs": 12,
        },
        "analytical_metadata": {
            "pairing_rule": "intersection_by_time_bucket",
            "matched_time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-12"},
        },
    }


def _forecast_payload(n_buckets: int = 3) -> dict[str, Any]:
    buckets = [
        {
            "bucket_index": i + 1,
            "window": {
                "start": f"2024-01-{8 + i:02d}",
                "end": f"2024-01-{9 + i:02d}",
            },
            "point_forecast": 100.0 + i,
            "prediction_interval": None,
        }
        for i in range(n_buckets)
    ]
    return {
        "observation_type": "forecast_series",
        "artifact_schema_version": "v1",
        "metric": "dau",
        "profile": "trend",
        "interval_level": 0.95,
        "forecast": buckets,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding_count_in_db(store: SQLiteMetadataStore, artifact_id: str) -> int:
    rows = store.query_rows("SELECT finding_id FROM findings WHERE artifact_id = ?", [artifact_id])
    return len(rows)


def _artifact_lifecycle(store: SQLiteMetadataStore, artifact_id: str) -> str | None:
    row = store.query_one("SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id])
    return row["lifecycle"] if row else None


# ===========================================================================
# 1. Observe — success-empty (time_series, segmented)
# ===========================================================================


class TestObserveEmptyTimeSeries(unittest.TestCase):
    """observe time_series with empty series → committed artifact, 0 findings."""

    def setUp(self) -> None:
        self.store = _make_store()

    def _commit(self) -> str:
        return _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "metric_frame",
            "obs_empty_ts",
            _observe_time_series_payload(series=[]),
            step_type="observe",
        )

    def test_metric_frame_extractor_is_registered(self) -> None:
        extractor = default_finding_registry.find("metric_frame", None)
        self.assertIsNotNone(extractor)
        assert extractor is not None
        self.assertEqual(extractor.extractor_name, "observe_metric_frame_v1")

    def test_artifact_committed(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_artifact_lifecycle(self.store, artifact_id), "committed")

    def test_zero_findings_in_db(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_finding_count_in_db(self.store, artifact_id), 0)

    def test_does_not_raise(self) -> None:
        self._commit()  # must not raise


class TestObserveEmptySegmented(unittest.TestCase):
    """observe segmented with empty segments → committed artifact, 0 findings."""

    def setUp(self) -> None:
        self.store = _make_store()

    def _commit(self) -> str:
        return _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "metric_frame",
            "obs_empty_seg",
            _observe_segmented_payload(segments=[]),
            step_type="observe",
        )

    def test_metric_frame_extractor_is_registered(self) -> None:
        extractor = default_finding_registry.find("metric_frame", None)
        self.assertIsNotNone(extractor)
        assert extractor is not None
        self.assertEqual(extractor.extractor_name, "observe_metric_frame_v1")

    def test_artifact_committed(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_artifact_lifecycle(self.store, artifact_id), "committed")

    def test_zero_findings_in_db(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_finding_count_in_db(self.store, artifact_id), 0)


# ===========================================================================
# 2. Detect — success-empty (zero candidates)
# ===========================================================================


class TestDetectEmptyCommit(unittest.TestCase):
    """detect with 0 candidates → committed artifact, 0 findings in DB."""

    def setUp(self) -> None:
        self.store = _make_store()

    def _commit(self) -> str:
        return _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "anomaly_candidates",
            "detect_empty",
            _detect_payload(candidates=[]),
            step_type="detect",
        )

    def test_artifact_committed(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_artifact_lifecycle(self.store, artifact_id), "committed")

    def test_zero_findings_in_db(self) -> None:
        artifact_id = self._commit()
        self.assertEqual(_finding_count_in_db(self.store, artifact_id), 0)

    def test_does_not_raise(self) -> None:
        self._commit()


# ===========================================================================
# 3. Compare — failure on empty segmented rows
# ===========================================================================


class TestCompareEmptyRejects(unittest.TestCase):
    """compare_artifact with empty segmented rows → FamilyEmptyError, no artifact in DB."""

    def setUp(self) -> None:
        self.store = _make_store()

    def _commit_empty(self) -> None:
        _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "compare_artifact",
            "cmp_empty",
            _compare_segmented_payload(rows=[]),
            step_type="compare",
        )

    def test_raises_family_empty_error(self) -> None:
        with self.assertRaises(FamilyEmptyError) as ctx:
            self._commit_empty()
        self.assertEqual(ctx.exception.family, "compare")

    def test_no_artifact_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            self._commit_empty()
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'compare_artifact'",
            [_SESSION],
        )
        self.assertEqual(rows, [])

    def test_no_findings_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            self._commit_empty()
        rows = self.store.query_rows("SELECT * FROM findings WHERE session_id = ?", [_SESSION])
        self.assertEqual(rows, [])


# ===========================================================================
# 4. Decompose — failure on empty rows
# ===========================================================================


class TestDecomposeEmptyRejects(unittest.TestCase):
    """delta_decomposition with empty rows → FamilyEmptyError, no artifact in DB."""

    def setUp(self) -> None:
        self.store = _make_store()

    def _commit_empty(self) -> None:
        _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "delta_decomposition",
            "decomp_empty",
            _decompose_payload(rows=[]),
            step_type="decompose",
        )

    def test_raises_family_empty_error(self) -> None:
        with self.assertRaises(FamilyEmptyError) as ctx:
            self._commit_empty()
        self.assertEqual(ctx.exception.family, "decompose")

    def test_no_artifact_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            self._commit_empty()
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'delta_decomposition'",
            [_SESSION],
        )
        self.assertEqual(rows, [])

    def test_no_findings_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            self._commit_empty()
        rows = self.store.query_rows("SELECT * FROM findings WHERE session_id = ?", [_SESSION])
        self.assertEqual(rows, [])


# ===========================================================================
# 5. Correlate — success with exactly 1 finding
# ===========================================================================


class TestCorrelateCommit(unittest.TestCase):
    """pairwise_time_series_association → committed artifact, exactly 1 finding in DB."""

    def setUp(self) -> None:
        self.store = _make_store()

        self.artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "pairwise_time_series_association",
            "correlate_001",
            _correlate_payload(),
            step_type="correlate",
        )

    def test_artifact_committed(self) -> None:
        self.assertEqual(_artifact_lifecycle(self.store, self.artifact_id), "committed")

    def test_exactly_one_finding(self) -> None:
        self.assertEqual(_finding_count_in_db(self.store, self.artifact_id), 1)

    def test_finding_type_is_correlation_result(self) -> None:
        rows = self.store.query_rows(
            "SELECT finding_type FROM findings WHERE artifact_id = ?", [self.artifact_id]
        )
        self.assertEqual(rows[0]["finding_type"], "correlation_result")


# ===========================================================================
# 6. Forecast — success with N findings (one per bucket)
# ===========================================================================


class TestForecastCommit(unittest.TestCase):
    """forecast_series with N buckets → committed artifact, N findings in DB."""

    _N_BUCKETS = 3

    def setUp(self) -> None:
        self.store = _make_store()

        self.artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION,
            _STEP_ID,
            "forecast_series",
            "forecast_001",
            _forecast_payload(n_buckets=self._N_BUCKETS),
            step_type="forecast",
        )

    def test_artifact_committed(self) -> None:
        self.assertEqual(_artifact_lifecycle(self.store, self.artifact_id), "committed")

    def test_findings_count_equals_buckets(self) -> None:
        self.assertEqual(_finding_count_in_db(self.store, self.artifact_id), self._N_BUCKETS)

    def test_finding_types_are_forecast_point(self) -> None:
        rows = self.store.query_rows(
            "SELECT finding_type FROM findings WHERE artifact_id = ?", [self.artifact_id]
        )
        for row in rows:
            self.assertEqual(row["finding_type"], "forecast_point")

    def test_one_bucket_produces_one_finding(self) -> None:
        store = _make_store()
        art_id = _commit_artifact_with_extraction(
            store,
            _SESSION,
            _STEP_ID,
            "forecast_series",
            "forecast_1b",
            _forecast_payload(n_buckets=1),
            step_type="forecast",
        )
        self.assertEqual(_finding_count_in_db(store, art_id), 1)


# ===========================================================================
# 7. Success-empty replay stability (observe, detect)
# ===========================================================================


class TestSuccessEmptyObserveReplayStability(unittest.TestCase):
    """Re-extracting an empty observe artifact produces 0 findings; no synthetic finding."""

    def _extract(self, artifact_id: str, payload: dict[str, Any]) -> Any:
        from marivo.runtime.evidence.observe_extractor import ObserveArtifactExtractor

        extractor = ObserveArtifactExtractor()
        step_ref = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="observe")
        return extractor.extract(artifact_id, payload, step_ref, _SESSION)

    def test_empty_time_series_replay_still_zero(self) -> None:
        art_id = "art_observe_replay_ts001"
        payload = _observe_time_series_payload(series=[])
        result1 = self._extract(art_id, payload)
        result2 = self._extract(art_id, payload)
        self.assertEqual(result1["finding_count"], 0)
        self.assertEqual(result2["finding_count"], 0)

    def test_empty_segmented_replay_still_zero(self) -> None:
        art_id = "art_observe_replay_seg001"
        payload = _observe_segmented_payload(segments=[])
        result1 = self._extract(art_id, payload)
        result2 = self._extract(art_id, payload)
        self.assertEqual(result1["finding_count"], 0)
        self.assertEqual(result2["finding_count"], 0)

    def test_empty_replay_produces_no_findings_list(self) -> None:
        art_id = "art_observe_replay_empty_list"
        payload = _observe_time_series_payload(series=[])
        for _ in range(2):
            result = self._extract(art_id, payload)
            self.assertEqual(result["findings"], [])


class TestSuccessEmptyDetectReplayStability(unittest.TestCase):
    """Re-extracting an empty detect artifact produces 0 findings; no synthetic finding."""

    def _extract(self, artifact_id: str, payload: dict[str, Any]) -> Any:
        from marivo.runtime.evidence.detect_extractor import DetectArtifactExtractor

        extractor = DetectArtifactExtractor()
        step_ref = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="detect")
        return extractor.extract(artifact_id, payload, step_ref, _SESSION)

    def test_empty_candidates_replay_still_zero(self) -> None:
        art_id = "art_detect_replay_empty001"
        payload = _detect_payload(candidates=[])
        result1 = self._extract(art_id, payload)
        result2 = self._extract(art_id, payload)
        self.assertEqual(result1["finding_count"], 0)
        self.assertEqual(result2["finding_count"], 0)

    def test_empty_replay_produces_no_findings_list(self) -> None:
        art_id = "art_detect_replay_empty_list"
        payload = _detect_payload(candidates=[])
        for _ in range(2):
            result = self._extract(art_id, payload)
            self.assertEqual(result["findings"], [])


# ===========================================================================
# 8. Finding ID replay stability (non-empty families)
# ===========================================================================


class TestFindingIdReplayStability(unittest.TestCase):
    """Same artifact_id + payload → same finding_id on every extraction."""

    def test_correlate_finding_id_stable_across_re_extraction(self) -> None:
        from marivo.runtime.evidence.correlate_extractor import CorrelateArtifactExtractor

        extractor = CorrelateArtifactExtractor()
        art_id = "art_correlate_replay001"
        step_ref = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="correlate")
        payload = _correlate_payload()

        result1 = extractor.extract(art_id, payload, step_ref, _SESSION)
        result2 = extractor.extract(art_id, payload, step_ref, _SESSION)

        ids1 = [f["finding_id"] for f in result1["findings"]]
        ids2 = [f["finding_id"] for f in result2["findings"]]
        self.assertEqual(ids1, ids2)
        self.assertEqual(len(ids1), 1)

    def test_observe_scalar_finding_id_stable_across_re_extraction(self) -> None:
        from marivo.runtime.evidence.observe_extractor import ObserveArtifactExtractor

        extractor = ObserveArtifactExtractor()
        art_id = "art_observe_scalar_replay001"
        step_ref = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="observe")
        payload: dict[str, Any] = {
            "artifact_family": "metric_frame",
            "shape": "scalar",
            "subject": {
                "kind": "metric",
                "metric_ref": "metric.dau",
                "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
                "scope": {},
            },
            "axes": [],
            "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
            "payload": {"series": [{"keys": {}, "points": [{"value": 1234.0}]}]},
        }

        result1 = extractor.extract(art_id, payload, step_ref, _SESSION)
        result2 = extractor.extract(art_id, payload, step_ref, _SESSION)

        ids1 = [f["finding_id"] for f in result1["findings"]]
        ids2 = [f["finding_id"] for f in result2["findings"]]
        self.assertEqual(ids1, ids2)
        self.assertEqual(len(ids1), 1)

    def test_forecast_finding_ids_stable_across_re_extraction(self) -> None:
        from marivo.runtime.evidence.forecast_extractor import ForecastArtifactExtractor

        extractor = ForecastArtifactExtractor()
        art_id = "art_forecast_replay001"
        step_ref = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="forecast")
        payload = _forecast_payload(n_buckets=3)

        result1 = extractor.extract(art_id, payload, step_ref, _SESSION)
        result2 = extractor.extract(art_id, payload, step_ref, _SESSION)

        ids1 = sorted(f["finding_id"] for f in result1["findings"])
        ids2 = sorted(f["finding_id"] for f in result2["findings"])
        self.assertEqual(ids1, ids2)
        self.assertEqual(len(ids1), 3)


if __name__ == "__main__":
    unittest.main()
