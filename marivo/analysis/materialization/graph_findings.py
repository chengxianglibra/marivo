"""Frozen graph Finding extractors, policy authority and common collection reads."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.core.graph import FixedLeaf, MethodNode, topology
from marivo.analysis.core.model import FindingPolicyPart, FunnelAllocationPart, FunnelComparisonPart
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import (
    decode_finding_body,
    finding_identity,
    finding_set_digest,
)
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
)
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    Descriptor,
    ValidatedDescriptor,
    checked_metadata,
    digest,
    encode,
    invalid,
)
from marivo.analysis.materialization.graph_snapshot import FixedRecord, MethodRecord
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.methods import funnel as f
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, ref

if TYPE_CHECKING:
    from marivo.analysis.materialization.store import SessionStore

CAP = 1000


def _integer(value: object) -> int:
    if type(value) is not int:
        raise invalid("Finding component lacks an exact integer")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise invalid("Finding component lacks a finite numeric carrier")
    return float(value)


@dataclass(frozen=True, slots=True)
class Policy:
    producer: Literal[
        "funnel.compare",
        "funnel_ratio_mix",
        "deviation.zscore",
        "deviation.mad",
        "time.runs",
        "association.pearson",
        "association.spearman",
        "association.kendall",
        "forecast.naive",
        "forecast.drift",
        "forecast.seasonal_naive",
    ]
    state_version: Literal["v1"]
    extractor: Literal[
        "graph.funnel_delta_findings@v1",
        "graph.funnel_contribution_findings@v1",
        "graph.no_findings@v1",
        "graph.association_findings@v1",
        "graph.forecast_findings@v1",
    ]
    policy: Literal[
        "bounded_algebraic_findings@v1",
        "zero_findings@v1",
        "bounded_descriptive_findings@v1",
        "bounded_prediction_findings@v1",
    ]
    ordered_input_bindings: tuple[str, ...]
    eligible: int
    emitted: int
    truncated: int
    version: Literal["v1"] = "v1"


AXIS: TypeAdapter[f.Axis] = TypeAdapter(f.Axis)
POLICY = TypeAdapter(Policy)


def _eligible(primary: pa.Table, producer: str) -> list[dict[str, object]]:
    if producer.startswith("deviation.") or producer == "time.runs":
        return []
    rows = [r for r in primary.to_pylist() if r["cell_tag"] == "defined"]
    keys = tuple(k for k in primary.column_names if k.startswith("key_"))

    def ordering(row: dict[str, object]) -> tuple[tuple[tuple[int, int | str], ...], ...]:
        values = []
        for key in keys:
            value = row[key]
            if key == "key_0":
                values.append(f.axis_key((_integer(value),)))
            elif type(value) is int:
                values.append(f.axis_key((value,)))
            elif value in ("loss", "denominator_mix"):
                values.append(f.axis_key((str(value),)))
            else:
                values.append(f.axis_key((AXIS.validate_json(str(value)),)))
        return tuple(values)

    rows.sort(
        key=lambda r: (
            -abs(_number(r["value"] if producer == "funnel.compare" else r["contribution"])),
            ordering(r),
        )
    )
    return rows


def policy_part(
    node: MethodNode,
    primary: pa.Table,
    state: f.FunnelState | f.ComparisonState | f.AllocationState | f.EntryAxisState,
) -> ExchangePart:
    declaration = next(p for p in node.signature.parts if isinstance(p, FindingPolicyPart))
    fixed = tuple(
        tuple(n.artifact.ref for n in topology(edge.node) if isinstance(n, FixedLeaf))
        for edge in node.inputs
    )
    if any(fixed):
        inputs = tuple("artifacts:" + encode_versions(identities) for identities in fixed)
    elif isinstance(state, f.ComparisonState):
        inputs = tuple(
            "capture:" + digest(f.FUNNEL_STATE.dump_json(endpoint).decode())
            for endpoint in (state.current, state.baseline)
        )
    else:
        assert isinstance(state, f.AllocationState)
        inputs = tuple(
            "capture:" + digest(f.COMPARISON_STATE.dump_json(endpoint).decode())
            for endpoint in (state.original, state.expanded)
        )
    eligible = len(_eligible(primary, declaration.producer))
    value = Policy(
        declaration.producer,
        "v1",
        declaration.extractor,
        declaration.policy,
        inputs,
        eligible,
        min(CAP, eligible),
        max(eligible - CAP, 0),
    )
    return ExchangePart(
        "finding_policy", pa.table({"finding_policy__retained": [POLICY.dump_json(value).decode()]})
    )


def _policy(parts: tuple[ExchangePart, ...]) -> Policy:
    payload = next(p.table for p in parts if p.role == "finding_policy")[
        "finding_policy__retained"
    ][0].as_py()
    try:
        policy = POLICY.validate_json(payload, strict=True)
    except ValidationError:
        raise invalid("invalid Finding policy encoding") from None
    if (
        POLICY.dump_json(policy).decode() != payload
        or policy.emitted != min(CAP, policy.eligible)
        or policy.truncated != policy.eligible - policy.emitted
        or policy.eligible < 0
        or not policy.ordered_input_bindings
    ):
        raise invalid("Finding policy count or canonical encoding differs")
    return policy


def validate_policy(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...], state: object
) -> None:
    policy = _policy(parts)
    declaration = next(p for p in contract.signature.parts if isinstance(p, FindingPolicyPart))
    if (policy.producer, policy.extractor, policy.policy) != (
        declaration.producer,
        declaration.extractor,
        declaration.policy,
    ) or policy.eligible != len(_eligible(primary, policy.producer)):
        raise invalid("Finding extractor/version/count differs from frozen producer")
    if all(value.startswith("capture:") for value in policy.ordered_input_bindings):
        if isinstance(state, f.ComparisonState):
            expected = tuple(
                "capture:" + digest(f.FUNNEL_STATE.dump_json(endpoint).decode())
                for endpoint in (state.current, state.baseline)
            )
        elif isinstance(state, f.AllocationState):
            expected = tuple(
                "capture:" + digest(f.COMPARISON_STATE.dump_json(endpoint).decode())
                for endpoint in (state.original, state.expanded)
            )
        else:
            raise invalid("Finding policy is attached to an ineligible producer")
        if policy.ordered_input_bindings != expected:
            raise invalid("Finding ordered capture bindings differ")


def versions(result: ExchangeResult) -> tuple[str, ...]:
    return (
        (_policy(result.parts).extractor,)
        if any(p.role == "finding_policy" for p in result.parts)
        else ("graph.no_findings@v1",)
    )


def _share(
    value: object, reason: Literal["zero_total_delta", "empty_positive_pool", "empty_negative_pool"]
) -> t.FindingShareV1:
    return (
        t.UndefinedFindingShareV1(reason=reason)
        if value is None
        else t.DefinedFindingRatioV1(value=_number(value))
    )


def extract(
    descriptor: Descriptor,
    result: ExchangeResult,
    artifact_ref: str,
    committed_at: datetime,
    *,
    _validated: ValidatedDescriptor | None = None,
) -> tuple[t.Finding, ...]:
    if not any(p.role == "finding_policy" for p in result.parts):
        return ()
    policy = _policy(result.parts)
    if policy.policy == "zero_findings@v1":
        if (
            policy.producer not in ("deviation.zscore", "deviation.mad", "time.runs")
            or policy.extractor != "graph.no_findings@v1"
            or (policy.eligible, policy.emitted, policy.truncated) != (0, 0, 0)
        ):
            raise invalid("deviation empty Finding authority differs")
        return ()
    if policy.producer.startswith(("association.", "forecast.")):
        from marivo.analysis.materialization.statistical_findings import (
            extract as extract_statistics,
        )

        return extract_statistics(descriptor, result, artifact_ref, committed_at, policy)
    if any(value.startswith("artifacts:") for value in policy.ordered_input_bindings):
        checked = checked_metadata(descriptor, _validated)
        records = {record.identity: record for record in checked.nodes}

        def fixed_refs(identity: str) -> tuple[str, ...]:
            seen: set[str] = set()
            refs: list[str] = []

            def visit(current: str) -> None:
                if current in seen:
                    return
                seen.add(current)
                record = records[current]
                if isinstance(record, FixedRecord):
                    refs.append(record.artifact.ref)
                elif isinstance(record, MethodRecord):
                    for edge in record.inputs:
                        visit(edge.node)
                    for source in record.sources:
                        visit(source)

            visit(identity)
            return tuple(refs)

        expected_bindings = tuple(
            "artifacts:" + encode_versions(fixed_refs(edge.node)) for edge in checked.root.inputs
        )
        if expected_bindings != policy.ordered_input_bindings:
            raise invalid("Finding ordered Artifact bindings differ from frozen inputs")
    declaration = next(
        p
        for p in result.contract.signature.parts
        if isinstance(p, (FunnelComparisonPart, FunnelAllocationPart))
    )
    funnel = (
        declaration.current
        if isinstance(declaration, FunnelComparisonPart)
        else declaration.comparison.current
    )
    axes = tuple(a.dimension.ref.path for a in funnel.axes)
    subject = t.FunnelFindingSubjectV1(
        subject_entity_ref=RefPayloadV1.from_ref(
            ref.entity(funnel.journey.preparation.events[0].subject.ref.path)
        ),
        pattern_fingerprint=digest(repr(funnel.journey.steps)),
    )
    derivation = t.GraphFindingDerivationV1(
        producer_id=policy.producer + "@v1",
        state_contract_version=policy.state_version,
        extractor_contract_id=policy.extractor.removesuffix("@v1"),
        extractor_contract_version="v1",
        policy_contract_id=policy.policy.removesuffix("@v1"),
        policy_contract_version="v1",
        ordered_input_bindings=policy.ordered_input_bindings,
        definition_fingerprint=descriptor.definition_fingerprint,
        scope_id=result.contract.signature.domain.binding.scope_id,
    )
    findings = []
    for row in _eligible(result.primary, policy.producer)[:CAP]:
        check()
        coordinates = []
        first_id = d._make_field_id(
            "graph.funnel.step"
            if isinstance(declaration, FunnelComparisonPart)
            else "graph.funnel.resolution"
        )
        coordinates.append(
            t.FindingCoordinateV1(
                field_id=first_id,
                identity=d._generated_identity(first_id),
                value=_integer(row["key_0"]),
            )
        )
        for i, axis in enumerate(axes):
            field_id = d._make_field_id("graph.funnel.axis." + axis)
            coordinates.append(
                t.FindingCoordinateV1(
                    field_id=field_id,
                    identity=d._catalog_identity("dimension:" + axis),
                    value=TypeAdapter(f.Axis).validate_json(str(row[f"key_{i + 1}"]), strict=True),
                )
            )
        if isinstance(declaration, FunnelComparisonPart):
            value: t.FindingValueV1 = t.FunnelDeltaFindingValueV1(
                step_key=funnel.journey.steps[_integer(row["key_0"])],
                coordinate_presence=TypeAdapter(t.CoordinatePresence).validate_python(
                    row["presence"], strict=True
                ),
                current_cohort_count=_integer(row["current_cohort_count"]),
                baseline_cohort_count=_integer(row["baseline_cohort_count"]),
                current_resolved_cohort_count=_integer(row["current_resolved_cohort_count"]),
                baseline_resolved_cohort_count=_integer(row["baseline_resolved_cohort_count"]),
                current_entry_count=_integer(row["current_entry_count"]),
                baseline_entry_count=_integer(row["baseline_entry_count"]),
                current_resolved_entry_count=_integer(row["current_resolved_entry_count"]),
                baseline_resolved_entry_count=_integer(row["baseline_resolved_entry_count"]),
                current_reached_count=_integer(row["current_reached_count"]),
                baseline_reached_count=_integer(row["baseline_reached_count"]),
                current_lost_count=_integer(row["current_lost_count"]),
                baseline_lost_count=_integer(row["baseline_lost_count"]),
                current_coverage_censored_count=_integer(row["current_coverage_censored_count"]),
                baseline_coverage_censored_count=_integer(row["baseline_coverage_censored_count"]),
                current_loss_rate_from_previous=_number(row["current_loss_rate_from_previous"]),
                baseline_loss_rate_from_previous=_number(row["baseline_loss_rate_from_previous"]),
                loss_rate_delta=_number(row["loss_rate_delta"]),
            )
            finding_type: t.FindingType = "funnel_delta"
        else:
            value = t.ContributionFindingValueV1(
                method="funnel_ratio_mix@v1",
                active_axis_mask=tuple(i < _integer(row["resolution"]) for i in range(len(axes))),
                other_mask=tuple(
                    bool(_integer(row[f"key_{len(axes) + 1}"]) & (1 << i)) for i in range(len(axes))
                ),
                contribution_kind=TypeAdapter(Literal["loss", "denominator_mix"]).validate_python(
                    row["kind"], strict=True
                ),
                current_value=_number(row["current"]),
                baseline_value=_number(row["baseline"]),
                overall_delta=_number(row["target"]),
                contribution=_number(row["contribution"]),
                share_of_total_delta=_share(row["share_total"], "zero_total_delta"),
                share_of_positive_pool=_share(row["share_positive"], "empty_positive_pool"),
                share_of_negative_pool=_share(row["share_negative"], "empty_negative_pool"),
                contribution_rank=_integer(row["rank"]),
                status=TypeAdapter(Literal["ok", "zero_total_delta"]).validate_python(
                    row["status"], strict=True
                ),
            )
            finding_type = "contribution"
        key = digest(encode_versions(tuple(str(row[k]) for k in result.contract.key_fields)))
        finding = t.Finding(
            finding_id="finding_pending",
            artifact_ref=ArtifactRef(ref=artifact_ref),
            session_id=descriptor.signature.domain.binding.session_id,
            finding_type=finding_type,
            epistemic_kind="algebraic",
            subject=subject,
            coordinates=tuple(coordinates),
            canonical_item_key=key,
            value=value,
            derivation=derivation,
            committed_at=committed_at,
        )
        findings.append(replace(finding, finding_id="finding_" + finding_identity(finding)))
    if len(findings) != policy.emitted:
        raise invalid("extractor emitted count differs from retained authority")
    return tuple(findings)


def collection(
    store: SessionStore,
    conn: sqlite3.Connection,
    descriptor: Descriptor,
    artifact_ref: str,
    *,
    verify_receipts: bool = True,
    _validated: ValidatedDescriptor | None = None,
) -> tuple[tuple[t.Finding, ...], t.ArtifactDigest]:
    artifact = conn.execute(
        "SELECT committed_at FROM dataset_artifacts WHERE artifact_ref=?", (artifact_ref,)
    ).fetchone()
    evidence = conn.execute(
        "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (artifact_ref,)
    ).fetchone()
    if artifact is None or evidence is None:
        raise invalid("Artifact lacks atomic Evidence")
    committed_at = datetime.fromisoformat(artifact[0])
    if verify_receipts:
        result = read_result(store.project_root, descriptor, _validated=_validated)
        expected = extract(descriptor, result, artifact_ref, committed_at, _validated=_validated)
    else:
        if any(isinstance(p, FindingPolicyPart) for p in descriptor.signature.parts):
            raise invalid("Finding producer requires receipt verification")
        expected = ()
    rows = conn.execute(
        "SELECT * FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal", (artifact_ref,)
    ).fetchall()
    actual = []
    for ordinal, row in enumerate(rows):
        check()
        if row["finding_ordinal"] != ordinal:
            raise invalid("Finding ordered collection has a gap or duplicate")
        finding = decode_finding_body(
            row["finding_body_payload"],
            finding_id=row["finding_ref"],
            artifact_ref=artifact_ref,
            session_id=descriptor.signature.domain.binding.session_id,
            committed_at=committed_at,
        )
        if finding_identity(finding) != row[
            "finding_identity_digest"
        ] or finding.finding_id != "finding_" + finding_identity(finding):
            raise invalid("Finding identity or body binding differs")
        actual.append(finding)
    findings = tuple(actual)
    version = versions(result) if verify_receipts else ("graph.no_findings@v1",)
    if (
        findings != expected
        or evidence["finding_count"] != len(expected)
        or evidence["finding_set_digest"] != finding_set_digest(expected)
        or evidence["extractor_contract_versions_payload"] != encode_versions(version)
        or evidence["evidence_digest"] != digest(encode(descriptor, DESCRIPTOR))
    ):
        raise invalid("Finding collection, body, version, count or digest differs")
    return findings, t.ArtifactDigest(
        artifact_ref=ArtifactRef(ref=artifact_ref),
        quality_summary_digest=digest(encode(descriptor, DESCRIPTOR)),
        typed_issue_digest=digest("[]"),
        evidence_digest=evidence["evidence_digest"],
        finding_count=len(findings),
        finding_set_digest=finding_set_digest(findings),
        extractor_contract_versions=version,
    )


def encode_versions(value: tuple[str, ...]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def page(
    findings: tuple[t.Finding, ...], artifact: str, limit: int, cursor: str | None
) -> t.FindingPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise invalid("Finding page limit must be an exact integer in 1..100")
    start = 0
    if cursor is not None:
        try:
            position, owner = decode_keyset_cursor(cursor)
        except (TypeError, ValueError):
            raise invalid("Finding cursor is malformed") from None
        if type(position) is not int or owner != artifact or not 0 <= position < len(findings):
            raise invalid("Finding cursor belongs to a different Artifact or collection")
        start = position + 1
    items = findings[start : start + limit]
    more = start + limit < len(findings)
    return t.FindingPage(
        items, limit, more, encode_keyset_cursor(start + limit - 1, artifact) if more else None
    )
