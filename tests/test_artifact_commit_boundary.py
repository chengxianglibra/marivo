"""Extraction transaction tests for the canonical commit boundary (Phase 4c-1).

Covers acceptance criteria:
- No extractor registered → artifact committed directly (backward compatible)
- Registered extractor success → artifact committed + findings persisted
- Extractor raises → no artifact row written, exception re-raised, no findings written
- validate_for_commit raises FamilyEmptyError → no artifact row written
- validate_for_commit raises ValueError (count mismatch) → no artifact row written
- Finding idempotency (same finding_id replay is silently ignored)
- observe family allows empty committed finding set (D4 allow-empty)
- compare family rejects empty committed finding set (D4 non-empty required)
"""

from __future__ import annotations

import contextlib
import unittest
from typing import Any

from marivo.evidence_engine.canonical_finding import (
    FindingExtractionResult,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.evidence_engine.family_contract import FamilyEmptyError
from marivo.evidence_engine.finding_extractor_registry import (
    FindingExtractor,
    FindingExtractorRegistry,
)
from marivo.storage.evidence_repositories import FindingRepository
from marivo.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "sess_commit_boundary"
_STEP_ID = "step_cbt001"


def _make_store() -> SQLiteMetadataStore:
    store = make_temp_metadata_store()
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [_SESSION_ID, "commit boundary test", "{}", "{}", "open"],
    )
    return store


class _FailFindingInsertSQLiteStore(ManagedSQLiteMetadataStore):
    def execute_sql(self, con: Any, sql: str, params: list[Any] | None = None) -> Any:
        if "INTO findings" in sql:
            raise RuntimeError("injected finding insert failure")
        return super().execute_sql(con, sql, params)


def _make_failing_finding_store() -> _FailFindingInsertSQLiteStore:
    store = make_temp_metadata_store(store_cls=_FailFindingInsertSQLiteStore)
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [_SESSION_ID, "commit boundary test", "{}", "{}", "open"],
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

    from marivo.evidence_engine.canonical_finding import StepRef as _StepRef
    from marivo.evidence_engine.finding_extractor_registry import (
        default_finding_registry as _default_reg,
    )
    from marivo.evidence_engine.finding_extractor_registry import (
        validate_for_commit as _validate,
    )

    registry = _registry if _registry is not None else _default_reg
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
    _validate(extractor.family, result)

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


def _build_observation_finding(artifact_id: str) -> dict[str, Any]:
    """Build a minimal but structurally complete observation finding dict."""
    canonical_item_key, item_ref = make_item_identity("value")
    finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)
    return {
        "finding_id": finding_id,
        "finding_type": "observation",
        "artifact_id": artifact_id,
        "step_ref": StepRef(
            session_id=_SESSION_ID,
            step_id=_STEP_ID,
            step_type="observation_artifact",
        ),
        "subject": {
            "metric": "test_metric",
            "entity": None,
            "slice": {},
            "grain": None,
            "analysis_axis": "scalar",
        },
        "observed_window": None,
        "quality": {
            "data_complete": None,
            "sample_size": None,
            "row_count": None,
            "null_rate": None,
            "quality_status": "ready",
            "quality_warnings": [],
        },
        "provenance": {
            "source_step_type": "observation_artifact",
            "extractor_name": "obs_stub",
            "extractor_version": "0.0.1",
            "artifact_schema_version": "v1",
            "canonical_item_key": canonical_item_key,
            "artifact_item_ref": item_ref,
            "projection_ref": None,
        },
        "payload": {"observation_kind": "scalar", "value": 42.0, "unit": None},
    }


# ---------------------------------------------------------------------------
# Stub extractors (isolated — NOT registered in default_finding_registry)
# ---------------------------------------------------------------------------


class _ObserveSuccessExtractor(FindingExtractor):
    """Returns 1 valid observation finding."""

    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "obs_success_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        finding = _build_observation_finding(artifact_id)
        return {
            "findings": [finding],  # type: ignore[list-item]
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 1,
        }


class _ObserveEmptyExtractor(FindingExtractor):
    """Returns 0 findings — valid for the 'observe' allow-empty family."""

    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "obs_empty_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        return {
            "findings": [],
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


class _CompareEmptyExtractor(FindingExtractor):
    """Returns 0 findings for the 'compare' family — must be rejected (D4 non-empty)."""

    artifact_type = "compare_artifact"
    artifact_schema_version = "v1"
    family = "compare"
    extractor_name = "compare_empty_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        return {
            "findings": [],
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,
        }


class _ObserveCountMismatchExtractor(FindingExtractor):
    """Returns 1 finding but declares finding_count=0 — internal inconsistency."""

    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "obs_count_mismatch_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        finding = _build_observation_finding(artifact_id)
        return {
            "findings": [finding],  # type: ignore[list-item]
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "artifact_schema_version": self.artifact_schema_version,
            "finding_count": 0,  # wrong: should be 1
        }


