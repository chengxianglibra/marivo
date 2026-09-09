"""Independent Candidate publication corruption and original Evidence checks."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pyarrow as pa
import pytest

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
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.operators.candidate_contracts import (
    CandidateEvaluationSummary,
    CandidateObjective,
    CandidatePayload,
    CandidateSpecV1,
)
from marivo.analysis.operators.candidate_values import candidate_item_id
from tests.lazy_forecast_fixtures import history
from tests.lazy_materialization_fixtures import descriptor as base_descriptor
from tests.lazy_observation_fixtures import make_sources

_REASONS = {
    "point_anomalies": "point_zscore_threshold_met",
    "interesting_windows": "global_zscore_run",
    "period_shifts": "delta_window_zscore_run",
}


def _value(
    objective: CandidateObjective = "point_anomalies", *, limit: int = 50
) -> tuple[
    ArtifactDescriptor, list[dict[str, object]], CandidateSpecV1, CandidateEvaluationSummary
]:
    metric = history(make_sources(session_id="session"))
    if objective == "point_anomalies":
        logical = metric.discover.point_anomalies(threshold=0.5, limit=limit)
    elif objective == "interesting_windows":
        logical = metric.discover.interesting_windows(threshold=1.0, limit=limit)
    else:
        logical = metric.compare(metric).discover.period_shifts(threshold=1.0, limit=limit)
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, CandidatePayload)
    spec = logical._root.payload.spec
    # The rows and original evaluation are authored independently of the scorer.
    # Four observations [0, 0, 0, 4] have population mean 1 and deviation sqrt(3).
    sigma = math.sqrt(3.0)
    peak = 3.0 / sigma
    records: list[dict[str, object]] = []
    if objective == "point_anomalies":
        for day, observed in ((4, 4.0), (1, 0.0), (2, 0.0), (3, 0.0)):
            deviation = observed - 1.0
            records.append(
                {
                    "score": abs(deviation) / sigma,
                    "time_coordinate": date(2026, 2, day),
                    "observed_value": observed,
                    "baseline_value": 1.0,
                    "signed_deviation": deviation,
                    "direction": "high" if deviation > 0 else "low",
                }
            )
    else:
        record: dict[str, object] = {
            "score": peak,
            "window_start": date(2026, 2, 4),
            "window_end": date(2026, 2, 4),
            "baseline_start": date(2026, 2, 1),
            "baseline_end": date(2026, 2, 4),
            "peak_absolute_zscore": peak,
            "direction": "high",
        }
        record["point_count" if objective == "interesting_windows" else "window_size"] = (
            1 if objective == "interesting_windows" else 7
        )
        records.append(record)
    for record in records:
        record["reason_codes"] = (_REASONS[objective],)
        record["item_id"] = candidate_item_id(spec.definition, spec.output_row, record)
    summary = CandidateEvaluationSummary(
        input_row_count=4 if objective != "period_shifts" else 10,
        series_count=1,
        evaluated_series_count=1,
        insufficient_series_count=0,
        constant_series_count=0,
        unavailable_series_count=0,
        searched_unit_count=4,
        evaluated_unit_count=4,
        pre_limit_candidate_count=len(records),
        emitted_candidate_count=min(limit, len(records)),
        score_range=(1.0 / sigma, peak) if objective == "point_anomalies" else (peak, peak),
        reason_counts=((_REASONS[objective], len(records)),),
        baseline_mean_range=(1.0, 1.0),
        baseline_stddev_range=(sigma, sigma),
        window_size_range=(7, 7) if objective == "period_shifts" else None,
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
        realized_row_count=len(records),
    )
    descriptor = make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(receipt, (), realized, len(records)),
        (("dataset.final_row_key_unique", 0),),
    )
    return descriptor, records, spec, summary


def _published(
    objective: CandidateObjective = "point_anomalies", *, limit: int = 50
) -> tuple[ArtifactDescriptor, list[dict[str, object]]]:
    descriptor, records, spec, summary = _value(objective, limit=limit)
    published, findings = build_candidate_publication(
        descriptor,
        pa.Table.from_pylist(records).to_batches(max_chunksize=1),
        artifact_ref="artifact",
        session_ref="session",
        definition=spec.definition,
        evaluation=summary,
    )
    assert findings == ()
    return published, records


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_closed_candidate_evidence_round_trip_without_source(
    objective: CandidateObjective, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor, _ = _published(objective)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold Candidate validation must not replay its source or scorer")

    monkeypatch.setattr("marivo.analysis.operators.candidate_values.execute_candidate", forbidden)
    monkeypatch.setattr("marivo.semantic.validator.Registry.__init__", forbidden)
    encoded = encode_descriptor(descriptor)
    recovered = decode_descriptor(encoded)
    assert encode_descriptor(recovered) == encoded
    assert recovered.candidate_evidence == descriptor.candidate_evidence
    assert recovered.candidate_evidence is not None
    assert recovered.candidate_evidence.emitted_finding_count == 0
    assert recovered.candidate_evidence.finding_set_digest == finding_set_digest(())
    assert "observed_value" not in canonical_json(evidence_payload(recovered.candidate_evidence))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("evaluated_series_count", 0),
        ("series_count", 2),
        ("evaluated_unit_count", 5),
        ("pre_limit_candidate_count", 5),
        ("emitted_candidate_count", 3),
        ("score_range", [0.1, 2.0]),
        ("baseline_stddev_range", [0.0, 0.0]),
        ("reason_counts", [["unregistered_reason", 4]]),
        ("window_size_range", [7, 7]),
    ],
)
def test_forged_original_evaluation_is_rejected(field: str, replacement: object) -> None:
    descriptor, _ = _published()
    payload = parse_json(canonical_json(evidence_payload(descriptor.candidate_evidence)))
    assert isinstance(payload, dict) and isinstance(payload["evaluation"], dict)
    payload["evaluation"][field] = replacement
    with pytest.raises(IntegrityError):
        decode_evidence(payload)


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize(
    "field", ["threshold", "score_range", "baseline_mean_range", "baseline_stddev_range"]
)
def test_huge_numeric_metadata_raises_typed_integrity_error(field: str, sign: int) -> None:
    descriptor, _ = _published()
    payload = parse_json(canonical_json(evidence_payload(descriptor.candidate_evidence)))
    assert isinstance(payload, dict)
    target = payload["definition" if field == "threshold" else "evaluation"]
    assert isinstance(target, dict)
    number = sign * 10**400
    target[field] = number if field == "threshold" else [number, number]
    with pytest.raises(IntegrityError, match="finite number"):
        decode_evidence(payload)


@pytest.mark.parametrize("change", ["findings", "digest", "current_count", "objective"])
def test_metadata_cannot_promote_leads_or_contradict_current_output(change: str) -> None:
    descriptor, _ = _published()
    evidence = descriptor.candidate_evidence
    assert evidence is not None
    changed = (
        replace(evidence, emitted_finding_count=1)
        if change == "findings"
        else replace(evidence, finding_set_digest="0" * 64)
        if change == "digest"
        else replace(evidence, row_count=3)
        if change == "current_count"
        else replace(
            evidence,
            definition=replace(
                evidence.definition,
                objective="interesting_windows",
                method_id="global_zscore_runs@v1",
            ),
        )
    )
    with pytest.raises(IntegrityError):
        validate_descriptor(replace(descriptor, candidate_evidence=changed))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("item_id", "sha256:" + "a" * 64),
        ("reason_codes", "point_zscore_threshold_met"),
        ("reason_codes", ("global_zscore_run",)),
        ("score", float("inf")),
        ("score", 0.25),
        ("score", 1),
        ("signed_deviation", 2.0),
        ("baseline_value", 2.0),
        ("direction", "low"),
        ("time_coordinate", date(2026, 2, 10)),
    ],
)
def test_forged_point_rows_are_rejected(field: str, replacement: object) -> None:
    descriptor, records = _published()
    forged = dict(records[0])
    forged[field] = replacement
    with pytest.raises(IntegrityError):
        validate_row(descriptor, forged)


@pytest.mark.parametrize("objective", ["interesting_windows", "period_shifts"])
@pytest.mark.parametrize("field", ["window_start", "baseline_end", "peak_absolute_zscore"])
def test_window_endpoint_and_peak_corruption_is_rejected(
    objective: CandidateObjective, field: str
) -> None:
    descriptor, records = _published(objective)
    forged = dict(records[0])
    forged[field] = (
        99.0
        if field == "peak_absolute_zscore"
        else date(2026, 2, 10)
        if field == "window_start"
        else date(2026, 1, 1)
    )
    assert descriptor.candidate_evidence is not None
    forged["item_id"] = candidate_item_id(
        descriptor.candidate_evidence.definition, descriptor.row_contract, forged
    )
    with pytest.raises(IntegrityError):
        validate_row(descriptor, forged)


def test_digest_binds_original_authority_after_selection() -> None:
    descriptor, records = _published()
    evidence = descriptor.candidate_evidence
    assert evidence is not None
    changed = replace(
        descriptor,
        candidate_evidence=replace(
            evidence, definition=replace(evidence.definition, input_authority="f" * 64)
        ),
    )
    with pytest.raises(IntegrityError, match="digest"):
        validate_row(changed, records[0])


def test_recomputed_digest_does_not_authorize_changed_time_scalar_type() -> None:
    descriptor, records = _published()
    evidence = descriptor.candidate_evidence
    assert evidence is not None
    forged = dict(records[0])
    forged["time_coordinate"] = "2026-02-04"
    forged["item_id"] = candidate_item_id(evidence.definition, descriptor.row_contract, forged)
    with pytest.raises(IntegrityError, match="scalar type"):
        validate_row(descriptor, forged)


def test_publication_checks_order_duplicates_and_complete_scan() -> None:
    descriptor, records = _published()
    for changed in (list(reversed(records)), [records[0]] * 4, records[:-1]):
        with pytest.raises(IntegrityError):
            validate_rows(descriptor, pa.Table.from_pylist(changed).to_batches(max_chunksize=1))


def test_limited_producer_must_keep_maximum_but_selection_can_omit_it() -> None:
    descriptor, records = _published(limit=2)
    evidence = descriptor.candidate_evidence
    assert evidence is not None and evidence.evaluation.pre_limit_candidate_count == 4
    lower = []
    for day in (2, 3):
        record = dict(records[1])
        record["time_coordinate"] = date(2026, 2, day)
        record["item_id"] = candidate_item_id(evidence.definition, descriptor.row_contract, record)
        lower.append(record)
    batches = pa.Table.from_pylist(lower).to_batches(max_chunksize=1)
    with pytest.raises(IntegrityError, match="strongest original score"):
        validate_rows(descriptor, batches)
    selected = replace(
        descriptor,
        dataset_materialization_contract=replace(
            descriptor.dataset_materialization_contract, producer_id="dataset.where"
        ),
    )
    validate_rows(selected, batches)


def test_original_limit_and_empty_selection_preserve_evaluation() -> None:
    descriptor, records = _published(limit=2)
    original = descriptor.candidate_evidence
    assert original is not None and original.evaluation.pre_limit_candidate_count == 4
    assert original.row_count == original.evaluation.emitted_candidate_count == 2
    selected = replace(
        descriptor,
        dataset_materialization_contract=replace(
            descriptor.dataset_materialization_contract, producer_id="dataset.where"
        ),
        storage_receipt=replace(descriptor.storage_receipt, realized_row_count=0),
        candidate_evidence=replace(original, row_count=0),
    )
    validate_rows(selected, ())
    assert selected.candidate_evidence is not None
    assert selected.candidate_evidence.definition is original.definition
    assert selected.candidate_evidence.evaluation is original.evaluation
    assert len(records) == 2


def test_evaluated_empty_is_distinct_from_no_evaluation() -> None:
    descriptor, _ = _published()
    evidence = descriptor.candidate_evidence
    assert evidence is not None
    empty = CandidateEvidenceSummary(
        0,
        0,
        finding_set_digest(()),
        replace(evidence.definition, threshold=10.0),
        replace(
            evidence.evaluation,
            pre_limit_candidate_count=0,
            emitted_candidate_count=0,
            score_range=None,
            reason_counts=(("point_zscore_threshold_met", 0),),
        ),
    )
    validate_evidence(empty)
    damaged = deepcopy(empty)
    assert isinstance(damaged.evaluation, CandidateEvaluationSummary)
    damaged = replace(
        damaged,
        evaluation=replace(damaged.evaluation, evaluated_series_count=0, constant_series_count=1),
    )
    with pytest.raises(IntegrityError):
        validate_evidence(damaged)
