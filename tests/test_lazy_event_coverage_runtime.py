"""Native provider coverage is exact, atomic, and absent from retained reuse."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis import time_scope
from marivo.analysis.domains.completeness import (
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    EventCoverageRequestV1,
    SourceOriginCoverageStartV1,
)
from marivo.analysis.event import every_start, first_per_subject, sequence, step
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import encode_descriptor
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.event_codec import EventEvidenceSummary
from marivo.refs import ref
from marivo.semantic.event import participant_role
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_event_runtime_fixtures import (
    END,
    OCCURRENCE_CANARY,
    START,
    THROUGH,
    journey,
    setup_event,
)

pytestmark = pytest.mark.runtime


def _receipt(request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
    return EventCoverageReceiptV1(
        event_ref=request.event_ref,
        event_fingerprint=request.event_fingerprint,
        source_entity_ref=request.source_entity_ref,
        source_origin_ref=request.source_origin_ref,
        occurred_at_ref=request.occurred_at_ref,
        coverage_start=BoundedCoverageStartV1(complete_from=request.required_from),
        complete_through=request.required_through,
        authority="fixture.watermark@v1",
        observed_at=THROUGH + timedelta(hours=1),
        source_revision="fixture.revision@v1",
        source_binding_fingerprint=request.source_binding_fingerprint,
        execution_domain_id=request.execution_domain_id,
    )


@dataclass
class _Provider:
    mode: Literal["complete", "mixed", "unknown", "short_end", "late_start", "origin"] = "complete"
    requests: list[EventCoverageRequestV1] = field(default_factory=list)

    def __call__(
        self, backend: Backend, request: EventCoverageRequestV1
    ) -> EventCoverageReceiptV1 | None:
        assert isinstance(backend, Backend)
        assert request.source_binding_fingerprint
        assert request.execution_domain_id
        assert request.required_from == START
        assert request.required_through == THROUGH
        self.requests.append(request)
        if self.mode == "unknown" or (
            self.mode == "mixed" and request.event_ref.path == "sales.finished"
        ):
            return None
        receipt = _receipt(request)
        if self.mode == "short_end":
            return replace(receipt, complete_through=THROUGH - timedelta(microseconds=1))
        if self.mode == "late_start":
            return replace(
                receipt,
                coverage_start=BoundedCoverageStartV1(
                    complete_from=START + timedelta(microseconds=1)
                ),
            )
        if self.mode == "origin":
            return replace(
                receipt,
                coverage_start=SourceOriginCoverageStartV1(
                    source_origin_ref=request.source_origin_ref
                ),
            )
        return receipt


def _summary(runtime: DatasetRuntime, artifact_ref: str) -> EventEvidenceSummary:
    record = runtime.store.artifact(artifact_ref)
    assert record is not None
    result = record.descriptor.event_evidence
    assert isinstance(result, EventEvidenceSummary)
    return result


def test_provider_reads_each_distinct_event_once_per_new_action(tmp_path: Path) -> None:
    provider = _Provider()
    runtime, sources, _ = setup_event(tmp_path, provider=provider)
    started, finished = ref.event("sales.started"), ref.event("sales.finished")
    pattern = sequence(
        step(participant=participant_role(event=started, name="buyer"), key="first_start"),
        step(participant=participant_role(event=started, name="buyer"), key="second_start"),
        step(participant=participant_role(event=finished, name="buyer"), key="finish"),
    )
    logical = sources.events.match(
        pattern,
        cohort_window=time_scope(start=START, end=END),
        completion_through=THROUGH,
        matching=first_per_subject(),
    )
    assert provider.requests == []
    result = logical.execute()
    rows = result.to_pandas()
    assert Counter(item.event_ref.path for item in provider.requests) == {
        "sales.started": 1,
        "sales.finished": 1,
    }
    assert rows.completion_status.tolist() == ["complete"] * 3 + ["incomplete"] * 3
    assert _summary(runtime, result.state.artifact_ref.ref).coverage.basis == "observed"
    assert {request.source_entity_ref for request in provider.requests} == {
        "sales.started_rows",
        "sales.finished_rows",
    }
    assert len({request.execution_domain_id for request in provider.requests}) == 1
    assert len({request.source_binding_fingerprint for request in provider.requests}) == 1
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert len(provider.requests) == 2
    second = journey(
        sources, complete=False, matching=every_start(completion_assignment="shared")
    ).execute()
    assert second.state.artifact_ref != result.state.artifact_ref
    assert Counter(item.event_ref.path for item in provider.requests) == {
        "sales.started": 2,
        "sales.finished": 2,
    }


@pytest.mark.parametrize("mode", ["complete", "origin", "mixed"])
def test_observed_and_mixed_authority_publish_complete_followup(
    tmp_path: Path, mode: Literal["complete", "origin", "mixed"]
) -> None:
    provider = _Provider(mode=mode)
    runtime, sources, _ = setup_event(tmp_path, provider=provider)
    result = journey(sources, complete=mode == "mixed").execute()
    rows = result.to_pandas()
    assert rows.completion_status.tolist() == ["complete", "complete", "incomplete", "incomplete"]
    summary = _summary(runtime, result.state.artifact_ref.ref)
    assert summary.complete_journey_count == 1
    assert summary.incomplete_journey_count == 1
    assert summary.censored_journey_count == 0
    assert summary.coverage.complete
    assert summary.coverage.basis == ("mixed" if mode == "mixed" else "observed")
    facts = {item.event_ref: item for item in summary.coverage.events}
    assert facts["event:sales.started"].basis == "observed"
    assert facts["event:sales.finished"].basis == ("declared" if mode == "mixed" else "observed")
    assert len(provider.requests) == 2
    if mode == "origin":
        assert all(item.complete_from is None for item in facts.values())
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert str(OCCURRENCE_CANARY) not in encode_descriptor(record.descriptor)
    assert result.findings().items == ()


@pytest.mark.parametrize("mode", ["unknown", "short_end", "late_start"])
def test_missing_or_insufficient_provider_receipt_preserves_censoring(
    tmp_path: Path, mode: Literal["unknown", "short_end", "late_start"]
) -> None:
    provider = _Provider(mode=mode)
    runtime, sources, _ = setup_event(tmp_path, provider=provider)
    result = journey(sources, complete=False).execute()
    rows = result.to_pandas()
    assert rows.completion_status.tolist() == [
        "complete",
        "complete",
        "coverage_censored",
        "coverage_censored",
    ]
    summary = _summary(runtime, result.state.artifact_ref.ref)
    assert summary.complete_journey_count == 1
    assert summary.incomplete_journey_count == 0
    assert summary.censored_journey_count == 1
    assert not summary.coverage.complete and summary.coverage.basis == "unknown"
    assert all(not item.complete for item in summary.coverage.events)
    assert {item.basis for item in summary.coverage.events} == {
        "unknown" if mode == "unknown" else "observed"
    }
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    "corruption",
    [
        "event",
        "fingerprint",
        "binding",
        "domain",
        "source",
        "axis",
        "origin",
        "invalid_return",
        "provider_exception",
    ],
)
def test_malformed_provider_authority_fails_safely_and_atomically(
    tmp_path: Path, corruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EventCoverageRequestV1] = []
    canary = f"private_occurrence_{OCCURRENCE_CANARY}"

    def broken(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1 | None:
        requests.append(request)
        receipt = _receipt(request)
        if corruption == "event":
            return replace(receipt, event_ref=ref.event("sales." + canary))
        if corruption == "fingerprint":
            return replace(receipt, event_fingerprint=canary)
        if corruption == "binding":
            return replace(receipt, source_binding_fingerprint=canary)
        if corruption == "domain":
            return replace(receipt, execution_domain_id=canary)
        if corruption == "source":
            return replace(receipt, source_entity_ref=canary)
        if corruption == "axis":
            return replace(receipt, occurred_at_ref=canary)
        if corruption == "origin":
            return replace(
                receipt,
                coverage_start=SourceOriginCoverageStartV1(
                    source_origin_ref=ref.datasource(canary)
                ),
            )
        raise RuntimeError(canary)

    runtime, sources, _ = setup_event(tmp_path, provider=broken)
    if corruption == "invalid_return":

        def invalid(backend: Backend, request: EventCoverageRequestV1) -> dict[str, str]:
            requests.append(request)
            return {"event_identity": canary}

        monkeypatch.setattr(runtime, "event_coverage_provider", invalid)
    with pytest.raises(MaterializationError) as caught:
        journey(sources, complete=False).execute()
    assert len(requests) == 1
    assert canary not in str(caught.value)
    assert str(OCCURRENCE_CANARY) not in str(caught.value)
    assert runtime.statistics.primary_queries == 0
    assert runtime.statistics.validation_queries == 0
    assert snapshot(runtime)["dataset_artifacts"] == 0
    assert snapshot(runtime)["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    session_directory = runtime.store.layout.session_dir(runtime.session_ref)
    assert not list(session_directory.rglob("*.parquet"))
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        retained = "\n".join(db.iterdump())
    assert canary not in retained
    assert str(OCCURRENCE_CANARY) not in retained


@pytest.mark.parametrize("blocked_request", [1, 2])
def test_provider_query_deadline_interrupts_and_allows_clean_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_request: int
) -> None:
    requests: list[EventCoverageRequestV1] = []
    completed_queries: list[int] = []

    def slow(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        requests.append(request)
        if len(requests) == blocked_request:
            backend.raw_sql("SELECT sum(sqrt(i)) FROM range(1000000000) AS t(i)").fetchone()
            completed_queries.append(len(requests))
        return _receipt(request)

    runtime, sources, _ = setup_event(tmp_path, provider=slow)
    logical = journey(sources, complete=False)
    with monkeypatch.context() as deadline:
        deadline.setattr(admission, "_SOURCE_EXECUTION_DEADLINE_SECONDS", 0.02)
        with pytest.raises(MaterializationError):
            logical.execute()
    assert len(requests) == blocked_request
    assert completed_queries == []
    assert runtime.statistics.primary_queries == 0
    assert runtime.statistics.validation_queries == 0
    failed = snapshot(runtime)
    assert failed["analysis_action_runs"] == 1
    assert failed["analysis_action_run_terminals"] == 1
    assert failed["dataset_artifacts"] == 0
    assert failed["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))

    monkeypatch.setattr(runtime, "event_coverage_provider", _Provider())
    assert len(logical.execute().to_pandas()) == 4
    retried = snapshot(runtime)
    assert retried["analysis_action_runs"] == 2
    assert retried["analysis_action_run_terminals"] == 2
    assert retried["dataset_artifacts"] == 1
    assert retried["dataset_evidence"] == 1
    assert runtime.store.resources(runtime.session_ref) == ()


def test_cold_recovery_and_exact_rebuilt_binding_do_not_read_provider(tmp_path: Path) -> None:
    provider = _Provider()
    runtime, sources, database = setup_event(tmp_path, provider=provider)
    logical = journey(sources, complete=False)
    result = logical.execute()
    rows = result.to_pandas()
    assert len(provider.requests) == 2
    database.unlink()
    forbidden_calls: list[EventCoverageRequestV1] = []

    def forbidden(
        backend: Backend, request: EventCoverageRequestV1
    ) -> EventCoverageReceiptV1 | None:
        forbidden_calls.append(request)
        raise AssertionError("retained Event reuse must not request source coverage")

    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, event_coverage_provider=forbidden)
    recovered = cold.artifact(result.state.artifact_ref)
    assert recovered.to_pandas().equals(rows)
    assert recovered.evidence_digest == result.evidence_digest
    rebuilt_sources = cold.sources(
        semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar
    )
    rebuilt = journey(rebuilt_sources, complete=False)
    assert rebuilt.definition_fingerprint == logical.definition_fingerprint
    reused = rebuilt.execute()
    assert reused.state.artifact_ref == result.state.artifact_ref
    assert reused.evidence_digest == result.evidence_digest
    assert forbidden_calls == []
    assert snapshot(cold) == before
    assert cold.statistics.primary_queries == 0
    assert cold.statistics.validation_queries == 0


def test_declared_coverage_preserves_insufficient_observation_after_cold_recovery(
    tmp_path: Path,
) -> None:
    provider = _Provider(mode="short_end")
    runtime, sources, database = setup_event(tmp_path, provider=provider)
    logical = journey(sources, complete=True)
    result = logical.execute()
    rows = result.to_pandas()
    assert rows.completion_status.tolist() == ["complete", "complete", "incomplete", "incomplete"]
    summary = _summary(runtime, result.state.artifact_ref.ref)
    assert summary.coverage.complete and summary.coverage.basis == "declared"
    for fact in summary.coverage.events:
        observed = fact.supplemented_observation
        assert observed is not None
        assert observed.complete_from == START.isoformat()
        assert observed.complete_through == (THROUGH - timedelta(microseconds=1)).isoformat()
        assert observed.authority == "fixture.watermark@v1"
        assert observed.observed_at == (THROUGH + timedelta(hours=1)).isoformat()
        assert observed.source_revision == "fixture.revision@v1"
        assert fact.complete_through == THROUGH.isoformat()
        assert fact.rationale and fact.authority is None
    database.unlink()
    calls: list[EventCoverageRequestV1] = []

    def forbidden(
        backend: Backend, request: EventCoverageRequestV1
    ) -> EventCoverageReceiptV1 | None:
        calls.append(request)
        raise AssertionError("cold supplemented coverage must remain retained")

    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, event_coverage_provider=forbidden)
    recovered = cold.artifact(result.state.artifact_ref)
    assert recovered.to_pandas().equals(rows)
    assert _summary(cold, recovered.state.artifact_ref.ref) == summary
    assert recovered.evidence_digest == result.evidence_digest
    cold_sources = cold.sources(
        semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar
    )
    assert (
        journey(cold_sources, complete=True).execute().state.artifact_ref
        == result.state.artifact_ref
    )
    assert calls == [] and len(provider.requests) == 2
    assert snapshot(cold) == before
