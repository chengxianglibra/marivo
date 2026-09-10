"""Independent Driver Candidate Evidence and cold publication corruption checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pyarrow as pa
import pytest

from marivo._temporal import builtin_grain, time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.evidence._dataset_codec import finding_set_digest
from marivo.analysis.materialization.candidate_codec import (
    CandidateEvidenceSummary,
    decode_evidence,
    evidence_payload,
    validate_evidence,
)
from marivo.analysis.materialization.candidate_publication import (
    build_candidate_publication,
    validate_descriptor,
    validate_row,
    validate_rows,
)
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    canonical_json,
    decode_descriptor,
    encode_descriptor,
    parse_json,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.operators.candidate_values import candidate_item_id
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateEvaluationSummary,
    DriverCandidatePayload,
    DriverCandidateSpecV1,
)
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION
from tests.lazy_materialization_fixtures import descriptor as base_descriptor
from tests.lazy_observation_fixtures import make_sources


def _value(
    *, temporal: bool = False, entity: bool = False, limit: int = 50
) -> tuple[
    ArtifactDescriptor,
    list[dict[str, object]],
    DriverCandidateSpecV1,
    DriverCandidateEvaluationSummary,
]:
    sources = make_sources(session_id="session")
    metric = sources.observe(
        ref.metric("sales.revenue"),
        time_scope=time_scope(start="2026-02-01", end="2026-02-20"),
    )
    if not entity:
        metric = metric.with_dimensions(REGION, CHANNEL)
        if temporal:
            metric = metric.with_time_axis(
                ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day")
            )
        metric = metric.aggregate()
    logical = metric.compare(metric).discover.driver_axes(search_space=[REGION], limit=limit)
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, DriverCandidatePayload)
    spec = logical._root.payload.spec
    # Authored independently: four members concentrate 75% in one or two members.
    records: list[dict[str, object]] = []
    if not entity:
        for channel, members in (("app", 1), ("web", 2)):
            record: dict[str, object] = {
                "channel": channel,
                "score": 1.0 / (members + 4 / 1000.0),
                "reason_codes": ("axis_concentration",),
                "axis_ref": REGION.path,
                "axis_cardinality": 4,
                "concentration_member_count": members,
                "concentration_share": 0.75,
            }
            if temporal:
                record.update(
                    comparison_ordinal=2,
                    current_time=date(2026, 2, 4),
                    baseline_time=date(2026, 2, 2),
                )
            record["item_id"] = candidate_item_id(spec.definition, spec.output_row, record)
            records.append(record)
    count = 1 if entity else 2
    high, low = 1 / 1.004, 1 / 2.004
    evaluation = DriverCandidateEvaluationSummary(
        input_row_count=4 if entity else 8,
        scope_count=count,
        searched_axis_count=count,
        evaluated_axis_count=count,
        zero_contribution_axis_count=0,
        pre_limit_candidate_count=count,
        emitted_candidate_count=min(limit, count),
        score_range=(high, high) if entity else (low, high),
        reason_counts=(("axis_concentration", count),),
    )
    records = records[:limit]
    realized = d._make_schema(
        tuple(
            replace(
                field,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(
                    field.logical_type_id, ids=logical._registration.ids
                ),
            )
            for field in logical.schema.columns
        )
    )
    receipt = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=min(limit, count),
    )
    descriptor = make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(receipt, (), realized, min(limit, count)),
        (("dataset.final_row_key_unique", 0), ("candidate.driver_output", 0)),
    )
    return descriptor, records, spec, evaluation


def _published(
    *, temporal: bool = False, entity: bool = False, limit: int = 50
) -> tuple[ArtifactDescriptor, list[dict[str, object]]]:
    descriptor, records, spec, evaluation = _value(temporal=temporal, entity=entity, limit=limit)
    result, findings = build_candidate_publication(
        descriptor,
        None if entity else pa.Table.from_pylist(records).to_batches(max_chunksize=1),
        artifact_ref="artifact",
        session_ref="session",
        definition=spec.definition,
        evaluation=evaluation,
    )
    assert findings == ()
    return result, records


@pytest.mark.parametrize("temporal", [False, True])
def test_driver_evidence_cold_roundtrip_has_exact_scope_and_zero_findings(
    temporal: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor, records = _published(temporal=temporal)
    encoded = encode_descriptor(descriptor)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold Driver Candidate validation reached the semantic registry")

    monkeypatch.setattr("marivo.semantic.validator.Registry.__init__", forbidden)
    recovered = decode_descriptor(encoded)
    assert encode_descriptor(recovered) == encoded
    assert recovered.candidate_evidence == descriptor.candidate_evidence
    assert recovered.comparison_inputs == ()
    assert recovered.comparison_basis is not None
    assert recovered.candidate_evidence is not None
    assert recovered.candidate_evidence.finding_set_digest == finding_set_digest(())
    validate_rows(recovered, pa.Table.from_pylist(records).to_batches(max_chunksize=1))
    payload = evidence_payload(recovered.candidate_evidence)
    assert isinstance(payload, dict) and isinstance(payload["definition"], dict)
    assert "threshold" not in payload["definition"]
    assert '"app"' not in canonical_json(payload) and '"web"' not in canonical_json(payload)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("scope_count", 3),
        ("searched_axis_count", 3),
        ("evaluated_axis_count", 1),
        ("evaluated_axis_count", 0),
        ("zero_contribution_axis_count", 1),
        ("pre_limit_candidate_count", 1),
        ("emitted_candidate_count", 1),
        ("score_range", [0.0, 1.0]),
        ("reason_counts", [["causal_driver", 2]]),
        ("baseline_mean_range", [0.0, 1.0]),
    ],
)
def test_driver_cold_evaluation_rejects_contradictory_or_unknown_facts(
    field: str, replacement: object
) -> None:
    descriptor, _ = _published()
    payload = parse_json(canonical_json(evidence_payload(descriptor.candidate_evidence)))
    assert isinstance(payload, dict) and isinstance(payload["evaluation"], dict)
    payload["evaluation"][field] = replacement
    with pytest.raises(MaterializationError):
        decode_evidence(payload)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("method_id", "delta_window_zscore@v1"),
        ("search_space", []),
        ("search_space", [REGION.path, REGION.path]),
        ("fold_authority", "{}"),
        ("baseline_fold_authority", "{}"),
        ("input_authority", "changed"),
        ("metric_key", "metric:sales.order_count"),
        ("threshold", 0.5),
    ],
)
def test_driver_cold_definition_rejects_changed_authority(field: str, replacement: object) -> None:
    descriptor, _ = _published()
    payload = parse_json(canonical_json(evidence_payload(descriptor.candidate_evidence)))
    assert isinstance(payload, dict) and isinstance(payload["definition"], dict)
    payload["definition"][field] = replacement
    with pytest.raises(MaterializationError):
        decode_evidence(payload)


@pytest.mark.parametrize("field", ["scope_fields", "paired_time_fields"])
def test_driver_original_scope_cannot_be_removed_from_evidence(field: str) -> None:
    descriptor, _ = _published(temporal=True)
    evidence = descriptor.candidate_evidence
    assert evidence is not None and isinstance(evidence.definition, DriverCandidateDefinition)
    changed = (
        replace(evidence.definition, scope_fields=())
        if field == "scope_fields"
        else replace(evidence.definition, paired_time_fields=())
    )
    with pytest.raises(MaterializationError):
        validate_descriptor(
            replace(descriptor, candidate_evidence=replace(evidence, definition=changed))
        )


@pytest.mark.parametrize("field", ["role_id", "logical_type_id", "field_id"])
def test_driver_cold_scope_schema_rejects_unregistered_bindings(field: str) -> None:
    descriptor, _ = _published()
    payload = parse_json(canonical_json(evidence_payload(descriptor.candidate_evidence)))
    assert isinstance(payload, dict) and isinstance(payload["definition"], dict)
    scope = payload["definition"]["scope_fields"]
    assert isinstance(scope, dict) and isinstance(scope["columns"], list)
    column = scope["columns"][0]
    assert isinstance(column, dict)
    column[field] = "unregistered"
    with pytest.raises(MaterializationError):
        decoded = decode_evidence(payload)
        validate_descriptor(replace(descriptor, candidate_evidence=decoded))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("axis_ref", CHANNEL.path),
        ("axis_cardinality", 5),
        ("concentration_member_count", 0),
        ("concentration_member_count", True),
        ("concentration_share", 0.49),
        ("concentration_share", float("inf")),
        ("score", 0.1),
        ("reason_codes", ["causal_driver"]),
        ("channel", "changed"),
        ("item_id", "changed"),
    ],
)
def test_driver_row_requires_exact_axis_scope_and_concentration(
    field: str, replacement: object
) -> None:
    descriptor, records = _published()
    row = deepcopy(records[0])
    row[field] = replacement
    with pytest.raises(MaterializationError):
        validate_row(descriptor, row)


def test_driver_complete_order_and_keys_are_independently_validated() -> None:
    descriptor, records = _published()
    for changed in (records[::-1], [records[0], records[0]]):
        with pytest.raises(MaterializationError):
            validate_rows(descriptor, pa.Table.from_pylist(changed).to_batches(max_chunksize=1))


def test_driver_zero_evaluation_and_retained_selection_keep_original_authority() -> None:
    descriptor, _ = _published()
    evidence = descriptor.candidate_evidence
    assert evidence is not None and isinstance(
        evidence.evaluation, DriverCandidateEvaluationSummary
    )
    zero = replace(
        evidence,
        row_count=0,
        evaluation=replace(
            evidence.evaluation,
            zero_contribution_axis_count=2,
            pre_limit_candidate_count=0,
            emitted_candidate_count=0,
            score_range=None,
            reason_counts=(("axis_concentration", 0),),
        ),
    )
    validate_evidence(zero)
    assert decode_evidence(parse_json(canonical_json(evidence_payload(zero)))) == zero
    retained = replace(evidence, row_count=1)
    validate_evidence(retained)
    assert retained.definition is evidence.definition and retained.evaluation is evidence.evaluation


@pytest.mark.parametrize("input_rows", [0, 1])
def test_driver_positive_scope_axes_require_actual_contributing_rows(input_rows: int) -> None:
    descriptor, _ = _published(temporal=True)
    evidence = descriptor.candidate_evidence
    assert evidence is not None and isinstance(
        evidence.evaluation, DriverCandidateEvaluationSummary
    )
    changed = replace(evidence, evaluation=replace(evidence.evaluation, input_row_count=input_rows))
    with pytest.raises(MaterializationError):
        validate_evidence(changed)
    with pytest.raises(MaterializationError):
        validate_descriptor(replace(descriptor, candidate_evidence=changed))
    with pytest.raises(MaterializationError):
        decode_evidence(parse_json(canonical_json(evidence_payload(changed))))


def test_driver_multiple_axes_can_share_one_contributing_member_row() -> None:
    metric = (
        make_sources()
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(REGION, CHANNEL)
        .aggregate()
    )
    logical = metric.compare(metric).discover.driver_axes(search_space=[REGION, CHANNEL])
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, DriverCandidatePayload)
    definition = logical._root.payload.spec.definition
    # A single joint partition contributes nonzero Delta to both searched axes.
    score = 1.0 / 1.001
    evidence = CandidateEvidenceSummary(
        row_count=2,
        emitted_finding_count=0,
        finding_set_digest=finding_set_digest(()),
        definition=definition,
        evaluation=DriverCandidateEvaluationSummary(
            input_row_count=1,
            scope_count=1,
            searched_axis_count=2,
            evaluated_axis_count=2,
            zero_contribution_axis_count=0,
            pre_limit_candidate_count=2,
            emitted_candidate_count=2,
            score_range=(score, score),
            reason_counts=(("axis_concentration", 2),),
        ),
    )
    validate_evidence(evidence)
    assert decode_evidence(parse_json(canonical_json(evidence_payload(evidence)))) == evidence


def test_driver_empty_expanded_count_has_one_evaluated_scalar_scope() -> None:
    metric = make_sources().observe(ref.metric("sales.order_count")).aggregate()
    logical = metric.compare(metric).discover.driver_axes(search_space=[REGION])
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, DriverCandidatePayload)
    definition = logical._root.payload.spec.definition
    evidence = CandidateEvidenceSummary(
        row_count=0,
        emitted_finding_count=0,
        finding_set_digest=finding_set_digest(()),
        definition=definition,
        evaluation=DriverCandidateEvaluationSummary(
            input_row_count=0,
            scope_count=1,
            searched_axis_count=1,
            evaluated_axis_count=1,
            zero_contribution_axis_count=1,
            pre_limit_candidate_count=0,
            emitted_candidate_count=0,
            score_range=None,
            reason_counts=(("axis_concentration", 0),),
        ),
    )
    validate_evidence(evidence)
    assert decode_evidence(parse_json(canonical_json(evidence_payload(evidence)))) == evidence
    assert isinstance(evidence.evaluation, DriverCandidateEvaluationSummary)
    forged = replace(
        evidence,
        evaluation=replace(
            evidence.evaluation,
            scope_count=2,
            searched_axis_count=2,
            evaluated_axis_count=2,
            zero_contribution_axis_count=2,
        ),
    )
    with pytest.raises(MaterializationError):
        validate_evidence(forged)


def test_driver_materialized_scopes_require_retained_primary_rows() -> None:
    descriptor, _ = _published(temporal=True)
    evidence = descriptor.candidate_evidence
    assert evidence is not None and isinstance(evidence.definition, DriverCandidateDefinition)
    assert isinstance(evidence.evaluation, DriverCandidateEvaluationSummary)
    forged = replace(
        evidence,
        row_count=0,
        definition=replace(
            evidence.definition,
            input_state_kind="materialized",
            input_authority="artifact_" + "a" * 32,
        ),
        evaluation=replace(
            evidence.evaluation,
            input_row_count=1,
            zero_contribution_axis_count=2,
            pre_limit_candidate_count=0,
            emitted_candidate_count=0,
            score_range=None,
            reason_counts=(("axis_concentration", 0),),
        ),
    )
    with pytest.raises(MaterializationError):
        validate_evidence(forged)
    valid = replace(forged, evaluation=replace(forged.evaluation, input_row_count=2))
    validate_evidence(valid)


def test_driver_limit_preserves_complete_search_and_original_authority_binds_items() -> None:
    descriptor, records = _published(limit=1)
    evidence = descriptor.candidate_evidence
    assert evidence is not None and isinstance(evidence.definition, DriverCandidateDefinition)
    assert evidence.evaluation.pre_limit_candidate_count == 2
    assert evidence.evaluation.emitted_candidate_count == len(records) == 1
    changed = replace(
        evidence,
        definition=replace(evidence.definition, input_authority="ds_" + "a" * 64),
    )
    with pytest.raises(MaterializationError):
        validate_row(replace(descriptor, candidate_evidence=changed), records[0])


def test_entity_driver_requires_native_proof_and_rejects_generic_row_scanning() -> None:
    descriptor, _ = _published(entity=True)
    evidence = descriptor.candidate_evidence
    assert isinstance(evidence, CandidateEvidenceSummary)
    assert decode_evidence(parse_json(canonical_json(evidence_payload(evidence)))) == evidence
    with pytest.raises(MaterializationError):
        validate_rows(descriptor, ())
    without_proof = replace(
        descriptor,
        population_authority=replace(
            descriptor.population_authority,
            validation_results=(("dataset.final_row_key_unique", 0),),
        ),
    )
    with pytest.raises(MaterializationError):
        validate_descriptor(without_proof)
