"""Distinct allocation extends existing proof, Findings, and cold descriptor contracts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_reads import _validate
from marivo.analysis.materialization.attribution_publication import (
    AttributionSourceSummary,
    build_attribution_publication,
    finding_registration,
)
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    canonical_json,
    decode_descriptor,
    descriptor_payload,
    encode_descriptor,
    evidence_for,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from tests.lazy_distinct_fixtures import CHANNEL, DISTINCT_BUYERS, REGION, make_distinct_sources
from tests.lazy_materialization_fixtures import descriptor as base_descriptor


def _value() -> tuple[ArtifactDescriptor, pd.DataFrame, AttributionSourceSummary]:
    metric = (
        make_distinct_sources()
        .observe(DISTINCT_BUYERS)
        .with_dimensions(REGION, CHANNEL)
        .aggregate()
    )
    logical = metric.compare(metric).attribute(axes=(REGION, CHANNEL), mode="hierarchy")
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
    records = []
    for width, coordinates in (
        (1, (("a", None), ("b", None))),
        (2, (("a", "x"), ("a", "y"), ("b", "x"))),
    ):
        for rank, (region, channel) in enumerate(coordinates, 1):
            value = 1.0 / len(coordinates)
            records.append(
                {
                    "region": region,
                    "channel": channel,
                    "active_axis_mask": (True, width == 2),
                    "other_mask": (False, False),
                    "current_value": value,
                    "baseline_value": 0.0,
                    "overall_delta": 1.0,
                    "contribution": value,
                    "share_of_total_delta": value,
                    "share_of_positive_pool": value,
                    "share_of_negative_pool": None,
                    "contribution_rank": rank,
                    "status": "ok",
                }
            )
    frame = pd.DataFrame(records, columns=[field.name for field in logical.schema.columns])
    primary = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=len(frame),
    )
    descriptor = make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(primary, (), realized, len(frame)),
        (),
    )
    proof = AttributionSourceSummary(1, 2, (("ok", 5), ("zero_total_delta", 0)), 0.0, "a" * 64)
    return descriptor, frame, proof


def test_source_proof_publishes_independent_hierarchy_and_cold_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor, frame, proof = _value()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "source proof must not regroup publication rows or reload semantic sources"
        )

    monkeypatch.setattr(
        "marivo.analysis.materialization.attribution_publication._summarize_rows", forbidden
    )
    published, findings = build_attribution_publication(
        descriptor,
        iter(pa.Table.from_pandas(frame, preserve_index=False).to_batches(max_chunksize=1)),
        artifact_ref="artifact",
        session_ref="session",
        source_summary=proof,
    )
    monkeypatch.setattr("marivo.semantic.validator.Registry.__init__", forbidden)
    encoded = encode_descriptor(published)
    recovered = decode_descriptor(encoded)
    assert encode_descriptor(recovered) == encoded
    semantics = recovered.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics)
    assert semantics.method == "distinct_membership@v1"
    assert semantics.resolution_semantics == "independent" and not semantics.rollup_safe
    assert semantics.numeric_type == "float64"
    assert len(findings) == 5 and recovered.retained_parts == ()
    registration = finding_registration(recovered)
    assert registration is not None
    record = ArtifactRecord(
        "artifact",
        "session",
        "a" * 64,
        recovered,
        "2026-09-09T00:00:00+00:00",
        "run",
        evidence_for(recovered),
    )
    for finding in findings:
        assert isinstance(finding.value, t.ContributionFindingValueV1)
        assert finding.value.method == "distinct_membership@v1"
        _validate(finding, record, registration)
    assert "__mv_distinct_key" not in encoded


@pytest.mark.parametrize("change", ["rollup", "safe", "numeric", "method"])
def test_cold_distinct_rejects_incompatible_method_resolution_facts(change: str) -> None:
    descriptor, frame, proof = _value()
    published, _ = build_attribution_publication(
        descriptor, frame, artifact_ref="artifact", session_ref="session", source_summary=proof
    )
    payload = descriptor_payload(published)
    row = payload["row_contract"]
    assert isinstance(row, dict)
    semantics = row["family_semantics"]
    assert isinstance(semantics, dict)
    if change == "rollup":
        semantics["resolution_semantics"] = "rollup"
    elif change == "safe":
        semantics["rollup_safe"] = True
    elif change == "numeric":
        semantics["numeric_type"] = "int64"
    else:
        semantics["method"] = "additive_difference@v1"
    with pytest.raises(IntegrityError):
        decode_descriptor(canonical_json(payload))


def test_distinct_publication_requires_source_proof_before_consuming_rows() -> None:
    descriptor, _, _ = _value()

    def forbidden() -> Iterator[pa.RecordBatch]:
        raise AssertionError("missing proof cannot create a local reader")
        yield from ()

    with pytest.raises(IntegrityError, match="source-reduced proof"):
        build_attribution_publication(
            descriptor, forbidden(), artifact_ref="artifact", session_ref="session"
        )


def test_distinct_selected_continuation_keeps_complete_proof_without_new_findings() -> None:
    descriptor, frame, proof = _value()
    published, _ = build_attribution_publication(
        descriptor, frame, artifact_ref="artifact", session_ref="session", source_summary=proof
    )
    metric = (
        make_distinct_sources()
        .observe(DISTINCT_BUYERS)
        .with_dimensions(REGION, CHANNEL)
        .aggregate()
    )
    logical = metric.compare(metric).attribute(axes=(REGION, CHANNEL), mode="hierarchy")
    selected_logical = logical.where(eq(logical.fields.get("region"), "b"))
    selected = replace(
        published,
        definition_fingerprint="ds_" + "b" * 64,
        storage_receipt=replace(published.storage_receipt, realized_row_count=2),
        dataset_materialization_contract=materialization_contract(selected_logical),
    )
    continued, findings = build_attribution_publication(
        selected, frame[frame.region == "b"], artifact_ref="continued", session_ref="session"
    )
    assert findings == ()
    evidence = continued.attribution_evidence
    assert evidence is not None and not evidence.complete and evidence.complete_row_count == 5
    assert evidence.origin_definition_fingerprint == descriptor.definition_fingerprint
    assert evidence.mapped_membership_digest == proof.mapped_membership_digest
    assert decode_descriptor(encode_descriptor(continued)).attribution_evidence == evidence