class _ObserveRaiseExtractor(FindingExtractor):
    """Raises RuntimeError from extract() to simulate extraction crash."""

    artifact_type = "observation_artifact"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "obs_raise_stub"
    extractor_version = "0.0.1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        raise RuntimeError("Simulated extraction crash")


# ---------------------------------------------------------------------------
# Test: no extractor registered → direct commit (backward compatible)
# ---------------------------------------------------------------------------


class TestNoExtractorDirectCommit(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()  # empty — no extractor registered

    def test_no_extractor_artifact_lifecycle_is_committed(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "profile",  # not registered in the empty registry
            "test_profile",
            {"table_name": "t", "row_count": 100},
            _registry=self.registry,
        )
        row = self.store.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")

    def test_no_extractor_no_findings_written(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "profile",
            "test_profile",
            {"table_name": "t", "row_count": 100},
            _registry=self.registry,
        )
        rows = self.store.query_rows("SELECT * FROM findings WHERE artifact_id = ?", [artifact_id])
        self.assertEqual(rows, [])

    def test_no_extractor_returns_artifact_id(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "profile",
            "test_profile",
            {},
            _registry=self.registry,
        )
        self.assertIsNotNone(artifact_id)
        self.assertTrue(artifact_id.startswith("art_"))


# ---------------------------------------------------------------------------
# Test: extraction success → artifact committed + findings persisted
# ---------------------------------------------------------------------------


class TestExtractionSuccess(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveSuccessExtractor())

    def test_success_artifact_lifecycle_is_committed(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_test",
            {"observation_type": "scalar"},
            _registry=self.registry,
        )
        row = self.store.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")

    def test_success_findings_persisted(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_test",
            {"observation_type": "scalar"},
            _registry=self.registry,
        )
        rows = self.store.query_rows("SELECT * FROM findings WHERE artifact_id = ?", [artifact_id])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["finding_type"], "observation")
        self.assertEqual(rows[0]["session_id"], _SESSION_ID)

    def test_success_finding_id_is_deterministic(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_test",
            {"observation_type": "scalar"},
            _registry=self.registry,
        )
        rows = self.store.query_rows(
            "SELECT finding_id FROM findings WHERE artifact_id = ?", [artifact_id]
        )
        self.assertEqual(len(rows), 1)
        # finding_id is derived from artifact_id, so it must reference the committed artifact
        expected_key, _ = make_item_identity("value")
        expected_id = make_finding_id(artifact_id, "observation", expected_key)
        self.assertEqual(rows[0]["finding_id"], expected_id)


class TestTransactionRollbackAfterArtifactStage(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_failing_finding_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveSuccessExtractor())

    def test_finding_insert_failure_rolls_back_staged_artifact(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "injected finding insert failure"):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {"observation_type": "scalar"},
                _registry=self.registry,
            )

        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM artifacts WHERE session_id = ?",
                [_SESSION_ID],
            ),
            {"cnt": 0},
        )
        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM findings WHERE session_id = ?",
                [_SESSION_ID],
            ),
            {"cnt": 0},
        )
        self.assertEqual(
            self.store.query_one(
                "SELECT COUNT(*) AS cnt FROM propositions WHERE session_id = ?",
                [_SESSION_ID],
            ),
            {"cnt": 0},
        )


# ---------------------------------------------------------------------------
# Test: extractor crash → no artifact row written, no findings
# ---------------------------------------------------------------------------


class TestExtractionCrashNoArtifact(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveRaiseExtractor())

    def test_extraction_failure_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )

    def test_extraction_failure_no_artifact_written(self) -> None:
        with contextlib.suppress(RuntimeError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )
        # Extraction runs before any DB write; crash must leave no artifact row.
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'observation_artifact'",
            [_SESSION_ID],
        )
        self.assertEqual(rows, [])

    def test_extraction_failure_no_findings_written(self) -> None:
        with contextlib.suppress(RuntimeError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )
        rows = self.store.query_rows("SELECT * FROM findings WHERE session_id = ?", [_SESSION_ID])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# Test: FamilyEmptyError → no artifact row written
# ---------------------------------------------------------------------------


class TestFamilyEmptyErrorNoArtifact(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_CompareEmptyExtractor())

    def test_family_empty_error_is_raised(self) -> None:
        with self.assertRaises(FamilyEmptyError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "compare_artifact",
                "cmp_test",
                {},
                _registry=self.registry,
            )

    def test_family_empty_error_no_artifact_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "compare_artifact",
                "cmp_test",
                {},
                _registry=self.registry,
            )
        # Validation runs before any DB write; failure must leave no artifact row.
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'compare_artifact'",
            [_SESSION_ID],
        )
        self.assertEqual(rows, [])

    def test_family_empty_error_no_findings_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "compare_artifact",
                "cmp_test",
                {},
                _registry=self.registry,
            )
        rows = self.store.query_rows("SELECT * FROM findings WHERE session_id = ?", [_SESSION_ID])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# Test: count mismatch → ValueError → no artifact row written
