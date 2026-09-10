"""Real native matching, identity authority and atomic journey publication."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.event import every_start
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.event_codec import EventEvidenceSummary
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_fixtures import OCCURRENCE_CANARY, journey, setup_event
from tests.lazy_event_runtime_worker import assert_identity_private

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("engine", [False, True])
def test_native_event_roundtrip_and_cold_recovery(tmp_path: Path, engine: bool) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=engine)
    logical = journey(sources)
    result = logical.execute()
    assert isinstance(result, MaterializedEventDataset)
    rows = result.to_pandas()
    assert list(rows.columns) == [
        "journey_id",
        "completion_status",
        "entity_identity",
        "step_key",
        "event_identity",
        "occurred_at",
        "elapsed_from_start",
        "elapsed_from_previous",
    ]
    assert rows.entity_identity.tolist() == [(1,), (1,), (2,), (2,)]
    assert rows.step_key.tolist() == ["start", "finish", "start", "finish"]
    assert rows.completion_status.tolist() == ["complete", "complete", "incomplete", "incomplete"]
    assert rows.event_identity.iloc[0] == (OCCURRENCE_CANARY,)
    assert rows.event_identity.iloc[1] == (OCCURRENCE_CANARY + 10,)
    assert rows.occurred_at.iloc[1].hour == 3
    assert str(OCCURRENCE_CANARY) not in repr(result.evidence_digest)
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.event_evidence is not None
    assert record.descriptor.event_evidence.coverage.basis == "declared"
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered = cold.artifact(result.state.artifact_ref)
    assert recovered.to_pandas().equals(rows)
    assert recovered.evidence_digest == result.evidence_digest
    before = snapshot(runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(runtime) == before


@pytest.mark.parametrize("assignment", ["shared", "exclusive"])
def test_every_start_completion_assignment(tmp_path: Path, assignment: str) -> None:
    runtime, sources, _ = setup_event(tmp_path)
    policy = every_start(completion_assignment="shared" if assignment == "shared" else "exclusive")
    result = journey(sources, matching=policy).execute()
    rows = result.to_pandas()
    finals = rows[rows.step_key == "finish"]
    assert len(rows) == 6
    assert finals.event_identity.iloc[:2].tolist() == [
        (OCCURRENCE_CANARY + 10,),
        (OCCURRENCE_CANARY + (10 if assignment == "shared" else 11),),
    ]
    assert runtime.statistics.local_handoffs == ()


def test_unknown_coverage_and_empty_membership(tmp_path: Path) -> None:
    runtime, sources, database = setup_event(tmp_path)
    result = journey(sources, complete=False).execute()
    rows = result.to_pandas()
    assert set(rows.completion_status) == {"complete", "coverage_censored"}
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
    other = DatasetRuntime.create(tmp_path, "empty-event")
    empty_sources = other.sources(
        semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar
    )
    empty = journey(empty_sources).execute()
    assert empty.to_pandas().empty


@pytest.mark.parametrize("failure", ["quality", "source_statement"])
def test_event_failure_has_no_partial_authority(tmp_path: Path, failure: str) -> None:
    def fail(point: str) -> None:
        if point == failure:
            raise RuntimeError("injected Event failure")

    runtime, sources, _ = setup_event(tmp_path, event=fail)
    with pytest.raises(MaterializationError):
        journey(sources).execute()
    assert snapshot(runtime)["dataset_artifacts"] == 0
    assert snapshot(runtime)["dataset_evidence"] == 0


def test_duplicate_occurrence_fails_without_identity_diagnostic(tmp_path: Path) -> None:
    runtime, sources, database = setup_event(tmp_path)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("INSERT INTO started_rows SELECT * FROM started_rows LIMIT 1")
    with pytest.raises(MaterializationError) as caught:
        journey(sources).execute()
    assert str(OCCURRENCE_CANARY) not in str(caught.value)
    assert snapshot(runtime)["dataset_artifacts"] == 0


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("point", ["before_commit", "cancel"])
def test_event_commit_failure_and_cancellation_cleanup(
    tmp_path: Path, engine: bool, point: str
) -> None:
    def fail(current: str) -> None:
        if current == ("quality" if point == "cancel" else point):
            if point == "cancel":
                raise KeyboardInterrupt("private-cancellation-canary")
            raise RuntimeError("private-commit-canary")

    runtime, sources, _ = setup_event(tmp_path, engine=engine, event=fail)
    with pytest.raises(MaterializationError) as caught:
        journey(sources).execute()
    assert "canary" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    counts = snapshot(runtime)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    assert counts["analysis_action_run_terminals"] == 1
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
    assert_identity_private(runtime)


@pytest.mark.parametrize("engine", [False, True])
def test_event_storage_failure_never_publishes_authority(tmp_path: Path, engine: bool) -> None:
    def fail(current: str) -> None:
        if current == "after_rename":
            raise OSError("private-storage-canary")

    runtime, sources, _ = setup_event(tmp_path, engine=engine, event=fail)
    with pytest.raises(MaterializationError):
        journey(sources).execute()
    assert snapshot(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()


def test_lost_event_commit_acknowledgement_recovers_one_bundle(tmp_path: Path) -> None:
    def fail(current: str) -> None:
        if current == "after_commit":
            raise RuntimeError("lost acknowledgement")

    runtime, sources, _ = setup_event(tmp_path, event=fail)
    logical = journey(sources)
    result = logical.execute()
    before = snapshot(runtime)
    assert before["dataset_artifacts"] == before["dataset_evidence"] == 1
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(runtime) == before
    assert_identity_private(runtime)


def test_large_identity_relation_stays_inside_the_native_engine(tmp_path: Path) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute(
            "INSERT INTO customers (id) SELECT 881730041 + i FROM range(5000) AS t(i)"
        )
        connection.execute(
            "INSERT INTO started_rows SELECT 981730041 + i, 881730041 + i, TIMESTAMP '2026-02-01 12:00:00' FROM range(5000) AS t(i)"
        )
    with (
        patch.object(admission, "supervise", forbidden),
        patch.object(DatasetRuntime, "_batches", forbidden),
        patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
    ):
        result = journey(sources).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.event_evidence is not None
    summary = record.descriptor.event_evidence
    assert isinstance(summary, EventEvidenceSummary)
    assert (summary.row_count, summary.journey_count, summary.subject_count) == (10000, 5000, 5000)
    assert summary.missing_row_count == summary.incomplete_journey_count == 5000
    assert runtime.statistics.transferred_rows == runtime.statistics.transferred_bytes == 0
    assert runtime.statistics.worker_pid is None
    assert runtime.statistics.local_handoffs == ()
    assert_identity_private(runtime, ("881730041", "981730041"))
