"""Atomic complete selection, native identity boundaries and retained result reads."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.domains.completeness import (
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    EventCoverageRequestV1,
)
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.event_reducer_codec import (
    EventFunnelEvidenceSummary,
    EventTimeToEventEvidenceSummary,
)
from marivo.analysis.observation.predicates import eq, gte
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_fixtures import START, THROUGH, journey, setup_event
from tests.lazy_event_runtime_worker import assert_identity_private

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("downstream", ("sample", "metric"))
def test_unknown_selection_cannot_disappear_through_downstream_action(
    tmp_path: Path, downstream: str
) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    input = journey(sources, complete=False)
    meaning = input.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    selection = input.select_subjects(dropped_before(step=meaning.pattern.steps[-1]))
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as caught:
        if downstream == "sample":
            selection.sample(engine_sample(target_rows=1)).execute()
        else:
            sources.observe(ref.metric("sales.revenue"), population=selection).execute()
    assert "981730041" not in str(caught.value)
    after = snapshot(runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime)


def test_proven_empty_selection_publishes_complete_population(tmp_path: Path) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute(
            "INSERT INTO finished_rows VALUES (981740040, 2, TIMESTAMP '2026-02-02 00:00:00')"
        )
    input = journey(sources)
    meaning = input.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    result = input.select_subjects(dropped_before(step=meaning.pattern.steps[-1])).execute()
    assert result.to_pandas().empty
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.subject_selection_evidence is not None
    summary = record.descriptor.subject_selection_evidence
    assert summary.input_subject_count == 2
    assert summary.selected_subject_count == summary.unknown_subject_count == summary.row_count == 0
    assert record.evidence.finding_count == 0
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("shape", ("funnel", "duration", "selection"))
@pytest.mark.parametrize("failure", ("before_commit", "cancel"))
def test_retained_reducer_failure_cancellation_has_no_partial_authority(
    tmp_path: Path, shape: str, failure: str
) -> None:
    armed = False

    def fail(point: str) -> None:
        if armed and point == ("quality" if failure == "cancel" else failure):
            if failure == "cancel":
                raise KeyboardInterrupt("reducer-private-cancellation-canary")
            raise OSError("reducer-private-storage-canary")

    runtime, sources, _ = setup_event(tmp_path, engine=True, event=fail)
    receiver = journey(sources).execute()
    meaning = receiver.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    first, last = meaning.pattern.steps
    before = snapshot(runtime)
    armed = True
    with pytest.raises(KeyboardInterrupt) as caught:
        if shape == "funnel":
            receiver.funnel().execute()
        elif shape == "duration":
            receiver.time_to_event(from_step=first, to_step=last).execute()
        else:
            receiver.select_subjects(dropped_before(step=last)).execute()
    assert "canary" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    after = snapshot(runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert after["analysis_action_run_terminals"] == before["analysis_action_run_terminals"] + 1
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime)


@pytest.mark.parametrize("sampled", (False, True))
def test_local_result_filter_reads_only_retained_columns_after_source_removal(
    tmp_path: Path,
    sampled: bool,
) -> None:
    runtime, sources, database = setup_event(tmp_path)
    population = sources.population(ref.entity("sales.customers"))
    if sampled:
        population = population.sample(engine_sample(target_rows=100, seed=42))
    input = journey(sources, population=population)
    meaning = input.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    first, last = meaning.pattern.steps
    funnel = input.funnel().execute()
    assert funnel.to_pandas().step_key.tolist() == ["start", "finish"]
    attempts = input.time_to_event(from_step=first, to_step=last).execute()
    expected = attempts.to_pandas()
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered_funnel = cold.artifact(funnel.state.artifact_ref)
    recovered_attempts = cold.artifact(attempts.state.artifact_ref)
    assert isinstance(recovered_funnel, MaterializedEventDataset)
    assert isinstance(recovered_attempts, MaterializedEventDataset)
    assert recovered_attempts.to_pandas().equals(expected)
    with patch.object(admission, "_build_backend_from_effective", forbidden):
        complete_funnel = recovered_funnel.where(
            gte(recovered_funnel.fields.get("reached_count"), 0)
        ).execute()
        loss = recovered_funnel.where(
            eq(recovered_funnel.fields.get("step_key"), "finish")
        ).execute()
        incomplete = recovered_attempts.where(
            eq(recovered_attempts.fields.get("completion_status"), "incomplete")
        ).execute()
        empty_attempts = recovered_attempts.where(
            eq(recovered_attempts.fields.get("completion_status"), "entry_unknown")
        ).execute()
    assert loss.to_pandas().lost_count.tolist() == [1]
    assert complete_funnel.to_pandas().step_key.tolist() == ["start", "finish"]
    assert incomplete.to_pandas().entity_identity.tolist() == [(2,)]
    assert empty_attempts.to_pandas().empty
    if sampled:
        record = cold.store.artifact(incomplete.state.artifact_ref.ref)
        assert record is not None and record.descriptor.sampling_execution is not None
        assert tuple(part.role for part in record.descriptor.retained_parts) == (
            "population_sampling_state",
        )
    assert cold.store.resources(cold.session_ref) == ()


def test_high_cardinality_reducers_keep_all_identity_relations_native(tmp_path: Path) -> None:
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
        patch.object(admission, "execute_local", forbidden),
        patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
    ):
        receiver = journey(sources).execute()
        meaning = receiver.row_contract.family_semantics
        assert isinstance(meaning, EventJourneySemantics)
        first, last = meaning.pattern.steps
        funnel = receiver.funnel().execute()
        durations = receiver.time_to_event(from_step=first, to_step=last).execute()
        selected = receiver.select_subjects(dropped_before(step=last)).execute()
    records = [
        runtime.store.artifact(result.state.artifact_ref.ref)
        for result in (funnel, durations, selected)
    ]
    assert all(record is not None for record in records)
    funnel_record, duration_record, selected_record = records
    assert funnel_record is not None and duration_record is not None and selected_record is not None
    funnel_summary = funnel_record.descriptor.event_evidence
    duration_summary = duration_record.descriptor.event_evidence
    selection_summary = selected_record.descriptor.subject_selection_evidence
    assert isinstance(funnel_summary, EventFunnelEvidenceSummary)
    assert (funnel_summary.row_count, funnel_summary.cohort_count) == (2, 5000)
    assert isinstance(duration_summary, EventTimeToEventEvidenceSummary)
    assert duration_summary.row_count == duration_summary.incomplete_count == 5000
    assert selection_summary is not None
    assert selection_summary.row_count == selection_summary.selected_subject_count == 5000
    assert runtime.statistics.transferred_rows == 5000
    assert runtime.statistics.transferred_bytes > 0
    assert (
        runtime.statistics.events.get("local_execution_started", 0) == 0
        and runtime.statistics.local_handoffs == ()
    )
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime, ("881730041", "981730041"))


def test_retained_partial_receipt_proves_attempt_after_coverage_start(tmp_path: Path) -> None:
    requests: list[str] = []

    def provider(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        del backend
        requests.append(request.event_ref.key)
        start = START + timedelta(hours=6) if request.event_ref.path == "sales.finished" else START
        return EventCoverageReceiptV1(
            event_ref=request.event_ref,
            event_fingerprint=request.event_fingerprint,
            source_entity_ref=request.source_entity_ref,
            source_origin_ref=request.source_origin_ref,
            occurred_at_ref=request.occurred_at_ref,
            coverage_start=BoundedCoverageStartV1(complete_from=start),
            complete_through=THROUGH,
            authority="fixture.partial@v1",
            observed_at=THROUGH,
            source_binding_fingerprint=request.source_binding_fingerprint,
            execution_domain_id=request.execution_domain_id,
        )

    runtime, sources, database = setup_event(tmp_path, engine=True, provider=provider)
    produced = journey(sources, complete=False).execute()
    assert len(requests) == 2
    assert produced.to_pandas().completion_status.tolist() == [
        "complete",
        "complete",
        "coverage_censored",
        "coverage_censored",
    ]
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DROP TABLE started_rows")
        connection.execute("DROP TABLE finished_rows")
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    receiver = cold.artifact(produced.state.artifact_ref)
    assert isinstance(receiver, MaterializedEventDataset)
    meaning = receiver.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    first, last = meaning.pattern.steps
    with patch("marivo.analysis.compiler.lowering.resolve_event_coverage", forbidden):
        attempts = receiver.time_to_event(from_step=first, to_step=last).execute()
        selected = receiver.select_subjects(dropped_before(step=last)).execute()
    assert attempts.to_pandas().completion_status.tolist() == ["complete", "incomplete"]
    assert selected.to_pandas().entity_identity.tolist() == [(2,)]
    assert len(requests) == 2
    assert cold.statistics.transferred_rows == 1
