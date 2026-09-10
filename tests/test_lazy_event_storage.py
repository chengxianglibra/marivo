"""Independent Event storage, bounded cross-batch ordering and cold authority checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    EventCoverageResolution,
    EventObservedWatermarkV1,
    declarations_json,
    resolve_event_coverage,
)
from marivo.analysis.domains.contracts import EventJourneySemantics, EventPayload
from marivo.analysis.domains.event import LogicalEventDataset
from marivo.analysis.event import every_start, sequence, step
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    LocalReceipt,
    canonical_json,
    decode_descriptor,
    encode_descriptor,
    evidence_for,
    parse_json,
    schema_fingerprint,
)
from marivo.analysis.materialization.engine import ordered_relation
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.event_codec import (
    EventEvidenceSummary,
    decode_evidence,
    evidence_payload,
    summary_from_proof,
)
from marivo.analysis.materialization.event_publication import (
    EventRowValidator,
    bind_event_summary,
    build_event_publication,
)
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.recovery import recover_dataset
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    ReadPolicy,
    _read,
    _realized_schema,
    _to_dataframe,
    write_local_dataset,
)
from marivo.analysis.observation.contracts import make_ids
from marivo.refs import ref
from marivo.semantic.event import ParticipantRoleHandle
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_materialization_fixtures import descriptor as base_descriptor
from tests.lazy_observation_fixtures import NoIoActionPort

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CANARY = 982734651


def _value() -> tuple[LogicalEventDataset, ArtifactDescriptor, pa.Table, EventEvidenceSummary]:
    pattern = sequence(
        step(participant=ParticipantRoleHandle(ref.event("sales.started"), "buyer"), key="z_start"),
        step(
            participant=ParticipantRoleHandle(ref.event("sales.finished"), "buyer"), key="a_finish"
        ),
    )
    logical = make_event_sources().events.match(
        pattern,
        cohort_window=mv.time_scope(start=_START, end=_START + timedelta(days=1)),
        completion_through=_START + timedelta(days=2),
        matching=every_start(completion_assignment="shared"),
    )
    root = logical._root
    assert isinstance(root, LogicalRootHandle) and isinstance(root.payload, EventPayload)
    records: list[dict[str, object]] = []
    # Journey ids deliberately sort opposite their governed anchor order.
    for suffix, subject, anchor_hour, occurrence in (
        ("f", _CANARY, 1, 71),
        ("e", _CANARY, 2, 72),
        ("d", _CANARY + 1, 1, 73),
    ):
        for ordinal in range(2):
            present = ordinal == 0 or subject == _CANARY
            hour = anchor_hour if ordinal == 0 else 3
            records.append(
                {
                    "journey_id": "journey_" + suffix * 64,
                    "completion_status": "complete" if subject == _CANARY else "coverage_censored",
                    "entity_identity": {"id": subject},
                    "step_key": "z_start" if ordinal == 0 else "a_finish",
                    "event_identity": {"k0": occurrence if ordinal == 0 else 80}
                    if present
                    else None,
                    "occurred_at": _START + timedelta(hours=hour) if present else None,
                    "elapsed_from_start": (hour - anchor_hour) * 3_600_000_000 if present else None,
                    "elapsed_from_previous": (hour - anchor_hour) * 3_600_000_000
                    if present
                    else None,
                }
            )
    arrow_schema = pa.schema(
        [
            ("journey_id", pa.string()),
            ("completion_status", pa.string()),
            ("entity_identity", pa.struct([("id", pa.int64())])),
            ("step_key", pa.string()),
            ("event_identity", pa.struct([("k0", pa.int64())])),
            ("occurred_at", pa.timestamp("us", tz="UTC")),
            ("elapsed_from_start", pa.int64()),
            ("elapsed_from_previous", pa.int64()),
        ]
    )
    table = pa.Table.from_pylist(records, schema=arrow_schema)
    realized = _realized_schema(logical.schema, arrow_schema)
    receipt = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=6,
    )
    descriptor = make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(receipt, (), realized, 6),
        (("dataset.final_row_key_unique", 0),),
    )
    summary = EventEvidenceSummary(
        6, 3, 2, 5, 1, 2, 0, 1, resolve_event_coverage(root.payload.definition)
    )
    return logical, bind_event_summary(descriptor, summary), table, summary


def test_event_native_summary_codec_is_closed_and_contains_no_identity() -> None:
    _, descriptor, table, summary = _value()
    restored = decode_descriptor(encode_descriptor(descriptor))
    assert restored.event_evidence == summary
    assert decode_evidence(evidence_payload(summary)) == summary
    assert str(_CANARY) not in encode_descriptor(descriptor)
    assert all(
        value not in encode_descriptor(descriptor)
        for value in table.column("journey_id").to_pylist()
    )
    evidence = evidence_for(descriptor)
    assert evidence.finding_count == 0
    assert isinstance(descriptor.storage_receipt, LocalReceipt)
    changed_receipt = replace(descriptor.storage_receipt, bytes_hash="b" * 64)
    assert evidence_for(replace(descriptor, storage_receipt=changed_receipt)) == evidence
    proof = {
        name: getattr(summary, name)
        for name in (
            "row_count",
            "journey_count",
            "subject_count",
            "matched_row_count",
            "missing_row_count",
            "complete_journey_count",
            "incomplete_journey_count",
            "censored_journey_count",
        )
    }
    assert summary_from_proof({**proof, "violations": 0}, summary.coverage) == summary
    with pytest.raises(IntegrityError, match="native output proof failed"):
        summary_from_proof({**proof, "violations": 0, "identities": [_CANARY]}, summary.coverage)
    with pytest.raises(IntegrityError, match="native output proof failed"):
        summary_from_proof({**proof, "violations": 1}, summary.coverage)


def test_event_dense_stream_roundtrips_across_every_batch_boundary(tmp_path: Path) -> None:
    logical, descriptor, table, summary = _value()
    published, findings = build_event_publication(descriptor, table.to_batches(max_chunksize=1))
    assert published.event_evidence == summary and findings == ()
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
    recovered = decode_descriptor(
        encode_descriptor(
            replace(
                descriptor,
                storage_receipt=written.primary_receipt,
                realized_schema=written.realized_schema,
            )
        )
    )
    result = _read(
        project_root=tmp_path,
        receipt=written.primary_receipt,
        row_contract=recovered.row_contract,
        row_set_contract=recovered.row_set_contract,
        policy=ReadPolicy(),
        preview=False,
    )
    assert result.to_pylist() == table.to_pylist()
    frame = _to_dataframe(result, recovered.row_contract)
    assert frame.entity_identity.tolist() == [
        (_CANARY,),
        (_CANARY,),
        (_CANARY,),
        (_CANARY,),
        (_CANARY + 1,),
        (_CANARY + 1,),
    ]
    assert frame.event_identity.iloc[-1] is None
    assert frame.event_identity.iloc[0] == (71,)
    assert frame.elapsed_from_start.iloc[1] == pd.Timedelta(hours=2)
    assert pd.isna(frame.elapsed_from_start.iloc[-1])


def test_event_engine_order_uses_current_initial_step_rows_after_cold_decode() -> None:
    _, descriptor, table, _ = _value()
    restored = decode_descriptor(encode_descriptor(descriptor))
    backend = ibis.duckdb.connect()
    try:
        unordered = backend.create_table("retained", table.take(pa.array([5, 2, 0, 4, 3, 1])))
        ordered = ordered_relation(unordered, restored.row_contract, restored.row_set_contract)
        assert backend.to_pyarrow(ordered).to_pylist() == table.to_pylist()
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "mutation",
    [
        "step_order",
        "missing_step",
        "anchor_order",
        "null_component",
        "elapsed",
        "status",
        "after_gap",
    ],
)
def test_event_corrupt_stream_is_rejected_without_identity_diagnostics(mutation: str) -> None:
    _, descriptor, table, _ = _value()
    records = table.to_pylist()
    if mutation == "step_order":
        records[0], records[1] = records[1], records[0]
    elif mutation == "missing_step":
        records.pop()
    elif mutation == "anchor_order":
        records = records[2:4] + records[:2] + records[4:]
    elif mutation == "null_component":
        records[0]["event_identity"] = {"k0": None}
    elif mutation == "elapsed":
        records[1]["elapsed_from_start"] = 1
    elif mutation == "status":
        records[-1]["completion_status"] = "incomplete"
    else:
        records[1]["event_identity"] = None
        records[1]["occurred_at"] = None
        records[1]["elapsed_from_start"] = None
        records[1]["elapsed_from_previous"] = None
    with pytest.raises(IntegrityError) as caught:
        build_event_publication(
            descriptor,
            pa.Table.from_pylist(records, schema=table.schema).to_batches(max_chunksize=1),
        )
    assert str(_CANARY) not in str(caught.value)
    assert "journey_" + "f" * 64 not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("journey_count", 4),
        ("subject_count", 4),
        ("matched_row_count", 4),
        ("censored_journey_count", 0),
    ],
)
def test_event_cold_descriptor_rejects_summary_contradictions(field: str, value: int) -> None:
    _, descriptor, _, _ = _value()
    payload = parse_json(encode_descriptor(descriptor))
    assert isinstance(payload, dict) and isinstance(payload["event_evidence"], dict)
    payload["event_evidence"][field] = value
    with pytest.raises(IntegrityError):
        decode_descriptor(canonical_json(payload))


def test_event_cold_descriptor_rejects_unbound_coverage_and_time_authority() -> None:
    _, descriptor, _, summary = _value()
    fact = replace(summary.coverage.events[0], event_ref="event:sales.unrelated")
    forged = replace(
        summary, coverage=replace(summary.coverage, events=(fact, *summary.coverage.events[1:]))
    )
    with pytest.raises(IntegrityError, match="exact retained source authority"):
        bind_event_summary(descriptor, forged)
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    invalid_time = replace(
        semantics, _token=d._CORE_TOKEN, completion_through=semantics.cohort_start
    )
    corrupt_row = replace(
        descriptor.row_contract, _token=d._CORE_TOKEN, family_semantics=invalid_time
    )
    with pytest.raises(IntegrityError):
        decode_descriptor(encode_descriptor(replace(descriptor, row_contract=corrupt_row)))


def test_event_stream_validator_retains_only_one_journey() -> None:
    _, descriptor, table, _ = _value()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    validator = EventRowValidator(semantics)
    for batch in table.to_batches(max_chunksize=1):
        validator.accept(batch)
        assert len(validator.occurrences) <= len(semantics.pattern.steps)
    validator.finish()
    assert validator.journey_count == 3


def _declared_value(
    *, supplemented: bool = False
) -> tuple[ArtifactDescriptor, EventEvidenceSummary]:
    _, descriptor, _, summary = _value()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    declaration = BoundedCompletenessDeclarationV1(
        inputs=tuple(step.event for step in semantics.pattern.steps),
        complete_from=_START,
        complete_through=_START + timedelta(days=2),
        rationale="The complete source extraction covers both inputs.",
    )
    declared_semantics = replace(
        semantics, _token=d._CORE_TOKEN, completeness_json=declarations_json((declaration,))
    )
    declared_row = replace(
        descriptor.row_contract, _token=d._CORE_TOKEN, family_semantics=declared_semantics
    )
    facts = tuple(
        replace(
            fact,
            basis="declared",
            complete=True,
            complete_from=declaration.complete_from.isoformat(),
            complete_through=declaration.complete_through.isoformat(),
            rationale=declaration.rationale,
        )
        for fact in summary.coverage.events
    )
    if supplemented:
        watermark = EventObservedWatermarkV1(
            _START.isoformat(),
            (_START + timedelta(days=1)).isoformat(),
            "fixture.partial_extract@v1",
            (_START + timedelta(days=3)).isoformat(),
            "extract-revision-1",
        )
        facts = (
            replace(
                facts[0],
                supplemented_observation=watermark,
                source_binding_fingerprint="a" * 64,
                execution_domain_id="b" * 64,
            ),
            *facts[1:],
        )
    declared_summary = replace(
        summary,
        incomplete_journey_count=1,
        censored_journey_count=0,
        coverage=EventCoverageResolution(True, "declared", facts),
    )
    declared = bind_event_summary(replace(descriptor, row_contract=declared_row), declared_summary)
    return declared, declared_summary


def test_event_declared_coverage_preserves_exact_authored_bounds_and_rationale() -> None:
    declared, declared_summary = _declared_value()
    facts = declared_summary.coverage.events
    assert decode_descriptor(encode_descriptor(declared)).event_evidence == declared_summary
    forged_fact = replace(facts[0], rationale="A different unretained assertion.")
    forged = replace(
        declared_summary,
        coverage=replace(declared_summary.coverage, events=(forged_fact, *facts[1:])),
    )
    with pytest.raises(IntegrityError, match="exact retained assumption"):
        bind_event_summary(declared, forged)


@pytest.mark.parametrize("basis", ["unknown", "partial_observed", "complete_observed"])
def test_event_cold_coverage_respects_complete_retained_declarations(basis: str) -> None:
    declared, declared_summary = _declared_value()
    _, _, _, original = _value()
    summary = declared_summary if basis == "complete_observed" else original
    if basis != "unknown":
        complete = basis == "complete_observed"
        facts = tuple(
            replace(
                fact,
                basis="observed",
                complete=complete,
                complete_from=_START.isoformat(),
                complete_through=(_START + timedelta(days=2 if complete else 1)).isoformat(),
                authority="fixture.complete_extract@v1",
                observed_at=(_START + timedelta(days=3)).isoformat(),
                rationale=None,
                source_binding_fingerprint="a" * 64,
                execution_domain_id="b" * 64,
            )
            for fact in summary.coverage.events
        )
        summary = replace(
            summary,
            coverage=EventCoverageResolution(
                complete, "observed" if complete else "unknown", facts
            ),
        )
    encoded = encode_descriptor(replace(declared, event_evidence=summary))
    if basis == "complete_observed":
        assert decode_descriptor(encoded).event_evidence == summary
    else:
        with pytest.raises(IntegrityError, match="complete retained declaration"):
            decode_descriptor(encoded)


@pytest.mark.parametrize("basis", ["unknown", "declared"])
@pytest.mark.parametrize("field", ["source_binding_fingerprint", "execution_domain_id"])
def test_event_cold_coverage_rejects_non_hash_bindings_for_every_basis(
    basis: str, field: str
) -> None:
    if basis == "declared":
        descriptor, summary = _declared_value()
    else:
        _, descriptor, _, summary = _value()
    fact = replace(
        summary.coverage.events[0],
        source_binding_fingerprint="PRIVATE_BINDING_CANARY"
        if field == "source_binding_fingerprint"
        else "a" * 64,
        execution_domain_id="PRIVATE_DOMAIN_CANARY" if field == "execution_domain_id" else "b" * 64,
    )
    forged = replace(
        summary, coverage=replace(summary.coverage, events=(fact, *summary.coverage.events[1:]))
    )
    with pytest.raises(IntegrityError, match="SHA-256") as caught:
        decode_descriptor(encode_descriptor(replace(descriptor, event_evidence=forged)))
    assert "PRIVATE_BINDING_CANARY" not in str(caught.value)
    assert "PRIVATE_DOMAIN_CANARY" not in str(caught.value)


def test_event_supplemented_observation_roundtrips_with_decisive_declaration() -> None:
    descriptor, summary = _declared_value(supplemented=True)
    restored = decode_descriptor(encode_descriptor(descriptor))
    assert restored.event_evidence == summary
    observation = summary.coverage.events[0].supplemented_observation
    assert observation is not None
    assert observation.complete_through == (_START + timedelta(days=1)).isoformat()
    assert summary.coverage.events[0].complete_through == (_START + timedelta(days=2)).isoformat()
    assert summary.coverage.basis == "declared"


@pytest.mark.parametrize(
    "mutation", ["sufficient", "reversed", "naive", "unbound", "wrong_basis", "unknown_member"]
)
def test_event_supplemented_observation_rejects_corrupt_or_unnecessary_authority(
    mutation: str,
) -> None:
    descriptor, _ = _declared_value(supplemented=True)
    value = parse_json(encode_descriptor(descriptor))
    assert isinstance(value, dict) and isinstance(value["event_evidence"], dict)
    coverage = value["event_evidence"]["coverage"]
    assert isinstance(coverage, dict) and isinstance(coverage["events"], list)
    fact = coverage["events"][0]
    assert isinstance(fact, dict) and isinstance(fact["supplemented_observation"], dict)
    observation = fact["supplemented_observation"]
    if mutation == "sufficient":
        observation["complete_through"] = (_START + timedelta(days=2)).isoformat()
    elif mutation == "reversed":
        observation["complete_from"] = (_START + timedelta(days=2)).isoformat()
    elif mutation == "naive":
        observation["observed_at"] = "2026-01-03T00:00:00"
    elif mutation == "unbound":
        fact["source_binding_fingerprint"] = ""
    elif mutation == "wrong_basis":
        fact["basis"] = "observed"
    else:
        observation["identity"] = "private-identity-canary"
    with pytest.raises(IntegrityError) as caught:
        decode_descriptor(canonical_json(value))
    assert "private-identity-canary" not in str(caught.value)


def test_event_observed_coverage_requires_bound_provider_authority_and_interval() -> None:
    _, descriptor, _, summary = _value()
    facts = tuple(
        replace(
            fact,
            basis="observed",
            complete=True,
            complete_from=_START.isoformat(),
            complete_through=(_START + timedelta(days=2)).isoformat(),
            authority="fixture.complete_extract@v1",
            observed_at=(_START + timedelta(days=3)).isoformat(),
            source_binding_fingerprint="a" * 64,
            execution_domain_id="b" * 64,
        )
        for fact in summary.coverage.events
    )
    observed_summary = replace(
        summary,
        incomplete_journey_count=1,
        censored_journey_count=0,
        coverage=EventCoverageResolution(True, "observed", facts),
    )
    observed = bind_event_summary(descriptor, observed_summary)
    assert decode_descriptor(encode_descriptor(observed)).event_evidence == observed_summary
    for forged_fact in (
        replace(facts[0], source_binding_fingerprint=""),
        replace(facts[0], complete_through=(_START + timedelta(days=1)).isoformat()),
    ):
        forged = replace(
            observed_summary,
            coverage=replace(observed_summary.coverage, events=(forged_fact, *facts[1:])),
        )
        with pytest.raises(IntegrityError):
            bind_event_summary(descriptor, forged)
    partial_facts = tuple(
        replace(fact, complete=False, complete_through=(_START + timedelta(days=1)).isoformat())
        for fact in facts
    )
    partial = replace(summary, coverage=EventCoverageResolution(False, "unknown", partial_facts))
    assert bind_event_summary(descriptor, partial).event_evidence == partial


@pytest.mark.parametrize(
    "column,type_id",
    [
        ("event_identity", "renamed_component"),
        ("occurred_at", "naive_timestamp"),
        ("elapsed_from_start", "floating_duration"),
    ],
)
def test_event_stream_rejects_unsupported_physical_identity_time_and_duration(
    column: str, type_id: str
) -> None:
    _, descriptor, table, _ = _value()
    if type_id == "renamed_component":
        values = [
            {"unowned": item["k0"]} if item is not None else None
            for item in table.column(column).to_pylist()
        ]
        replacement = pa.array(values, type=pa.struct([("unowned", pa.int64())]))
    elif type_id == "naive_timestamp":
        replacement = table.column(column).cast(pa.timestamp("us"))
    else:
        replacement = table.column(column).cast(pa.float64())
    table = table.set_column(table.schema.get_field_index(column), column, replacement)
    with pytest.raises(IntegrityError):
        build_event_publication(descriptor, table.to_batches(max_chunksize=1))


def test_event_empty_journeys_preserve_typed_schema_and_zero_finding_authority() -> None:
    _, descriptor, table, summary = _value()
    empty = replace(
        summary,
        row_count=0,
        journey_count=0,
        subject_count=0,
        matched_row_count=0,
        missing_row_count=0,
        complete_journey_count=0,
        incomplete_journey_count=0,
        censored_journey_count=0,
    )
    descriptor = replace(
        descriptor, storage_receipt=replace(descriptor.storage_receipt, realized_row_count=0)
    )
    descriptor, findings = build_event_publication(
        descriptor, table.slice(0, 0).to_batches(), summary=empty
    )
    restored = decode_descriptor(encode_descriptor(descriptor))
    assert restored.event_evidence == empty and findings == ()


def test_event_terminal_duration_preserves_microseconds_beyond_nanosecond_range() -> None:
    _, descriptor, table, _ = _value()
    duration = 400 * 365 * 86_400_000_000
    values = pa.array([0, duration, 0, duration, 0, None], type=pa.int64())
    table = table.set_column(
        table.schema.get_field_index("elapsed_from_start"), "elapsed_from_start", values
    )
    frame = _to_dataframe(table, descriptor.row_contract)
    assert frame.elapsed_from_start.dtype == pd.ArrowDtype(pa.duration("us"))
    assert frame.elapsed_from_start.iloc[1].to_pytimedelta() == timedelta(days=400 * 365)
    assert pd.isna(frame.elapsed_from_start.iloc[-1])


def test_event_composite_occurrence_identity_preserves_decimal_precision_and_tuple_shape(
    tmp_path: Path,
) -> None:
    _, descriptor, table, _ = _value()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    semantics = replace(
        semantics, _token=d._CORE_TOKEN, occurrence_identity_types=("decimal(12, 2)", "string")
    )
    rows = table.to_pylist()
    values = [
        {"k0": Decimal(str(row["event_identity"]["k0"])) / 100, "k1": "same_source"}
        if row["event_identity"] is not None
        else None
        for row in rows
    ]
    column = pa.array(values, type=pa.struct([("k0", pa.decimal128(12, 2)), ("k1", pa.string())]))
    table = table.set_column(
        table.schema.get_field_index("event_identity"), "event_identity", column
    )
    row_contract = replace(
        descriptor.row_contract, _token=d._CORE_TOKEN, family_semantics=semantics
    )
    realized = _realized_schema(row_contract.schema, table.schema)
    descriptor = replace(
        descriptor,
        row_contract=row_contract,
        realized_schema=realized,
        storage_receipt=replace(
            descriptor.storage_receipt, schema_fingerprint=schema_fingerprint(realized)
        ),
    )
    restored = decode_descriptor(encode_descriptor(descriptor))
    build_event_publication(restored, table.to_batches(max_chunksize=1))
    written = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "composite-staging",
        final_path=tmp_path / "composite-retained",
        batches=table.to_batches(max_chunksize=1),
        row_contract=restored.row_contract,
        row_set_contract=restored.row_set_contract,
        source_key_validation=True,
        event=lambda _: None,
    )
    retained = _read(
        project_root=tmp_path,
        receipt=written.primary_receipt,
        row_contract=restored.row_contract,
        row_set_contract=restored.row_set_contract,
        policy=ReadPolicy(),
        preview=False,
    )
    frame = _to_dataframe(retained, restored.row_contract)
    assert frame.event_identity.iloc[0] == (Decimal("0.71"), "same_source")
    wrong_type = pa.array(
        values, type=pa.struct([("k0", pa.decimal128(13, 2)), ("k1", pa.string())])
    )
    with pytest.raises(IntegrityError, match="occurrence identity schema"):
        build_event_publication(
            restored,
            table.set_column(
                table.schema.get_field_index("event_identity"), "event_identity", wrong_type
            ).to_batches(),
        )


def test_event_composite_decimal_subject_is_recovered_without_catalog(tmp_path: Path) -> None:
    _, descriptor, table, _ = _value()
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    signature = (("tenant", "string"), ("id", "decimal"))
    semantics = replace(semantics, _token=d._CORE_TOKEN, subject_identity_signature=signature)
    subject = descriptor.row_contract.schema.columns[2]
    subject = replace(
        subject,
        _token=d._CORE_TOKEN,
        identity=d._entity_identity(
            ref.entity(semantics.subject_entity_ref), signature, ids=make_ids(())
        ),
    )
    schema = d._make_schema(
        tuple(
            subject if field.name == "entity_identity" else field
            for field in descriptor.row_contract.schema.columns
        )
    )
    row_contract = replace(
        descriptor.row_contract, _token=d._CORE_TOKEN, schema=schema, family_semantics=semantics
    )
    identities = pa.array(
        [
            {"tenant": "tenant-canary", "id": Decimal(item["id"]) / 100}
            for item in table.column("entity_identity").to_pylist()
        ],
        type=pa.struct([("tenant", pa.string()), ("id", pa.decimal128(12, 2))]),
    )
    table = table.set_column(
        table.schema.get_field_index("entity_identity"), "entity_identity", identities
    )
    written = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "subject-staging",
        final_path=tmp_path / "subject-retained",
        batches=table.to_batches(max_chunksize=1),
        row_contract=row_contract,
        row_set_contract=descriptor.row_set_contract,
        source_key_validation=True,
        event=lambda _: None,
    )
    descriptor = replace(
        descriptor,
        row_contract=row_contract,
        realized_schema=written.realized_schema,
        population_authority=replace(descriptor.population_authority, identity_signature=signature),
        storage_receipt=written.primary_receipt,
    )
    restored = decode_descriptor(encode_descriptor(descriptor))
    record = ArtifactRecord(
        "artifact-composite",
        "session-event",
        "a" * 64,
        restored,
        _START.isoformat(),
        "run-composite",
        evidence_for(restored),
    )
    recovered = recover_dataset(
        record, session_ref="session-event", store_id="store-event", action_port=NoIoActionPort()
    )
    assert recovered.row_contract.family_semantics == semantics
    retained = _read(
        project_root=tmp_path,
        receipt=written.primary_receipt,
        row_contract=recovered.row_contract,
        row_set_contract=recovered.row_set_contract,
        policy=ReadPolicy(),
        preview=False,
    )
    frame = _to_dataframe(retained, recovered.row_contract)
    assert frame.entity_identity.iloc[0] == ("tenant-canary", Decimal("9827346.51"))
    assert "tenant-canary" not in encode_descriptor(restored)
