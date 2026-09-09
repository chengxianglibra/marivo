"""Attribution reconciliation, bounded Findings, and retained proof continuations."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cmp_to_key
from typing import Literal, TypeAlias

import pandas as pd
import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import _encode, finding_identity, finding_set_digest
from marivo.analysis.evidence._dataset_reads import CoordinateRule, FindingRegistration
from marivo.analysis.materialization.comparison_publication import (
    _difference,
    _number,
    _scalar,
    delta_finding_registration,
)
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    AttributionEvidenceSummary,
    canonical_json,
    invalid,
)
from marivo.analysis.operators.attribute_values import exact_magnitude
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.analysis.operators.row import compare_value
from marivo.analysis.refs import ArtifactRef

_Cell: TypeAlias = t.Scalar | tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class AttributionSourceSummary:
    """Source-reduced proof facts; no Entity identity crosses this interface."""

    scope_count: int
    resolution_count: int
    status_counts: tuple[tuple[str, int], ...]
    max_reconciliation_error: float
    mapped_membership_digest: str


def _semantics(descriptor: ArtifactDescriptor) -> AttributionSemantics:
    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, AttributionSemantics):
        raise invalid("missing exact Attribution row semantics")
    if semantics.method == "distinct_membership@v1" and (
        semantics.resolution_semantics != "independent"
        or semantics.rollup_safe
        or semantics.numeric_type != "float64"
    ):
        raise invalid("distinct Attribution requires independent floating-point allocation")
    return semantics


def _masks(semantics: AttributionSemantics) -> tuple[tuple[bool, ...], ...]:
    return tuple(
        tuple(index < len(prefix) for index in range(len(semantics.axis_field_ids)))
        for prefix in semantics.resolution_prefixes
    )


def contribution_item_key(finding: t.Finding) -> str:
    value = finding.value
    if not isinstance(value, t.ContributionFindingValueV1):
        raise invalid("invalid Contribution Finding value")
    return canonical_json(
        [
            [[item.field_id.value, _encode(item.value)] for item in finding.coordinates],
            value.active_axis_mask,
            value.other_mask,
        ]
    )


def finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration | None:
    if descriptor.row_contract.shape_id.family_id != "attribution":
        return delta_finding_registration(descriptor)
    semantics = _semantics(descriptor)
    contract = descriptor.dataset_materialization_contract
    if contract.finding_policy_id == "zero_findings@v1":
        return None
    fields = {field.field_id: field for field in descriptor.realized_schema.columns}
    if any(field.identity.kind == "entity_identity" for field in fields.values()):
        raise invalid("Entity-scoped Attribution cannot register Findings")
    coordinates: list[CoordinateRule] = []
    for field_id in semantics.scope_field_ids:
        field = fields[field_id]
        kind: Literal["comparison_ordinal", "dimension"] = (
            "comparison_ordinal" if field.name == "comparison_ordinal" else "dimension"
        )
        coordinates.append(CoordinateRule(field, kind))
    for index, field_id in enumerate(semantics.axis_field_ids):
        coordinates.append(CoordinateRule(fields[field_id], "dimension", index))
    for name in (semantics.current_time_field_name, semantics.baseline_time_field_name):
        if name is not None:
            coordinates.append(
                CoordinateRule(
                    next(field for field in fields.values() if field.name == name), "time"
                )
            )
    return FindingRegistration(
        producer_id=contract.producer_id,
        extractor_contract_id=contract.finding_extractor_id,
        extractor_contract_version=str(contract.finding_extractor_version),
        shape_id=descriptor.row_contract.shape_id,
        finding_type="contribution",
        subject=t.MetricFindingSubjectV1(
            metric=d._catalog_identity("metric:" + semantics.metric_ref)
        ),
        coordinates=tuple(coordinates),
        source_artifact_refs=tuple(
            ArtifactRef(ref=ref)
            for ref in dict.fromkeys(
                ref
                for operand in descriptor.comparison_inputs
                for ref in operand.source_artifact_refs
            )
        ),
        source_fields=tuple(
            field.field_id
            for field in fields.values()
            if field.name in ("current_value", "baseline_value", "overall_delta", "contribution")
        ),
        canonical_item_key=contribution_item_key,
        contribution_method=semantics.method,
        active_axis_masks=_masks(semantics),
    )


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    semantics = _semantics(descriptor)
    evidence = descriptor.attribution_evidence
    from marivo.analysis.observation.fold_contracts import decode_fold_authority
    from marivo.analysis.operators.attribute import attribute_method

    if descriptor.attribution_fold_authority is None:
        raise invalid("missing complete Attribution side fold and partition authority")
    fields = {field.field_id: field for field in descriptor.row_contract.schema.columns}
    axes: list[str] = []
    for field_id in semantics.axis_field_ids:
        identity = fields[field_id].identity
        if not isinstance(identity, d._CatalogFieldIdentity):
            raise invalid("Attribution axes require governed Dimension identities")
        axes.append(identity.identity_id.removeprefix("dimension:"))
    for payload in descriptor.attribution_fold_authority:
        authority = decode_fold_authority(payload)
        if len(authority.metrics) != 1 or authority.metrics[0].metric_ref != semantics.metric_ref:
            raise invalid(
                "Attribution method contradicts retained side fold and partition authority"
            )
        try:
            method = attribute_method(authority.metrics[0], tuple(axes))
        except DatasetConstructionError as exc:
            raise invalid(
                "Attribution method contradicts retained side fold and partition authority"
            ) from exc
        if method != semantics.method:
            raise invalid(
                "Attribution method contradicts retained side fold and partition authority"
            )
    if (
        len(descriptor.comparison_inputs) != 2
        or descriptor.delta_evidence is not None
        or evidence is None
    ):
        raise invalid("missing complete Attribution comparison authority or Evidence")
    if evidence.method != semantics.method or evidence.approximate != any(
        item.sampling_execution is not None for item in descriptor.comparison_inputs
    ):
        raise invalid("Attribution Evidence contradicts method or approximation authority")
    if (semantics.approximation_class == "sampled_population") != evidence.approximate:
        raise invalid(
            "Attribution row interpretation differs from retained approximation authority"
        )
    if (
        evidence.complete
        and evidence.complete_row_count != descriptor.storage_receipt.realized_row_count
    ):
        raise invalid("complete Attribution proof row count differs")
    if descriptor.storage_receipt.realized_row_count > evidence.complete_row_count:
        raise invalid("Attribution continuation exceeds its complete proof rows")
    if (
        descriptor.dataset_materialization_contract.finding_policy_id == "zero_findings@v1"
        and evidence.eligible_finding_count
    ):
        raise invalid("result-only or Entity Attribution cannot emit new Findings")
    if descriptor.retained_parts and any(
        part.contract_id != "population_sampling_state" for part in descriptor.retained_parts
    ):
        raise invalid("unregistered Attribution retained computation parts")


def _rows(
    rows: pd.DataFrame | Iterable[pa.RecordBatch], expected: tuple[str, ...]
) -> Iterator[dict[str, _Cell]]:
    def cell(name: str, value: object) -> _Cell:
        if name in ("active_axis_mask", "other_mask"):
            mask = d._bool_tuple_value(value)
            if mask is None:
                raise invalid("invalid boolean Attribution mask")
            return mask
        return _scalar(value)

    if isinstance(rows, pd.DataFrame):
        if tuple(rows.columns) != expected:
            raise invalid("Attribution publication schema differs")
        for values in rows.itertuples(index=False, name=None):
            yield {name: cell(name, value) for name, value in zip(expected, values, strict=True)}
        return
    for batch in rows:
        if not isinstance(batch, pa.RecordBatch):
            raise invalid("Attribution publication requires complete Arrow record batches")
        if tuple(batch.schema.names) != expected:
            raise invalid("Attribution publication batch schema differs")
        for values in zip(*(column.to_pylist() for column in batch.columns), strict=True):
            yield {name: cell(name, value) for name, value in zip(expected, values, strict=True)}


def _number_cell(value: _Cell, numeric: str) -> t.Number:
    if isinstance(value, tuple):
        raise invalid("a partition mask cannot be an Attribution number")
    return _number(value, numeric)


def _scalar_cell(value: _Cell) -> t.Scalar:
    if isinstance(value, tuple):
        raise invalid("a partition mask cannot be an ordinary Finding coordinate")
    return value


def _close(left: t.Number, right: t.Number) -> bool:
    return abs(float(left) - float(right)) <= max(
        1e-12, 1e-9 * max(abs(float(left)), abs(float(right)), 1)
    )


def _share(
    value: _Cell, reason: Literal["zero_total_delta", "empty_positive_pool", "empty_negative_pool"]
) -> t.FindingShareV1:
    if value is None:
        return t.UndefinedFindingShareV1(reason=reason)
    return t.DefinedFindingRatioV1(value=_number_cell(value, "float64"))


def _finding(
    row: dict[str, _Cell],
    registration: FindingRegistration,
    descriptor: ArtifactDescriptor,
    artifact_ref: str,
    session_ref: str,
) -> t.Finding:
    semantics = _semantics(descriptor)
    active, other = row["active_axis_mask"], row["other_mask"]
    rank, status = row["contribution_rank"], row["status"]
    if (
        not isinstance(active, tuple)
        or not isinstance(other, tuple)
        or type(rank) is not int
        or status not in ("ok", "zero_total_delta")
    ):
        raise invalid("invalid Attribution Finding row")
    value = t.ContributionFindingValueV1(
        method=semantics.method,
        active_axis_mask=active,
        other_mask=other,
        contribution_kind="metric",
        current_value=_number_cell(row["current_value"], semantics.numeric_type),
        baseline_value=_number_cell(row["baseline_value"], semantics.numeric_type),
        overall_delta=_number_cell(row["overall_delta"], semantics.numeric_type),
        contribution=_number_cell(row["contribution"], semantics.numeric_type),
        share_of_total_delta=_share(row["share_of_total_delta"], "zero_total_delta"),
        share_of_positive_pool=_share(row["share_of_positive_pool"], "empty_positive_pool"),
        share_of_negative_pool=_share(row["share_of_negative_pool"], "empty_negative_pool"),
        contribution_rank=rank,
        status="ok" if status == "ok" else "zero_total_delta",
    )
    result = t.Finding(
        finding_id="pending",
        artifact_ref=ArtifactRef(ref=artifact_ref),
        session_id=session_ref,
        finding_type="contribution",
        epistemic_kind="algebraic",
        subject=registration.subject,
        coordinates=tuple(
            t.FindingCoordinateV1(
                field_id=rule.field.field_id,
                identity=rule.field.identity,
                value=_scalar_cell(row[rule.field.name]),
            )
            for rule in registration.coordinates
        ),
        canonical_item_key="pending",
        value=value,
        derivation=t.FindingDerivationV1(
            producer_id=registration.producer_id,
            extractor_contract_id=registration.extractor_contract_id,
            extractor_contract_version=registration.extractor_contract_version,
            source_artifact_refs=registration.source_artifact_refs,
            source_fields=registration.source_fields,
        ),
        committed_at=datetime(1970, 1, 1, tzinfo=UTC),
    )
    result = replace(result, canonical_item_key=registration.canonical_item_key(result))
    return replace(result, finding_id=finding_identity(result))


def _candidate_compare(left: t.Finding, right: t.Finding) -> int:
    if not isinstance(left.value, t.ContributionFindingValueV1) or not isinstance(
        right.value, t.ContributionFindingValueV1
    ):
        raise invalid("invalid Contribution Finding candidate")
    magnitude = compare_value(
        exact_magnitude(right.value.contribution), exact_magnitude(left.value.contribution)
    )
    return (
        magnitude
        or compare_value(
            tuple(item.value for item in left.coordinates),
            tuple(item.value for item in right.coordinates),
        )
        or compare_value(left.value.active_axis_mask, right.value.active_axis_mask)
        or compare_value(left.value.other_mask, right.value.other_mask)
    )


def build_attribution_publication(
    descriptor: ArtifactDescriptor,
    rows: pd.DataFrame | Iterable[pa.RecordBatch] | None,
    *,
    artifact_ref: str,
    session_ref: str,
    top_k: int | None = None,
    source_summary: AttributionSourceSummary | None = None,
    continuation: bool = False,
    proof_definition_fingerprint: str | None = None,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    """Publish complete numerical proof, or preserve it through selected row reads."""
    semantics = _semantics(descriptor)
    inherited = descriptor.attribution_evidence
    if inherited is not None:
        return replace(
            descriptor,
            attribution_evidence=replace(
                inherited,
                complete=False,
                eligible_finding_count=0,
                emitted_finding_count=0,
                finding_truncated=False,
                finding_set_digest=finding_set_digest(()),
            ),
        ), ()
    if semantics.method == "distinct_membership@v1" and source_summary is None:
        raise invalid("distinct Attribution publication requires complete source-reduced proof")
    if continuation:
        if source_summary is None or proof_definition_fingerprint is None:
            raise invalid("selected Attribution requires its complete pre-selection proof")
        summary = source_summary
        evidence = AttributionEvidenceSummary(
            semantics.method,
            proof_definition_fingerprint,
            sum(count for _, count in summary.status_counts),
            summary.scope_count,
            summary.resolution_count,
            summary.mapped_membership_digest,
            summary.max_reconciliation_error,
            summary.status_counts,
            any(item.sampling_execution is not None for item in descriptor.comparison_inputs),
            False,
            top_k,
            0,
            0,
            False,
            finding_set_digest(()),
        )
        return replace(descriptor, attribution_evidence=evidence), ()
    entity = any(
        field.identity.kind == "entity_identity" for field in descriptor.realized_schema.columns
    )
    if entity:
        if source_summary is None or rows is not None:
            raise invalid(
                "Entity Attribution publication requires source-reduced proof without identity rows"
            )
        summary = source_summary
        evidence = AttributionEvidenceSummary(
            semantics.method,
            descriptor.definition_fingerprint,
            descriptor.storage_receipt.realized_row_count,
            summary.scope_count,
            summary.resolution_count,
            summary.mapped_membership_digest,
            summary.max_reconciliation_error,
            summary.status_counts,
            any(item.sampling_execution is not None for item in descriptor.comparison_inputs),
            True,
            top_k,
            0,
            0,
            False,
            finding_set_digest(()),
        )
        return replace(descriptor, attribution_evidence=evidence), ()
    if rows is None:
        raise invalid("complete non-Entity Attribution publication requires finalized rows")
    registration = finding_registration(descriptor)
    candidates: list[t.Finding] = []

    def emit(row: dict[str, _Cell]) -> None:
        if registration is None:
            return
        item = _finding(row, registration, descriptor, artifact_ref, session_ref)
        low, high = 0, len(candidates)
        while low < high:
            middle = (low + high) // 2
            if _candidate_compare(item, candidates[middle]) < 0:
                high = middle
            else:
                low = middle + 1
        candidates.insert(low, item)
        if len(candidates) > 1000:
            candidates.pop()

    summary = (
        _summarize_rows(rows, descriptor.row_contract, emit)
        if source_summary is None
        else _scan_proven_rows(rows, descriptor.row_contract, source_summary, emit)
    )
    row_count = sum(count for _, count in summary.status_counts)
    if row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("incomplete Attribution publication rows")
    findings = tuple(candidates)
    eligible = row_count if registration is not None else 0
    evidence = AttributionEvidenceSummary(
        semantics.method,
        descriptor.definition_fingerprint,
        row_count,
        summary.scope_count,
        summary.resolution_count,
        summary.mapped_membership_digest,
        summary.max_reconciliation_error,
        summary.status_counts,
        any(item.sampling_execution is not None for item in descriptor.comparison_inputs),
        True,
        top_k,
        eligible,
        len(findings),
        eligible > 1000,
        finding_set_digest(findings),
    )
    return replace(descriptor, attribution_evidence=evidence), findings


def _summarize_rows(
    rows: pd.DataFrame | Iterable[pa.RecordBatch],
    row_contract: d.DatasetRowContract,
    emit: Callable[[dict[str, _Cell]], None] | None = None,
) -> AttributionSourceSummary:
    semantics = row_contract.family_semantics
    if not isinstance(semantics, AttributionSemantics):
        raise invalid("missing exact Attribution row semantics")
    fields = {field.field_id: field for field in row_contract.schema.columns}
    scope_names = tuple(fields[item].name for item in semantics.scope_field_ids)
    axis_names = tuple(fields[item].name for item in semantics.axis_field_ids)
    key_names = tuple(fields[item].name for item in row_contract.key_field_ids)
    expected = tuple(field.name for field in row_contract.schema.columns)
    allowed = _masks(semantics)
    groups: dict[tuple[_Cell, ...], list[dict[str, _Cell]]] = {}
    scope_keys: set[tuple[_Cell, ...]] = set()
    row_keys: set[tuple[_Cell, ...]] = set()
    scope_times: dict[tuple[_Cell, ...], tuple[_Cell, ...]] = {}
    counts = dict.fromkeys(("ok", "zero_total_delta"), 0)
    membership = hashlib.sha256()
    for row in _rows(rows, expected):
        status = _check_output_row(row, semantics, axis_names, allowed)
        counts[status] += 1
        active = row["active_axis_mask"]
        scope = tuple(row[name] for name in scope_names)
        scope_keys.add(scope)
        row_key = tuple(row[name] for name in key_names)
        if row_key in row_keys:
            raise invalid("duplicate complete Attribution row key")
        row_keys.add(row_key)
        times = tuple(
            row[name]
            for name in (semantics.current_time_field_name, semantics.baseline_time_field_name)
            if name is not None
        )
        if scope_times.setdefault(scope, times) != times:
            raise invalid("paired Attribution time differs across scope resolutions")
        groups.setdefault((*scope, active), []).append(row)
        membership.update(
            canonical_json(
                [
                    [_encode(row[name]) if not isinstance(row[name], tuple) else row[name]]
                    for name in key_names
                ]
            ).encode()
        )
        membership.update(b"\n")
        if emit is not None:
            emit(row)
    maximum = 0.0
    if any(any((*scope, mask) not in groups for mask in allowed) for scope in scope_keys):
        raise invalid("incomplete Attribution resolution set for an exact scope")
    for group in groups.values():
        overall = _number_cell(group[0]["overall_delta"], semantics.numeric_type)
        contributions = [_number_cell(row["contribution"], semantics.numeric_type) for row in group]
        total = math.fsum(float(value) for value in contributions)
        error = abs(float(overall) - total)
        maximum = max(maximum, error)
        if not _close(overall, total) or any(
            not _close(_number_cell(row["overall_delta"], semantics.numeric_type), overall)
            for row in group
        ):
            raise invalid("Attribution resolution does not reconcile its independent overall Delta")
        positive = math.fsum(max(float(value), 0) for value in contributions)
        negative = math.fsum(max(-float(value), 0) for value in contributions)

        def compare_rows(left: dict[str, _Cell], right: dict[str, _Cell]) -> int:
            return compare_value(
                exact_magnitude(_number_cell(right["contribution"], semantics.numeric_type)),
                exact_magnitude(_number_cell(left["contribution"], semantics.numeric_type)),
            ) or compare_value(
                tuple(left[name] for name in key_names), tuple(right[name] for name in key_names)
            )

        ordered = sorted(group, key=cmp_to_key(compare_rows))
        for ordinal, row in enumerate(ordered, 1):
            if row["contribution_rank"] != ordinal:
                raise invalid(
                    "Attribution contribution rank violates its scope-resolution ordering"
                )
            contribution = float(_number_cell(row["contribution"], semantics.numeric_type))
            for name, numerator, denominator in (
                ("share_of_total_delta", contribution, float(overall)),
                ("share_of_positive_pool", max(contribution, 0), positive),
                ("share_of_negative_pool", max(-contribution, 0), negative),
            ):
                value = row[name]
                if denominator == 0:
                    if value is not None:
                        raise invalid("empty Attribution share denominator requires null")
                elif value is None or not _close(
                    _number_cell(value, "float64"), numerator / denominator
                ):
                    raise invalid("Attribution share violates its complete resolution pool")
            for time_name in (
                semantics.current_time_field_name,
                semantics.baseline_time_field_name,
            ):
                if time_name is not None and row[time_name] != group[0][time_name]:
                    raise invalid("paired Attribution time differs within one exact scope")
    return AttributionSourceSummary(
        len(scope_keys), len(groups), tuple(counts.items()), maximum, membership.hexdigest()
    )


def summarize_attribution_frame(
    frame: pd.DataFrame, row_contract: d.DatasetRowContract
) -> AttributionSourceSummary:
    """Check complete local Attribution before a result-only selected suffix."""
    if any(field.identity.kind == "entity_identity" for field in row_contract.schema.columns):
        raise invalid("Entity Attribution proof must remain at its source")
    return _summarize_rows(frame, row_contract)


def _check_output_row(
    row: dict[str, _Cell],
    semantics: AttributionSemantics,
    axis_names: tuple[str, ...],
    allowed_masks: tuple[tuple[bool, ...], ...],
) -> str:
    active, other = row["active_axis_mask"], row["other_mask"]
    if (
        not isinstance(active, tuple)
        or not isinstance(other, tuple)
        or active not in allowed_masks
        or len(other) != len(active)
        or any(value and not selected for value, selected in zip(other, active, strict=True))
    ):
        raise invalid("Attribution masks contradict their exact resolution registration")
    if any(
        (not selected or mapped) and row[name] is not None
        for name, selected, mapped in zip(axis_names, active, other, strict=True)
    ):
        raise invalid("inactive or Other Attribution axis must contain typed null")
    numbers = {
        name: _number_cell(row[name], semantics.numeric_type)
        for name in ("current_value", "baseline_value", "overall_delta", "contribution")
    }
    if not _close(
        _difference(numbers["current_value"], numbers["baseline_value"]),
        numbers["contribution"],
    ):
        raise invalid("Attribution output violates its side-term difference formula")
    status = row["status"]
    if (
        type(status) is not str
        or status not in ("ok", "zero_total_delta")
        or (numbers["overall_delta"] == 0) != (status == "zero_total_delta")
    ):
        raise invalid("Attribution overall Delta status mismatch")
    return status


def _scan_proven_rows(
    rows: pd.DataFrame | Iterable[pa.RecordBatch],
    row_contract: d.DatasetRowContract,
    summary: AttributionSourceSummary,
    emit: Callable[[dict[str, _Cell]], None],
) -> AttributionSourceSummary:
    """Stream output checks after complete source or bounded local group validation."""
    semantics = row_contract.family_semantics
    if not isinstance(semantics, AttributionSemantics):
        raise invalid("missing exact Attribution row semantics")
    fields = {field.field_id: field.name for field in row_contract.schema.columns}
    axes = tuple(fields[field] for field in semantics.axis_field_ids)
    allowed = _masks(semantics)
    counts = dict.fromkeys(("ok", "zero_total_delta"), 0)
    for row in _rows(rows, tuple(field.name for field in row_contract.schema.columns)):
        counts[_check_output_row(row, semantics, axes, allowed)] += 1
        emit(row)
    if tuple(counts.items()) != summary.status_counts:
        raise invalid("published Attribution rows differ from complete validated source proof")
    return summary
