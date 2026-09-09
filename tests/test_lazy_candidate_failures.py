"""Real Candidate budgets, faults and cold corruption preserve atomic authority."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json, descriptor_payload
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from tests.lazy_candidate_fixtures import candidate_input, discover, setup_candidate
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


def _unchanged(before: dict[str, object], after: dict[str, object]) -> None:
    old, new = before["tables"], after["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert old[table] == new[table]


@pytest.mark.parametrize(
    "policy",
    [
        replace(LocalPolicy(), max_input_rows=1),
        replace(LocalPolicy(), max_method_rows=1),
        replace(LocalPolicy(), max_input_bytes=1),
        replace(LocalPolicy(), max_output_rows=1),
        replace(LocalPolicy(), max_output_bytes=1),
        replace(LocalPolicy(), max_intermediate_bytes=1),
        replace(LocalPolicy(), max_worker_rss=1),
        replace(LocalPolicy(), deadline_seconds=0.001),
    ],
)
def test_candidate_guards_publish_nothing(tmp_path: Path, policy: LocalPolicy) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    runtime.local_policy = policy
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        discover(
            candidate_input(source, "point_anomalies"), "point_anomalies", threshold=0.1
        ).execute()
    _unchanged(before, snapshot(runtime))
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None


@pytest.mark.parametrize(
    "point",
    [
        "backend_compile",
        "source_statement",
        "transfer",
        "after_rename",
        "quality",
        "evidence",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ],
)
def test_candidate_fault_rolls_back_complete_bundle(tmp_path: Path, point: str) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    observed: list[str] = []

    def fault(event: str) -> None:
        if event == point:
            observed.append(event)
            raise RuntimeError("candidate-private-canary")

    runtime._hook = fault
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        discover(candidate_input(source, "point_anomalies"), "point_anomalies").execute()
    assert "candidate-private-canary" not in str(error.value)
    assert observed == [point]
    _unchanged(before, snapshot(runtime))


@pytest.mark.parametrize("point", ["transfer", "insert_findings"])
def test_candidate_cancellation_rolls_back(tmp_path: Path, point: str) -> None:
    runtime, source, _ = setup_candidate(tmp_path)

    def cancel(event: str) -> None:
        if event == point:
            raise KeyboardInterrupt()

    runtime._hook = cancel
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        discover(candidate_input(source, "interesting_windows"), "interesting_windows").execute()
    _unchanged(before, snapshot(runtime))


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
@pytest.mark.parametrize("values", [(1.0,), (1.0,) * 14, (1e308, -1e308) * 7])
def test_unevaluable_or_overflowed_discovery_never_publishes(
    tmp_path: Path, objective: CandidateObjective, values: tuple[float, ...]
) -> None:
    runtime, source, _ = setup_candidate(tmp_path, values)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        discover(candidate_input(source, objective), objective).execute()
    _unchanged(before, snapshot(runtime))


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_evaluated_empty_publishes_evidence_and_zero_findings(
    tmp_path: Path, objective: CandidateObjective
) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    result = discover(candidate_input(source, objective), objective, threshold=100.0).execute()
    assert result.to_pandas().empty
    assert result.evidence_digest.finding_count == 0 and result.findings().items == ()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    evidence = record.descriptor.candidate_evidence
    assert evidence.row_count == evidence.evaluation.emitted_candidate_count == 0
    assert evidence.evaluation.evaluated_series_count > 0
    assert evidence.evaluation.evaluated_unit_count > 0


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
@pytest.mark.parametrize("point", ["after_commit", "delivery"])
def test_candidate_lost_ack_recovers_exact_zero_finding_bundle(
    tmp_path: Path, objective: CandidateObjective, point: str
) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    observed: list[str] = []

    def lost(event: str) -> None:
        if event == point:
            observed.append(event)
            raise RuntimeError("lost-candidate-acknowledgement")

    runtime._hook = lost
    logical = discover(candidate_input(source, objective), objective)
    result = logical.execute()
    assert observed == [point]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.producing_run_ref == runtime.last_run_ref
    assert record.descriptor.candidate_evidence is not None
    assert record.evidence.finding_count == 0 and result.findings().items == ()
    before = snapshot(runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(runtime) == before and runtime.statistics.primary_queries == 0


def test_cold_candidate_rejects_damaged_original_evaluation(tmp_path: Path) -> None:
    runtime, source, database = setup_candidate(tmp_path)
    result = discover(candidate_input(source, "point_anomalies"), "point_anomalies").execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    payload = descriptor_payload(record.descriptor)
    evidence = payload["candidate_evidence"]
    assert isinstance(evidence, dict) and isinstance(evidence["evaluation"], dict)
    evidence["evaluation"]["evaluated_series_count"] = 0
    with sqlite3.connect(runtime.store.db_path) as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), result.state.artifact_ref.ref),
        )
    database.rename(tmp_path / "origin.offline")
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(result.state.artifact_ref.ref)
    assert snapshot(runtime) == before


def test_discovery_source_failure_never_publishes_candidate_authority(tmp_path: Path) -> None:
    runtime, source, database = setup_candidate(tmp_path)
    logical = discover(candidate_input(source, "point_anomalies"), "point_anomalies")
    database.rename(tmp_path / "origin.offline")
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        logical.execute()
    old, new = before["tables"], snapshot(runtime)["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings"):
        assert old[table] == new[table]
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.output_artifact_ref is None
    # A lost native backend handshake may retain its owning recovery journal.
    assert all(
        resource.run_ref == runtime.last_run_ref
        for resource in runtime.store.resources(runtime.session_ref)
    )
