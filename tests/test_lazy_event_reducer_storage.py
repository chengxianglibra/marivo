"""Independent reducer persistence, canonical order and closed authority tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.ordering import ordered_relation
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import EventCoverageResolution, resolve_event_coverage
from marivo.analysis.domains.contracts import (
    EventFunnelSemantics,
    EventPayload,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.event import LogicalEventDataset
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    SamplingRealization,
    canonical_json,
    decode_descriptor,
    encode_descriptor,
    evidence_for,
    parse_json,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.event_publication import bind_event_summary
from marivo.analysis.materialization.event_reducer_codec import (
    EventFunnelEvidenceSummary,
    EventTimeToEventEvidenceSummary,
    decode_evidence,
    evidence_payload,
    selection_summary_from_proof,
    summary_from_proof,
)
from marivo.analysis.materialization.event_reducer_publication import (
    EventReducerRowValidator,
    bind_selection_summary,
    summary_from_batches,
)
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.recovery import recover_dataset
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    ReadPolicy,
    _read,
    _realized_schema,
    write_local_dataset,
)
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from marivo.semantic.event import ParticipantRoleHandle
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_materialization_fixtures import descriptor as base_descriptor
from tests.lazy_observation_fixtures import NoIoActionPort

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CANARY = 928374657


def _journey() -> tuple[LogicalEventDataset, EventCoverageResolution]:
    pattern = sequence(
        step(participant=ParticipantRoleHandle(ref.event("sales.started"), "buyer"), key="z_start"),
        step(
            participant=ParticipantRoleHandle(ref.event("sales.finished"), "buyer"), key="a_finish"
        ),
    )
    value = make_event_sources().events.match(
        pattern,
        cohort_window=mv.time_scope(start=_START, end=_START + timedelta(days=1)),
        completion_through=_START + timedelta(days=2),
        matching=first_per_subject(),
    )
    assert isinstance(value._root, LogicalRootHandle) and isinstance(
        value._root.payload, EventPayload
    )
    return value, resolve_event_coverage(value._root.payload.definition)


def _descriptor(logical: LogicalDataset, table: pa.Table) -> ArtifactDescriptor:
    realized = _realized_schema(logical.row_contract, table.schema)
    receipt = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=table.num_rows,
    )
    return make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(receipt, (), realized, table.num_rows),
        (("dataset.final_row_key_unique", 0),),
    )


def _funnel() -> tuple[LogicalEventDataset, ArtifactDescriptor, pa.Table]:
    journey, coverage = _journey()
    logical = journey.funnel()
    schema = pa.schema(
        [
            (
                field.name,
                pa.string()
                if field.name == "step_key"
                else pa.float64()
                if "rate" in field.name or "conversion" in field.name
                else pa.int64(),
            )
            for field in logical.schema.columns
        ]
    )
    table = pa.Table.from_pylist(
        [
            dict(zip(schema.names, ("z_start", 3, 3, 3, 3, 3, 0, 1.0, None, None, 0), strict=True)),
            dict(zip(schema.names, ("a_finish", 3, 1, 3, 1, 1, 0, 1.0, 1.0, 0.0, 2), strict=True)),
        ],
        schema=schema,
    )
    descriptor = bind_event_summary(
        _descriptor(logical, table), EventFunnelEvidenceSummary(2, 1, 3, coverage)
    )
    return logical, descriptor, table


def _tte() -> tuple[LogicalEventDataset, ArtifactDescriptor, pa.Table]:
    journey, coverage = _journey()
    semantics = journey.row_contract.family_semantics
    assert isinstance(journey._root, LogicalRootHandle) and isinstance(
        journey._root.payload, EventPayload
    )
    pattern = journey._root.payload.definition.pattern
    logical = journey.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    schema = pa.schema(
        [
            ("journey_id", pa.string()),
            ("entity_identity", pa.struct([("id", pa.int64())])),
            ("from_event_identity", pa.struct([("k0", pa.int64())])),
            ("from_time", pa.timestamp("us", tz="UTC")),
            ("to_event_identity", pa.struct([("k0", pa.int64())])),
            ("to_time", pa.timestamp("us", tz="UTC")),
            ("duration", pa.int64()),
            ("followup_until", pa.timestamp("us", tz="UTC")),
            ("observed_duration", pa.int64()),
            ("completion_status", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(
        [
            dict(
                zip(
                    schema.names,
                    (
                        "journey_" + "f" * 64,
                        {"id": _CANARY},
                        {"k0": 90},
                        _START,
                        {"k0": 91},
                        _START + timedelta(hours=1),
                        3_600_000_000,
                        _START + timedelta(hours=1),
                        3_600_000_000,
                        "complete",
                    ),
                    strict=True,
                )
            ),
            dict(
                zip(
                    schema.names,
                    (
                        "journey_" + "a" * 64,
                        {"id": _CANARY + 1},
                        {"k0": 92},
                        _START,
                        None,
                        None,
                        None,
                        _START,
                        0,
                        "coverage_censored",
                    ),
                    strict=True,
                )
            ),
        ],
        schema=schema,
    )
    assert semantics is not None
    summary = EventTimeToEventEvidenceSummary(2, 2, 1, 0, 1, 0, 0, coverage)
    return logical, bind_event_summary(_descriptor(logical, table), summary), table


@pytest.mark.parametrize("shape", ["funnel", "tte"])
def test_reducer_metadata_and_local_rows_roundtrip_without_retained_parts(
    tmp_path: Path, shape: str
) -> None:
    logical, descriptor, table = _funnel() if shape == "funnel" else _tte()
    restored = decode_descriptor(encode_descriptor(descriptor))
    assert restored.event_evidence == descriptor.event_evidence
    assert evidence_for(restored).finding_count == 0
    assert str(_CANARY) not in encode_descriptor(restored)
    assert "journey_" + "f" * 64 not in encode_descriptor(restored)
    written = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "staging",
        final_path=tmp_path / "retained",
        batches=table.to_batches(max_chunksize=1),
        row_contract=logical.row_contract,
        row_set_contract=logical.row_set_contract,
        source_key_validation=True,
        event=lambda _: None,
    )
    assert written.retained_parts == ()
    result = _read(
        project_root=tmp_path,
        receipt=written.primary_receipt,
        row_contract=restored.row_contract,
        row_set_contract=restored.row_set_contract,
        policy=ReadPolicy(),
        preview=False,
    )
    assert result.to_pylist() == table.to_pylist()
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table("retained", table.take(pa.array([1, 0])))
        ordered = ordered_relation(source, restored.row_contract, restored.row_set_contract)
        assert backend.to_pyarrow(ordered).to_pylist() == table.to_pylist()
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "shape,field,value",
    [
        ("funnel", "resolved_entry_count", 2),
        ("funnel", "conversion_from_first", 0.5),
        ("funnel", "step_key", "foreign"),
        ("tte", "duration", 12),
        ("tte", "completion_status", "completed"),
        ("tte", "followup_until", _START),
    ],
)
def test_reducer_rows_reject_inconsistent_cells_without_identity_diagnostics(
    shape: str, field: str, value: object
) -> None:
    _, descriptor, table = _funnel() if shape == "funnel" else _tte()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics))
    records = table.to_pylist()
    records[0][field] = value
    with pytest.raises(IntegrityError) as caught:
        EventReducerRowValidator(semantics).accept(
            pa.Table.from_pylist(records, schema=table.schema).to_batches()[0]
        )
    assert str(_CANARY) not in str(caught.value)


def test_reducer_cold_metadata_rejects_counts_coverage_and_exact_step_tampering() -> None:
    _, descriptor, _ = _tte()
    for section, field, value in (
        ("event_evidence", "complete_count", 2),
        ("event_evidence", "not_entered_count", 1),
        ("event_evidence", "extra_identity", [_CANARY]),
    ):
        raw = parse_json(encode_descriptor(descriptor))
        assert isinstance(raw, dict) and isinstance(raw[section], dict)
        raw[section][field] = value
        with pytest.raises(IntegrityError):
            decode_descriptor(canonical_json(raw))
    raw = parse_json(encode_descriptor(descriptor))
    assert isinstance(raw, dict) and isinstance(raw["row_contract"], dict)
    row = raw["row_contract"]
    semantics = row["family_semantics"]
    assert isinstance(semantics, dict) and isinstance(semantics["to_step"], dict)
    semantics["to_step"]["key"] = "foreign"
    with pytest.raises(IntegrityError):
        decode_descriptor(canonical_json(raw))


def test_filtered_result_summary_is_fresh_and_proof_schema_is_closed() -> None:
    _, descriptor, table = _funnel()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventFunnelSemantics) and isinstance(
        descriptor.event_evidence, EventFunnelEvidenceSummary
    )
    original = descriptor.event_evidence
    assert summary_from_batches(
        semantics, table.slice(1).to_batches(), original.coverage
    ) == replace(original, row_count=1)
    assert summary_from_batches(semantics, (), original.coverage) == replace(
        original, row_count=0, group_count=0, cohort_count=0
    )
    proof = {"row_count": 2, "group_count": 1, "cohort_count": 3, "violations": 0}
    assert summary_from_proof("event/funnel@v1", proof, original.coverage) == original
    assert decode_evidence(evidence_payload(original)) == original
    with pytest.raises(IntegrityError):
        summary_from_proof("event/funnel@v1", {**proof, "identities": [_CANARY]}, original.coverage)


def test_funnel_int64_counts_keep_native_double_rate_arithmetic() -> None:
    _, descriptor, table = _funnel()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventFunnelSemantics)
    cohort = 2**53 + 1
    record = table.to_pylist()[1]
    record.update(
        cohort_count=cohort,
        resolved_cohort_count=cohort,
        entry_count=cohort,
        resolved_entry_count=cohort,
        reached_count=1,
        lost_count=cohort - 1,
        coverage_censored_count=0,
        conversion_from_first=1.0 / float(cohort),
        conversion_from_previous=1.0 / float(cohort),
        loss_rate_from_previous=float(cohort - 1) / float(cohort),
    )
    batch = pa.Table.from_pylist([record], schema=table.schema).to_batches()[0]
    EventReducerRowValidator(semantics).accept(batch)


def test_empty_time_to_event_rows_still_validate_occurrence_identity_schema() -> None:
    _, descriptor, table = _tte()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventTimeToEventSemantics)
    schema = table.schema.set(
        table.schema.get_field_index("from_event_identity"),
        pa.field("from_event_identity", pa.struct([("foreign", pa.int64())])),
    )
    batch = pa.RecordBatch.from_arrays(
        [pa.array([], type=field.type) for field in schema], schema=schema
    )
    with pytest.raises(IntegrityError, match="governed signature"):
        EventReducerRowValidator(semantics).accept(batch)


def test_selection_owns_complete_membership_and_rejects_uncertain_proof() -> None:
    journey, coverage = _journey()
    assert isinstance(journey._root, LogicalRootHandle) and isinstance(
        journey._root.payload, EventPayload
    )
    pattern = journey._root.payload.definition.pattern
    logical = journey.select_subjects(dropped_before(step=pattern.steps[1]))
    table = pa.Table.from_pylist(
        [], schema=pa.schema([("entity_identity", pa.struct([("id", pa.int64())]))])
    )
    descriptor = _descriptor(logical, table)
    descriptor = replace(
        descriptor,
        population_authority=replace(
            descriptor.population_authority,
            definition_fingerprint=descriptor.definition_fingerprint,
        ),
    )
    payload = logical._root
    assert isinstance(payload, LogicalRootHandle)
    from marivo.analysis.domains.contracts import EventSelectionPayload

    assert isinstance(payload.payload, EventSelectionPayload)
    proof = {
        "input_subject_count": 3,
        "selected_subject_count": 0,
        "unknown_subject_count": 0,
        "violations": 0,
    }
    summary = selection_summary_from_proof(
        proof,
        coverage,
        journey=payload.payload.journey,
        step=pattern.steps[1],
        input_definition=journey.definition_fingerprint,
    )
    result = bind_selection_summary(descriptor, summary)
    assert decode_descriptor(encode_descriptor(result)).subject_selection_evidence == summary
    assert result.event_evidence is None and evidence_for(result).finding_count == 0
    recovered = recover_dataset(
        ArtifactRecord(
            "artifact-selection",
            "session-event",
            "a" * 64,
            result,
            _START.isoformat(),
            "run-selection",
            evidence_for(result),
        ),
        session_ref="session-event",
        store_id="store-event",
        action_port=NoIoActionPort(),
    )
    assert isinstance(recovered, MaterializedPopulationDataset)
    for target, inherited in ((logical, None), (recovered, result)):
        sampled = target.sample(engine_sample(target_rows=2, seed=17))
        receipt = SamplingRealization(
            0, sampled.definition_fingerprint, target.definition_fingerprint, 2, 17, 0, "a" * 64
        )
        contract = materialization_contract(sampled, inherited=inherited)
        assert "population_sampling_state" in contract.retained_private_state_contract_ids
        storage = DatasetWriteResult(result.storage_receipt, (), result.realized_schema, 0)
        published = make_descriptor(sampled, contract, storage, (), (receipt,), inherited=inherited)
        assert published.sampling_execution == (receipt,)
        assert (
            published.population_authority.definition_fingerprint == sampled.definition_fingerprint
        )
        assert (
            published.population_authority.membership_scope
            == result.population_authority.membership_scope
        )
        assert bind_selection_summary(published, summary).subject_selection_evidence == summary
        for wrong in (
            (),
            (replace(receipt, target_rows=3),),
            (
                replace(
                    receipt, target_population_definition_fingerprint=journey.definition_fingerprint
                ),
            ),
        ):
            with pytest.raises(MaterializationError):
                make_descriptor(sampled, contract, storage, (), wrong, inherited=inherited)
    with pytest.raises(IntegrityError):
        selection_summary_from_proof(
            {**proof, "unknown_subject_count": 1},
            coverage,
            journey=payload.payload.journey,
            step=pattern.steps[1],
            input_definition=journey.definition_fingerprint,
        )
    with pytest.raises(IntegrityError):
        bind_selection_summary(
            replace(
                descriptor,
                population_authority=replace(
                    descriptor.population_authority,
                    definition_fingerprint=journey.definition_fingerprint,
                ),
            ),
            summary,
        )
