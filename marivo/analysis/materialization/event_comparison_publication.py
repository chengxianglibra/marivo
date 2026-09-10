"""Event-owned bounded algebraic Findings and independently validated output rows."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
from functools import cmp_to_key
from typing import Literal

import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.datasets.base import Dataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.event_attribution import METHOD, FunnelAttributionSemantics
from marivo.analysis.domains.event_comparison import (
    COUNTS,
    FunnelComparePayload,
    FunnelDeltaSemantics,
)
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import finding_identity, finding_set_digest
from marivo.analysis.evidence._dataset_reads import CoordinateRule, FindingRegistration
from marivo.analysis.materialization.attribution_publication import _share
from marivo.analysis.materialization.contracts import (
    FINDING_CAP,
    ArtifactDescriptor,
    canonical_json,
    invalid,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.event_comparison_codec import (
    FunnelEvidenceSummary,
    decode_evidence,
    evidence_payload,
)
from marivo.analysis.operators.attribute_values import reconciles
from marivo.analysis.operators.row_values import compare_value
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind


def item_key(finding: t.Finding) -> str:
    from marivo.analysis.evidence._dataset_codec import _encode

    value = finding.value
    return canonical_json(
        [
            [[v.field_id.value, _encode(v.value)] for v in finding.coordinates],
            *(
                [value.active_axis_mask, value.other_mask, value.contribution_kind]
                if isinstance(value, t.ContributionFindingValueV1)
                else []
            ),
        ]
    )


def finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration:
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, (FunnelDeltaSemantics, FunnelAttributionSemantics))
    attribution = isinstance(semantics, FunnelAttributionSemantics)
    delta = semantics.delta if isinstance(semantics, FunnelAttributionSemantics) else semantics
    journey = delta.current.journey
    fields = descriptor.realized_schema.columns
    axes = tuple(f for f in fields if f.role_id == "dimension")
    coordinates = tuple(
        CoordinateRule(f, "dimension", i if attribution else None) for i, f in enumerate(axes)
    )
    if not attribution:
        coordinates += (CoordinateRule(next(f for f in fields if f.name == "step_key"), "step"),)
    contract = descriptor.dataset_materialization_contract
    return FindingRegistration(
        producer_id=contract.producer_id,
        extractor_contract_id=contract.finding_extractor_id,
        extractor_contract_version=str(contract.finding_extractor_version),
        shape_id=descriptor.row_contract.shape_id,
        finding_type="contribution" if attribution else "funnel_delta",
        subject=t.FunnelFindingSubjectV1(
            subject_entity_ref=RefPayloadV1(
                schema="marivo.semantic_ref/v1",
                kind=SemanticKind.ENTITY,
                path=journey.subject_entity_ref,
            ),
            pattern_fingerprint=journey.pattern.fingerprint,
        ),
        coordinates=coordinates,
        source_artifact_refs=tuple(
            ArtifactRef(ref=ref)
            for ref in dict.fromkeys(
                ref
                for operand in descriptor.comparison_inputs
                for ref in operand.source_artifact_refs
            )
        ),
        source_fields=tuple(
            f.field_id for f in fields if f.role_id in ("comparison_value", "effect_value")
        ),
        canonical_item_key=item_key,
        contribution_method=METHOD if attribution else None,
        active_axis_masks=tuple(
            tuple(i < len(prefix) for i in range(len(semantics.axis_field_ids)))
            for prefix in semantics.resolution_prefixes
        )
        if isinstance(semantics, FunnelAttributionSemantics)
        else (),
    )


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    summary = descriptor.funnel_evidence
    if (
        summary is None
        or decode_evidence(evidence_payload(summary)) != summary
        or summary.row_count != descriptor.storage_receipt.realized_row_count
        or len(descriptor.comparison_inputs) != 2
    ):
        raise invalid("missing or inconsistent Event comparison Evidence")
    if (
        descriptor.delta_evidence is not None
        or descriptor.attribution_evidence is not None
        or descriptor.attribution_fold_authority is not None
    ):
        raise invalid("Event comparison cannot inherit Metric arithmetic authority")
    from marivo.analysis.domains.event_attribution import COMPONENT_CONTRACT, COMPONENT_ROLE

    expected = (
        {COMPONENT_ROLE}
        if isinstance(descriptor.row_contract.family_semantics, FunnelAttributionSemantics)
        else set()
    )
    parts = tuple(
        p for p in descriptor.retained_parts if p.contract_id != "population_sampling_state"
    )
    if {p.role for p in parts} != expected or any(
        p.contract_id != COMPONENT_CONTRACT
        or p.contract_version != 1
        or p.storage_receipt.realized_row_count != summary.row_count
        for p in parts
    ):
        raise invalid("missing or inconsistent Event mapped component authority")
    attribution = isinstance(descriptor.row_contract.family_semantics, FunnelAttributionSemantics)
    statuses = (
        {"ok", "zero_total_delta"} if attribution else {"ok", "zero_denominator", "missing_side"}
    )
    if not {s for s, _ in summary.status_counts} <= statuses or (
        not attribution and summary.resolution_count
    ):
        raise invalid("invalid Event comparison status Evidence")


def build_publication(
    descriptor: ArtifactDescriptor,
    batches: Iterable[pa.RecordBatch],
    *,
    artifact_ref: str,
    session_ref: str,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    semantics = descriptor.row_contract.family_semantics
    assert isinstance(semantics, (FunnelDeltaSemantics, FunnelAttributionSemantics))
    registration = finding_registration(descriptor)
    findings: list[t.Finding] = []
    statuses: dict[str, int] = {}
    row_count = eligible = 0
    contributions: dict[tuple[bool, ...], Fraction] = {}
    endpoints: dict[tuple[bool, ...], float] = {}
    share_sums: dict[tuple[tuple[bool, ...], str], Fraction] = {}
    for batch in batches:
        for record in batch.to_pylist():
            row: dict[str, object] = record
            row_count += 1

            def integer(name: str, row: dict[str, object] = row) -> int:
                value = row[name]
                if type(value) is not int or value < 0:
                    raise invalid(
                        "Event comparison counts and ranks require exact nonnegative integers"
                    )
                return value

            def number(name: str, row: dict[str, object] = row) -> float:
                value = row[name]
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                ):
                    raise invalid("Event comparison arithmetic must be finite")
                return float(value)

            def mask(name: str, row: dict[str, object] = row) -> tuple[bool, ...]:
                value = row[name]
                if not isinstance(value, list) or any(type(v) is not bool for v in value):
                    raise invalid("invalid Event contribution mask")
                return tuple(v for v in value if isinstance(v, bool))

            status = row[
                "status"
                if isinstance(semantics, FunnelAttributionSemantics)
                else "calculation_status"
            ]
            if not isinstance(status, str):
                raise invalid("invalid Event operator status")
            statuses[status] = statuses.get(status, 0) + 1
            value: t.FunnelDeltaFindingValueV1 | t.ContributionFindingValueV1
            if isinstance(semantics, FunnelDeltaSemantics):
                presence, step = row["coordinate_presence"], row["step_key"]
                if (
                    presence not in ("matched", "current_only", "baseline_only")
                    or not isinstance(step, str)
                    or step not in tuple(v.key for v in semantics.current.journey.pattern.steps)
                ):
                    raise invalid("invalid Event delta coordinate")
                for side in ("current", "baseline"):
                    counts = {name: integer(f"{side}_{name}") for name in COUNTS}
                    if (
                        counts["coverage_censored_count"]
                        or counts["cohort_count"] != counts["resolved_cohort_count"]
                        or counts["entry_count"] != counts["resolved_entry_count"]
                        or counts["lost_count"] + counts["reached_count"]
                        != counts["resolved_entry_count"]
                        or counts["entry_count"] > counts["cohort_count"]
                    ):
                        raise invalid("Event Delta completeness or count reconciliation failed")
                    if presence == (
                        "current_only" if side == "baseline" else "baseline_only"
                    ) and any(counts.values()):
                        raise invalid("absent Event side must have zero additive counts")
                ca, cb = (
                    integer("current_resolved_entry_count"),
                    integer("baseline_resolved_entry_count"),
                )
                expected = (
                    "missing_side"
                    if presence != "matched"
                    else "zero_denominator"
                    if not ca or not cb or step == semantics.current.journey.pattern.steps[0].key
                    else "ok"
                )
                if status != expected:
                    raise invalid("Event Delta calculation status contradicts its counts")
                for side in ("current", "baseline"):
                    denominator = integer(f"{side}_resolved_entry_count")
                    rate = row[f"{side}_loss_rate_from_previous"]
                    expected_rate = (
                        float(integer(f"{side}_lost_count")) / float(denominator)
                        if denominator and step != semantics.current.journey.pattern.steps[0].key
                        else None
                    )
                    if rate != expected_rate:
                        raise invalid(
                            "Event side rate differs from its own complete additive counts"
                        )
                if status != "ok":
                    if row["loss_rate_delta"] is not None:
                        raise invalid("undefined Event delta must be null")
                    continue
                a, b = (
                    float(integer("current_lost_count")) / float(ca),
                    float(integer("baseline_lost_count")) / float(cb),
                )
                if (
                    number("current_loss_rate_from_previous") != a
                    or number("baseline_loss_rate_from_previous") != b
                    or not reconciles(number("loss_rate_delta"), a - b)
                ):
                    raise invalid("Event Delta rates do not reproduce exact counts")
                value = t.FunnelDeltaFindingValueV1(
                    step_key=step,
                    coordinate_presence="matched",
                    current_cohort_count=integer("current_cohort_count"),
                    baseline_cohort_count=integer("baseline_cohort_count"),
                    current_resolved_cohort_count=integer("current_resolved_cohort_count"),
                    baseline_resolved_cohort_count=integer("baseline_resolved_cohort_count"),
                    current_entry_count=integer("current_entry_count"),
                    baseline_entry_count=integer("baseline_entry_count"),
                    current_resolved_entry_count=integer("current_resolved_entry_count"),
                    baseline_resolved_entry_count=integer("baseline_resolved_entry_count"),
                    current_reached_count=integer("current_reached_count"),
                    baseline_reached_count=integer("baseline_reached_count"),
                    current_lost_count=integer("current_lost_count"),
                    baseline_lost_count=integer("baseline_lost_count"),
                    current_coverage_censored_count=integer("current_coverage_censored_count"),
                    baseline_coverage_censored_count=integer("baseline_coverage_censored_count"),
                    current_loss_rate_from_previous=a,
                    baseline_loss_rate_from_previous=b,
                    loss_rate_delta=a - b,
                )
            else:
                active, other = mask("active_axis_mask"), mask("other_mask")
                kind = row["contribution_kind"]
                if (
                    active not in registration.active_axis_masks
                    or len(other) != len(active)
                    or kind not in ("loss", "denominator_mix")
                    or row["method"] != METHOD
                    or row["causal_claim"] != "none"
                ):
                    raise invalid("invalid Event contribution method or coordinate")
                overall, contribution = number("overall_delta"), number("contribution")
                if (
                    status != ("zero_total_delta" if overall == 0 else "ok")
                    or integer("contribution_rank") < 1
                ):
                    raise invalid("Event contribution status or rank contradicts arithmetic")
                axes = tuple(
                    f for f in descriptor.realized_schema.columns if f.role_id == "dimension"
                )
                if any(
                    (not enabled and is_other)
                    or ((not enabled or is_other) and row[field.name] is not None)
                    for field, enabled, is_other in zip(axes, active, other, strict=True)
                ):
                    raise invalid(
                        "Event inactive and Other coordinates require canonical null masks"
                    )
                for name in (
                    "share_of_total_delta",
                    "share_of_positive_pool",
                    "share_of_negative_pool",
                ):
                    if row[name] is not None:
                        share_value = number(name)
                        if name != "share_of_total_delta" and not 0 <= share_value <= 1:
                            raise invalid("Event pool share is outside [0, 1]")
                        key = (active, name)
                        share_sums[key] = share_sums.get(key, Fraction()) + Fraction.from_float(
                            share_value
                        )
                total_share = row["share_of_total_delta"]
                if (overall == 0 and total_share is not None) or (
                    overall != 0
                    and (
                        total_share is None
                        or not reconciles(number("share_of_total_delta"), contribution / overall)
                    )
                ):
                    raise invalid("Event total share does not reproduce its endpoint")
                if not reconciles(number("current_value") - number("baseline_value"), contribution):
                    raise invalid("Event contribution side terms do not reconcile")
                if active in endpoints and endpoints[active] != overall:
                    raise invalid("inconsistent Event resolution endpoint")
                endpoints[active] = overall
                contributions[active] = contributions.get(active, Fraction()) + Fraction.from_float(
                    contribution
                )

                def share(
                    name: str,
                    reason: Literal[
                        "zero_total_delta", "empty_positive_pool", "empty_negative_pool"
                    ],
                    row: dict[str, object] = row,
                ) -> t.FindingShareV1:
                    return _share(None if row[name] is None else number(name), reason)

                value = t.ContributionFindingValueV1(
                    method=METHOD,
                    active_axis_mask=active,
                    other_mask=other,
                    contribution_kind="loss" if kind == "loss" else "denominator_mix",
                    current_value=number("current_value"),
                    baseline_value=number("baseline_value"),
                    overall_delta=overall,
                    contribution=contribution,
                    share_of_total_delta=share("share_of_total_delta", "zero_total_delta"),
                    share_of_positive_pool=share("share_of_positive_pool", "empty_positive_pool"),
                    share_of_negative_pool=share("share_of_negative_pool", "empty_negative_pool"),
                    contribution_rank=integer("contribution_rank"),
                    status="zero_total_delta" if status == "zero_total_delta" else "ok",
                )
            from marivo.analysis.materialization.comparison_publication import _scalar

            finding = t.Finding(
                finding_id="pending",
                artifact_ref=ArtifactRef(ref=artifact_ref),
                session_id=session_ref,
                finding_type=registration.finding_type,
                epistemic_kind="algebraic",
                subject=registration.subject,
                coordinates=tuple(
                    t.FindingCoordinateV1(
                        field_id=rule.field.field_id,
                        identity=rule.field.identity,
                        value=_scalar(row[rule.field.name]),
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
            finding = replace(finding, canonical_item_key=item_key(finding))
            finding = replace(finding, finding_id=finding_identity(finding))
            eligible += 1
            findings.append(finding)

            def order(a: t.Finding, b: t.Finding) -> int:
                av, bv = a.value, b.value
                assert isinstance(
                    av, (t.ContributionFindingValueV1, t.FunnelDeltaFindingValueV1)
                ) and isinstance(bv, (t.ContributionFindingValueV1, t.FunnelDeltaFindingValueV1))
                x = (
                    av.contribution
                    if isinstance(av, t.ContributionFindingValueV1)
                    else av.loss_rate_delta
                )
                y = (
                    bv.contribution
                    if isinstance(bv, t.ContributionFindingValueV1)
                    else bv.loss_rate_delta
                )
                magnitude = -compare_value(abs(x), abs(y))
                if magnitude:
                    return magnitude
                if isinstance(av, t.ContributionFindingValueV1) and isinstance(
                    bv, t.ContributionFindingValueV1
                ):
                    resolution = compare_value(
                        registration.active_axis_masks.index(av.active_axis_mask),
                        registration.active_axis_masks.index(bv.active_axis_mask),
                    )
                    if resolution:
                        return resolution
                for left, right in zip(a.coordinates, b.coordinates, strict=True):
                    if (
                        isinstance(av, t.FunnelDeltaFindingValueV1)
                        and left.field_id == descriptor.row_contract.key_field_ids[-1]
                    ):
                        steps = (
                            tuple(step.key for step in semantics.current.journey.pattern.steps)
                            if isinstance(semantics, FunnelDeltaSemantics)
                            else ()
                        )
                        coordinate = (
                            compare_value(steps.index(av.step_key), steps.index(bv.step_key))
                            if isinstance(bv, t.FunnelDeltaFindingValueV1)
                            else 0
                        )
                    else:
                        coordinate = compare_value(left.value, right.value)
                    if coordinate:
                        return coordinate
                return compare_value(a.canonical_item_key, b.canonical_item_key)

            findings.sort(key=cmp_to_key(order))
            del findings[FINDING_CAP:]
    if any(
        not reconciles(float(values), endpoints[mask]) for mask, values in contributions.items()
    ):
        raise invalid("Event attribution resolution does not reproduce its endpoint")
    if any(not reconciles(float(total), 1.0) for total in share_sums.values()):
        raise invalid("Event attribution shares do not reconcile per resolution")
    retained = tuple(findings)
    summary = FunnelEvidenceSummary(
        row_count,
        eligible,
        len(retained),
        finding_set_digest(retained),
        tuple(sorted(statuses.items())),
        len(contributions),
    )
    descriptor = replace(descriptor, funnel_evidence=summary)
    validate_descriptor(descriptor)
    return descriptor, retained


def validate_checkpoint_inputs(
    dataset: Dataset, descriptors: Mapping[str, ArtifactDescriptor]
) -> None:
    """Require complete reducer scope when a comparison consumes aggregate checkpoints."""
    if isinstance(dataset._root, LogicalRootHandle) and isinstance(
        dataset._root.payload, FunnelComparePayload
    ):
        for operand in dataset._inputs:
            if isinstance(operand, MaterializedDataset):
                descriptor = descriptors[operand.state.artifact_ref.ref]
                if descriptor.dataset_materialization_contract.producer_id != "event.funnel":
                    raise MaterializationError(
                        expected="an unfiltered funnel checkpoint with complete comparison scope",
                        received="a filtered funnel checkpoint without pre-filter completeness authority",
                        repair="Rebuild both funnels from complete journeys, compare before filtering or materialize the unfiltered funnels, then retry.",
                        stage="authority_resolution",
                    )
    for operand in dataset._inputs:
        validate_checkpoint_inputs(operand, descriptors)
