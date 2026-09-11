"""Association quality and deterministic descriptive Findings from final rows."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from itertools import combinations

import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import _encode, finding_identity, finding_set_digest
from marivo.analysis.evidence._dataset_reads import CoordinateRule, FindingRegistration
from marivo.analysis.materialization.association_codec import AssociationEvidenceSummary
from marivo.analysis.materialization.comparison_publication import _scalar
from marivo.analysis.materialization.contracts import (
    FINDING_CAP,
    ArtifactDescriptor,
    _int,
    _text,
    canonical_json,
    invalid,
)
from marivo.analysis.operators.association_contracts import (
    MAX_CANDIDATES,
    SELECTION_RULE_ID,
    STATUSES,
    AssociationSearchSummary,
    AssociationSemantics,
    pair_approximation_bindings,
    selection_key,
)
from marivo.analysis.refs import ArtifactRef


def _lag(value: t.Scalar) -> int:
    if type(value) is not int:
        raise invalid("invalid signed Association lag")
    return value


def _selection(row: dict[str, t.Scalar]) -> tuple[float | int, ...]:
    coefficient = row["coefficient"]
    if type(coefficient) is not float:
        raise invalid("missing selected coefficient")
    lag = _lag(row["lag_offset"])
    return selection_key(coefficient, lag)


def association_key(finding: t.Finding) -> str:
    if not isinstance(finding.subject, t.AssociationFindingSubjectV1) or not isinstance(
        finding.value, t.AssociationFindingValueV1
    ):
        raise invalid("invalid Association Finding shape")
    return canonical_json(
        [
            [_encode(c.value) for c in finding.coordinates],
            _encode(finding.subject.metric_a),
            _encode(finding.subject.metric_b),
            finding.value.lag.lag_offset
            if isinstance(finding.value.lag, t.AssociationLagV1)
            else None,
        ]
    )


def finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration:
    s = descriptor.row_contract.family_semantics
    if not isinstance(s, AssociationSemantics):
        raise invalid("missing Association authority")
    subjects = tuple(
        t.AssociationFindingSubjectV1(
            metric_a=d._metric_identity_from_key(a.removeprefix("metric:")),
            metric_b=d._metric_identity_from_key(b.removeprefix("metric:")),
        )
        for a, b in combinations(s.metric_keys, 2)
    )
    contract = descriptor.dataset_materialization_contract
    return FindingRegistration(
        producer_id=contract.producer_id,
        extractor_contract_id=contract.finding_extractor_id,
        extractor_contract_version=str(contract.finding_extractor_version),
        shape_id=descriptor.row_contract.shape_id,
        finding_type="association",
        subject=subjects[0],
        coordinates=tuple(
            CoordinateRule(f, "dimension")
            for f in descriptor.realized_schema.columns
            if f.role_id == "dimension"
        ),
        source_artifact_refs=(),
        source_fields=tuple(
            f.field_id
            for f in descriptor.realized_schema.columns
            if f.name in ("coefficient", "complete_pair_count")
        ),
        canonical_item_key=association_key,
        association_subjects=subjects,
        association_method=s.method,
        association_lags=s.lag_offsets if "time" in s.input_shape else (),
    )


def build_association_publication(
    descriptor: ArtifactDescriptor,
    batches: Iterable[pa.RecordBatch],
    *,
    artifact_ref: str,
    session_ref: str,
    search_summary: AssociationSearchSummary | None = None,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    s = descriptor.row_contract.family_semantics
    if not isinstance(s, AssociationSemantics):
        raise invalid("missing Association semantics")
    registration = finding_registration(descriptor)
    dims = tuple(rule.field.name for rule in registration.coordinates)
    rows: list[dict[str, t.Scalar]] = []
    for batch in batches:
        if len(rows) + batch.num_rows > MAX_CANDIDATES:
            raise invalid("Association candidate ceiling exceeded")
        for raw in batch.to_pylist():
            rows.append({str(key): _scalar(value) for key, value in raw.items()})
    if len(rows) != descriptor.storage_receipt.realized_row_count:
        raise invalid("incomplete Association publication rows")
    status_counts = dict.fromkeys(STATUSES, 0)
    pairs = set(combinations(s.metric_keys, 2))
    grouped: dict[str, list[dict[str, t.Scalar]]] = {}
    candidates = []
    counts = []
    nulls = []
    time = "time" in s.input_shape
    producing = descriptor.dataset_materialization_contract.producer_id == "metric.correlate"
    for row in rows:
        a, b = _text(row["metric_key_a"]), _text(row["metric_key_b"])
        status = _text(row["status"])
        if (a, b) not in pairs or status not in STATUSES:
            raise invalid("invalid Association pair or status")
        status_counts[status] += 1
        n, missing, complete = (
            _int(row[name])
            for name in ("input_observation_count", "null_pair_count", "complete_pair_count")
        )
        if any(type(v) is not int or v < 0 for v in (n, missing, complete)):
            raise invalid("invalid Association counts")
        matched = _int(row["matched_observation_count"]) if time else n
        if type(matched) is not int or matched < 0 or matched > n or complete + missing != matched:
            raise invalid("inconsistent Association pair counts")
        if time and (
            row["lag_offset"] not in s.lag_offsets
            or row["lag_boundary_drop_count"] != n - matched
            or type(row["selected_for_pair"]) is not bool
        ):
            raise invalid("inconsistent lag coordinate or boundary count")
        coefficient = row["coefficient"]
        if (status == "insufficient_pairs") != (complete < 2):
            raise invalid("invalid insufficient-pair status")
        if status == "valid":
            if (
                type(coefficient) is not float
                or not math.isfinite(coefficient)
                or not -1 <= coefficient <= 1
            ):
                raise invalid("invalid valid coefficient")
        elif coefficient is not None or (time and row["selected_for_pair"]):
            raise invalid("unusable Association row claims a coefficient or selection")
        key = canonical_json([row[name] for name in (*dims, "metric_key_a", "metric_key_b")])
        grouped.setdefault(key, []).append(row)
        counts.append(complete)
        nulls.append(missing)
        if status != "valid":
            continue
        assert isinstance(coefficient, float)
        subject = t.AssociationFindingSubjectV1(
            metric_a=d._metric_identity_from_key(a.removeprefix("metric:")),
            metric_b=d._metric_identity_from_key(b.removeprefix("metric:")),
        )
        lag = (
            t.AssociationLagV1(
                lag_offset=_lag(row["lag_offset"]),
                selected_for_pair=row["selected_for_pair"] is True,
                matched_observation_count=matched,
                lag_boundary_drop_count=n - matched,
            )
            if time
            else t.NoAssociationLagV1()
        )
        item = t.Finding(
            finding_id="pending",
            artifact_ref=ArtifactRef(ref=artifact_ref),
            session_id=session_ref,
            finding_type="association",
            epistemic_kind="estimated",
            subject=subject,
            coordinates=tuple(
                t.FindingCoordinateV1(
                    field_id=rule.field.field_id,
                    identity=rule.field.identity,
                    value=row[rule.field.name],
                )
                for rule in registration.coordinates
            ),
            canonical_item_key="pending",
            value=t.AssociationFindingValueV1(
                method=s.method,
                coefficient=coefficient,
                input_observation_count=n,
                null_pair_count=missing,
                complete_pair_count=complete,
                lag=lag,
            ),
            derivation=t.FindingDerivationV1(
                producer_id=registration.producer_id,
                extractor_contract_id=registration.extractor_contract_id,
                extractor_contract_version=registration.extractor_contract_version,
                source_artifact_refs=registration.source_artifact_refs,
                source_fields=registration.source_fields,
            ),
            committed_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        item = replace(item, canonical_item_key=association_key(item))
        candidates.append(replace(item, finding_id=finding_identity(item)))
    if producing:
        if not grouped:
            raise invalid("no valid Association candidate")
        series = {canonical_json([row[name] for name in dims]) for row in rows}
        if search_summary is None or (
            len(rows) != search_summary.candidate_count
            or len(series) != search_summary.series_count
            or len(grouped) != len(series) * len(pairs)
            or len(rows) != len(grouped) * len(s.lag_offsets)
        ):
            raise invalid("incomplete original Association pair/series search")
        for group in grouped.values():
            valid = [r for r in group if r["status"] == "valid"]
            if not valid:
                raise invalid("pair/series has no valid candidate")
            if time:
                if {r["lag_offset"] for r in group} != set(s.lag_offsets):
                    raise invalid("incomplete Association lag candidates")
                selected = min(
                    valid,
                    key=_selection,
                )
                if any(bool(r["selected_for_pair"]) != (r is selected) for r in group):
                    raise invalid("Association selection rule contradiction")

    def finding_order(item: t.Finding) -> tuple[float, str]:
        value = item.value
        if not isinstance(value, t.AssociationFindingValueV1):
            raise invalid("invalid Association Finding value")
        coefficient = value.coefficient
        if not isinstance(coefficient, (float, int)):
            raise invalid("invalid descriptive coefficient type")
        return (-abs(float(coefficient)), item.canonical_item_key)

    candidates.sort(key=finding_order)
    findings = tuple(candidates[:FINDING_CAP])
    if search_summary is None:
        raise invalid("missing original complete Association search authority")
    evidence = AssociationEvidenceSummary(
        len(rows),
        tuple(status_counts.items()),
        len(findings),
        finding_set_digest(findings),
        len(pairs),
        len(s.lag_offsets),
        search_summary.series_count,
        search_summary.candidate_count,
        search_summary.complete_pair_range,
        search_summary.null_pair_range,
        SELECTION_RULE_ID,
        pair_approximation_bindings(s),
        len(candidates),
        len(candidates) > len(findings),
    )
    return replace(descriptor, association_evidence=evidence), findings
