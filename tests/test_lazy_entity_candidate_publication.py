"""Closed Entity evaluation proofs and private atomic publication failures."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.evidence._dataset_codec import finding_set_digest
from marivo.analysis.materialization import admission, candidate_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.candidate_codec import (
    CandidateEvidenceSummary,
    decode_evidence,
    evidence_payload,
    validate_evidence,
)
from marivo.analysis.materialization.contracts import canonical_json, descriptor_payload, parse_json
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.targets import EngineTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.operators.candidate_contracts import (
    CandidatePayload,
    EntityCandidateEvaluationSummary,
)
from tests.lazy_entity_candidate_fixtures import entity_metric, setup_entity_candidate
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_observation_fixtures import make_sources


def _evidence() -> CandidateEvidenceSummary:
    candidate = entity_metric(make_sources()).discover.entity_outliers()
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    return CandidateEvidenceSummary(
        row_count=1,
        emitted_finding_count=0,
        finding_set_digest=finding_set_digest(()),
        definition=candidate._root.payload.spec.definition,
        evaluation=EntityCandidateEvaluationSummary(
            input_row_count=4,
            non_null_value_count=4,
            null_value_count=0,
            center=1.0,
            scale=2.25,
            scale_method="mean_absolute_deviation",
            pre_limit_candidate_count=1,
            emitted_candidate_count=1,
            score_range=(4.0, 4.0),
            reason_counts=(("entity_mad_threshold_met", 1),),
        ),
    )


def test_entity_summary_is_closed_and_distinct_from_time_moments() -> None:
    evidence = _evidence()
    encoded = canonical_json(evidence_payload(evidence))
    assert decode_evidence(parse_json(encoded)) == evidence
    assert "baseline_mean_range" not in encoded and "baseline_stddev_range" not in encoded
    decoded = parse_json(encoded)
    assert isinstance(decoded, dict) and isinstance(decoded["evaluation"], dict)
    assert "entity_identity" not in decoded["evaluation"]
    assert decode_evidence(None) is None
    empty = replace(
        evidence,
        row_count=0,
        definition=replace(evidence.definition, threshold=10.0),
        evaluation=replace(
            evidence.evaluation,
            pre_limit_candidate_count=0,
            emitted_candidate_count=0,
            score_range=None,
            reason_counts=(("entity_mad_threshold_met", 0),),
        ),
    )
    validate_evidence(empty)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("input_row_count", 3),
        ("non_null_value_count", 2),
        ("null_value_count", -1),
        ("center", "not-a-number"),
        ("scale", 0),
        ("scale_method", "stddev"),
        ("pre_limit_candidate_count", 5),
        ("emitted_candidate_count", 0),
        ("score_range", [2.0, 4.0]),
        ("reason_counts", [["entity_mad_threshold_met", 2]]),
        ("baseline_mean_range", [1.0, 1.0]),
    ],
)
def test_forged_entity_summary_is_rejected(field: str, replacement: object) -> None:
    payload = parse_json(canonical_json(evidence_payload(_evidence())))
    assert isinstance(payload, dict) and isinstance(payload["evaluation"], dict)
    payload["evaluation"][field] = replacement
    with pytest.raises(MaterializationError):
        decode_evidence(payload)


def _unchanged(before: dict[str, object], after: dict[str, object]) -> None:
    old, new = before["tables"], after["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert old[table] == new[table]


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point",
    [
        "backend_compile",
        "source_statement",
        "transfer",
        "after_rename",
        "quality",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ],
)
def test_entity_failure_rolls_back_complete_publication(
    tmp_path: Path, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    observed: list[str] = []
    local_attempts: list[bool] = []

    def local_retry(*args: object, **kwargs: object) -> None:
        local_attempts.append(True)
        raise AssertionError("Entity source failure attempted a local retry")

    monkeypatch.setattr(admission, "supervise", local_retry)

    def fault(event: str) -> None:
        if event == point:
            observed.append(event)
            raise RuntimeError("private-entity-error-canary")

    runtime._hook = fault
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        entity_metric(sources).discover.entity_outliers().execute()
    assert "private-entity-error-canary" not in str(error.value)
    assert observed == [point]
    assert local_attempts == []
    _unchanged(before, snapshot(runtime))


@pytest.mark.runtime
@pytest.mark.parametrize("point", ["source_statement", "insert_findings"])
def test_entity_cancellation_keeps_no_partial_authority(tmp_path: Path, point: str) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    runtime.target = EngineTarget("warehouse")

    def cancel(event: str) -> None:
        if event == point:
            raise KeyboardInterrupt()

    runtime._hook = cancel
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        entity_metric(sources).discover.entity_outliers().execute()
    _unchanged(before, snapshot(runtime))


@pytest.mark.runtime
@pytest.mark.parametrize("damage", ["evaluation", "native_proof", "identity_signature"])
def test_cold_entity_candidate_rejects_corrupt_authority(tmp_path: Path, damage: str) -> None:
    runtime, sources, database = setup_entity_candidate(tmp_path)
    runtime.target = EngineTarget("warehouse")
    result = entity_metric(sources).discover.entity_outliers().execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    payload = descriptor_payload(record.descriptor)
    if damage == "evaluation":
        evidence = payload["candidate_evidence"]
        assert isinstance(evidence, dict) and isinstance(evidence["evaluation"], dict)
        evidence["evaluation"]["non_null_value_count"] = 2
    elif damage == "native_proof":
        population = payload["population_authority"]
        assert isinstance(population, dict)
        population["validation_results"] = []
    else:
        population = payload["population_authority"]
        assert isinstance(population, dict)
        population["identity_signature"] = [["id", "string"]]
    with sqlite3.connect(runtime.store.db_path) as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), result.state.artifact_ref.ref),
        )
    database.rename(tmp_path / "origin.offline")
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(result.state.artifact_ref)
    assert snapshot(runtime) == before


@pytest.mark.runtime
def test_entity_publication_never_calls_generic_candidate_row_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Entity publication collected rows for generic validation")

    monkeypatch.setattr(candidate_publication, "validate_rows", forbidden)
    result = entity_metric(sources).discover.entity_outliers().execute()
    result.show()
    shown = capsys.readouterr().out
    assert "<identity>" in shown
    assert "non_null_values=4" in shown and "center=1.0" in shown
    assert result.findings().items == ()
    assert result.to_pandas().entity_identity.tolist() == [(4,)]


@pytest.mark.runtime
def test_engine_metric_checkpoint_scores_without_origin_and_cold_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, database = setup_entity_candidate(tmp_path)
    runtime.target = EngineTarget("warehouse")
    checkpoint = entity_metric(sources).execute()
    database.rename(tmp_path / "origin.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Entity engine checkpoint reached origin or local computation")

    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    monkeypatch.setattr(admission, "supervise", forbidden)
    monkeypatch.setattr(candidate_publication, "validate_rows", forbidden)
    result = checkpoint.discover.entity_outliers().execute()
    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(4,)] and frame.score.tolist() == [4.0]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    assert record.descriptor.candidate_evidence.definition.input_state_kind == "materialized"
    assert (
        record.descriptor.candidate_evidence.definition.input_authority
        == checkpoint.state.artifact_ref.ref
    )
    assert runtime.statistics.source_fences == 1
    assert runtime.statistics.transferred_rows == 0 and runtime.statistics.worker_pid is None
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    retained = cold.artifact(checkpoint.state.artifact_ref)
    assert isinstance(retained, MaterializedMetricDataset)
    before = snapshot(cold)
    monkeypatch.setattr(admission, "place", forbidden)
    assert (
        retained.discover.entity_outliers().execute().state.artifact_ref
        == result.state.artifact_ref
    )
    assert snapshot(cold) == before and not cold.statistics.statements