# ---------------------------------------------------------------------------


class TestCountMismatchNoArtifact(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveCountMismatchExtractor())

    def test_count_mismatch_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )

    def test_count_mismatch_no_artifact_written(self) -> None:
        with contextlib.suppress(ValueError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )
        # Validation runs before any DB write; failure must leave no artifact row.
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'observation_artifact'",
            [_SESSION_ID],
        )
        self.assertEqual(rows, [])

    def test_count_mismatch_no_findings_written(self) -> None:
        with contextlib.suppress(ValueError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "observation_artifact",
                "obs_test",
                {},
                _registry=self.registry,
            )
        rows = self.store.query_rows("SELECT * FROM findings WHERE session_id = ?", [_SESSION_ID])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# Test: finding idempotency (replay safety)
# ---------------------------------------------------------------------------


class TestFindingIdempotency(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveSuccessExtractor())

    def test_replay_does_not_error(self) -> None:
        """Re-inserting the same finding_id via FindingRepository.create() is silent."""
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_replay",
            {},
            _registry=self.registry,
        )
        # Fetch the committed finding and re-insert it (simulates replay)
        rows = self.store.query_rows("SELECT * FROM findings WHERE artifact_id = ?", [artifact_id])
        self.assertEqual(len(rows), 1)
        repo = FindingRepository(self.store)
        repo.create(
            {
                "finding_id": rows[0]["finding_id"],
                "session_id": rows[0]["session_id"],
                "artifact_id": rows[0]["artifact_id"],
                "step_ref_json": rows[0]["step_ref_json"],
                "finding_type": rows[0]["finding_type"],
                "canonical_item_key": rows[0]["canonical_item_key"],
                "subject_json": rows[0]["subject_json"],
                "observed_window_json": rows[0]["observed_window_json"],
                "quality_json": rows[0]["quality_json"],
                "provenance_json": rows[0]["provenance_json"],
                "payload_json": rows[0]["payload_json"],
                "schema_version": rows[0]["schema_version"],
            }
        )
        # Still exactly 1 finding after idempotent replay.
        after = self.store.query_rows("SELECT * FROM findings WHERE artifact_id = ?", [artifact_id])
        self.assertEqual(len(after), 1)

    def test_finding_id_is_stable_for_same_artifact(self) -> None:
        """The same artifact payload must produce the same finding_id on replay."""
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_stable",
            {},
            _registry=self.registry,
        )
        rows = self.store.query_rows(
            "SELECT finding_id FROM findings WHERE artifact_id = ?", [artifact_id]
        )
        expected_key, _ = make_item_identity("value")
        expected_id = make_finding_id(artifact_id, "observation", expected_key)
        self.assertEqual(rows[0]["finding_id"], expected_id)


# ---------------------------------------------------------------------------
# Test: observe allows empty (D4)
# ---------------------------------------------------------------------------


class TestObserveAllowsEmpty(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_ObserveEmptyExtractor())

    def test_observe_empty_artifact_is_committed(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_empty",
            {},
            _registry=self.registry,
        )
        row = self.store.query_one(
            "SELECT lifecycle FROM artifacts WHERE artifact_id = ?", [artifact_id]
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["lifecycle"], "committed")

    def test_observe_empty_no_findings_written(self) -> None:
        artifact_id = _commit_artifact_with_extraction(
            self.store,
            _SESSION_ID,
            _STEP_ID,
            "observation_artifact",
            "obs_empty",
            {},
            _registry=self.registry,
        )
        rows = self.store.query_rows("SELECT * FROM findings WHERE artifact_id = ?", [artifact_id])
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# Test: compare rejects empty (D4)
# ---------------------------------------------------------------------------


class TestCompareRejectsEmpty(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.registry = FindingExtractorRegistry()
        self.registry.register(_CompareEmptyExtractor())

    def test_compare_empty_raises_family_empty_error(self) -> None:
        with self.assertRaises(FamilyEmptyError) as ctx:
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "compare_artifact",
                "cmp_empty",
                {},
                _registry=self.registry,
            )
        self.assertEqual(ctx.exception.family, "compare")

    def test_compare_empty_no_artifact_written(self) -> None:
        with contextlib.suppress(FamilyEmptyError):
            _commit_artifact_with_extraction(
                self.store,
                _SESSION_ID,
                _STEP_ID,
                "compare_artifact",
                "cmp_empty",
                {},
                _registry=self.registry,
            )
        # Validation runs before any DB write; failure must leave no artifact row.
        rows = self.store.query_rows(
            "SELECT artifact_id FROM artifacts "
            "WHERE session_id = ? AND artifact_type = 'compare_artifact'",
            [_SESSION_ID],
        )
        self.assertEqual(rows, [])
