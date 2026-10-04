"""Closed graph quantity subjects and original-scope statistical Finding transport."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from marivo.analysis.core.model import (
    DerivedQuantity,
    ObservedQuantity,
    Quantity,
    RolledQuantity,
    RowStatisticQuantity,
)
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import finding_identity
from marivo.analysis.materialization.deviation_execution import load
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import Descriptor, digest, invalid
from marivo.analysis.materialization.statistical_execution import (
    decode_forecast,
    decode_pairs,
)
from marivo.analysis.refs import ArtifactRef
from marivo.refs import Ref, RefPayloadV1

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_findings import Policy


def quantity(value: Quantity, typ: str) -> t.GraphQuantityV1:
    if isinstance(value, ObservedQuantity):
        metric = (
            t.CatalogGraphMetricV1(metric=RefPayloadV1.from_ref(value.metric_ref))
            if isinstance(value.metric_ref, Ref)
            else t.RuntimeGraphMetricV1(expression_identity=digest(repr(value.metric_ref)))
        )
        return t.ObservedGraphQuantityV1(
            quantity_identity=value.definition_id,
            definition_fingerprint=digest(repr(value)),
            unit=value.unit,
            value_type=typ,
            approximation_identity=value.method_version,
            metric=metric,
            graph_fingerprint=value.graph_fingerprint,
            contribution_identity=value.contribution_id,
        )
    if isinstance(value, DerivedQuantity):
        return t.DerivedGraphQuantityV1(
            quantity_identity=value.definition_id,
            definition_fingerprint=digest(repr(value)),
            unit=value.unit,
            value_type=typ,
            approximation_identity=value.method_version,
            method=value.method_version,
            ordered_input_identities=value.input_ids,
        )
    if isinstance(value, RowStatisticQuantity):
        return t.RowStatisticGraphQuantityV1(
            quantity_identity=value.definition_id,
            definition_fingerprint=digest(repr(value)),
            unit=value.unit,
            value_type=typ,
            approximation_identity=value.method_version,
            method=value.method_version,
            input_identity=value.input_id,
            input_domain=value.input_domain_id,
            weighting=value.weighting,
        )
    assert isinstance(value, RolledQuantity)
    return t.RolledGraphQuantityV1(
        quantity_identity=value.definition_id,
        definition_fingerprint=digest(repr(value)),
        unit=value.unit,
        value_type=typ,
        approximation_identity=value.method_version,
        original_identity=value.original_id,
        contribution_identity=value.contribution_id,
    )


def coordinate(name: str, value: str | int) -> t.FindingCoordinateV1:
    field = d._make_field_id("graph.statistics." + name)
    return t.FindingCoordinateV1(field_id=field, identity=d._generated_identity(field), value=value)


def extract(
    descriptor: Descriptor,
    result: ExchangeResult,
    artifact_ref: str,
    committed_at: datetime,
    policy: Policy,
) -> tuple[t.Finding, ...]:
    """Rebind the original capped bodies, including after current-output selection."""
    from marivo.analysis.materialization.graph_findings import CAP

    association = policy.producer.startswith("association.")
    if association:
        captured, state = decode_pairs(result.parts)
        declaration = captured.declaration
        candidates = sorted(
            (c for c in state.candidates if c.score.status == "valid"),
            key=lambda c: (-abs(c.score.coefficient or 0), c.a, c.b, c.series, c.lag, c.key),
        )
        eligible = len(candidates)
        input_digest = state.input_digest
        scope = declaration.association_id
    else:
        captured_f, state_f = decode_forecast(result.parts)
        eligible = len(state_f.series) * len(captured_f.future.grid.cells)
        input_digest = state_f.input_digest
        scope = captured_f.declaration.forecast_id
    expected_extractor = (
        "graph.association_findings@v1" if association else "graph.forecast_findings@v1"
    )
    expected_policy = (
        "bounded_descriptive_findings@v1" if association else "bounded_prediction_findings@v1"
    )
    if (
        policy.extractor != expected_extractor
        or policy.policy != expected_policy
        or policy.ordered_input_bindings != ("capture:" + input_digest,)
        or (policy.eligible, policy.emitted, policy.truncated)
        != (eligible, min(CAP, eligible), max(0, eligible - CAP))
    ):
        raise invalid("statistical Finding producer/input/scope/count differs")
    derivation = t.GraphFindingDerivationV1(
        producer_id=policy.producer + "@v1",
        state_contract_version="v1",
        extractor_contract_id=expected_extractor.removesuffix("@v1"),
        extractor_contract_version="v1",
        policy_contract_id=expected_policy.removesuffix("@v1"),
        policy_contract_version="v1",
        ordered_input_bindings=policy.ordered_input_bindings,
        definition_fingerprint=descriptor.definition_fingerprint,
        scope_id=scope,
    )
    findings: list[t.Finding] = []
    if association:
        for candidate in candidates[:CAP]:
            assert candidate.score.coefficient is not None
            subject = t.AssociationFindingSubjectV2(
                quantity_a=quantity(
                    declaration.quantities[candidate.a], declaration.input_types[candidate.a]
                ),
                quantity_b=quantity(
                    declaration.quantities[candidate.b], declaration.input_types[candidate.b]
                ),
            )
            lag = (
                t.NoAssociationLagV1()
                if not declaration.explicit_lag
                else t.AssociationLagV1(
                    lag_offset=candidate.lag,
                    selected_for_pair=candidate.selected,
                    matched_observation_count=candidate.matched,
                    lag_boundary_drop_count=candidate.boundary_drop,
                )
            )
            value = t.AssociationFindingValueV1(
                method=declaration.method,
                coefficient=candidate.score.coefficient,
                input_observation_count=candidate.input_count,
                null_pair_count=candidate.null_pairs,
                complete_pair_count=candidate.complete_pairs,
                lag=lag,
            )
            finding = t.Finding(
                finding_id="finding_pending",
                artifact_ref=ArtifactRef(ref=artifact_ref),
                session_id=descriptor.signature.domain.binding.session_id,
                finding_type="association",
                epistemic_kind="estimated",
                subject=subject,
                coordinates=(
                    coordinate("pair_a", candidate.a),
                    coordinate("pair_b", candidate.b),
                    coordinate("series", candidate.series),
                    coordinate("lag", candidate.lag),
                ),
                canonical_item_key=candidate.key,
                value=value,
                derivation=derivation,
                committed_at=committed_at,
            )
            findings.append(replace(finding, finding_id="finding_" + finding_identity(finding)))
    else:
        original = captured_f.declaration
        rows = sorted(
            load(state_f.views).to_pylist(),
            key=lambda r: tuple(r[k] for k in result.contract.key_fields),
        )
        subject_f = t.ForecastFindingSubjectV2(
            quantity=quantity(original.quantity, original.input_type)
        )
        for row in rows[:CAP]:
            training = next(s.training for s in state_f.series if s.identity == row["series"])
            value_f = t.ForecastPointFindingValueV1(
                model="naive@v1"
                if original.model == "naive"
                else "drift@v1"
                if original.model == "drift"
                else "seasonal_naive@v1",
                interval_level=original.level,
                horizon_ordinal=row["horizon"],
                forecast_value=row["prediction"],
                interval_lower=row["lower"],
                interval_upper=row["upper"],
                training_row_count=training.n,
            )
            key = digest(repr(tuple(row[k] for k in result.contract.key_fields)))
            finding = t.Finding(
                finding_id="finding_pending",
                artifact_ref=ArtifactRef(ref=artifact_ref),
                session_id=descriptor.signature.domain.binding.session_id,
                finding_type="forecast_point",
                epistemic_kind="predicted",
                subject=subject_f,
                coordinates=(
                    coordinate("series", row["series"]),
                    coordinate("horizon", row["horizon"]),
                    coordinate(
                        "time_cell", captured_f.future.grid.cells[row["horizon"] - 1].identity
                    ),
                ),
                canonical_item_key=key,
                value=value_f,
                derivation=derivation,
                committed_at=committed_at,
            )
            findings.append(replace(finding, finding_id="finding_" + finding_identity(finding)))
    return tuple(findings)
