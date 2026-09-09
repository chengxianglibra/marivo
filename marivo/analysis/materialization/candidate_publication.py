"""Candidate publication proofs retain original search authority and emit no Findings."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime

import pyarrow as pa

from marivo.analysis.datasets.descriptors import _EntityFieldIdentity
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import finding_set_digest
from marivo.analysis.materialization.candidate_codec import (
    CandidateEvidenceSummary,
    reason_code,
    validate_evidence,
)
from marivo.analysis.materialization.contracts import ArtifactDescriptor, _int, invalid
from marivo.analysis.operators.candidate_contracts import (
    CandidateDefinition,
    CandidateEvaluationSummary,
    CandidateSemantics,
    EntityCandidateEvaluationSummary,
)
from marivo.analysis.operators.row_values import compare_value, row_key_names


def _evidence(descriptor: ArtifactDescriptor) -> CandidateEvidenceSummary:
    result = descriptor.candidate_evidence
    if result is None:
        raise invalid("missing original Candidate search authority")
    return result


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    """Check original evaluation authority without reading or replaying source rows."""
    from marivo.analysis.operators.discovery import validate_candidate

    validate_candidate(descriptor.row_contract, descriptor.row_set_contract)
    meaning = descriptor.row_contract.family_semantics
    if not isinstance(meaning, CandidateSemantics):
        raise invalid("missing Candidate objective semantics")
    evidence = _evidence(descriptor)
    validate_evidence(evidence)
    definition = evidence.definition
    if definition.objective == "entity_outliers":
        identities = tuple(
            field.identity
            for field in descriptor.row_contract.schema.columns
            if isinstance(field.identity, _EntityFieldIdentity)
        )
        population = descriptor.population_authority
        if (
            len(identities) != 1
            or identities[0].entity_ref.path != population.entity_ref
            or identities[0].identity_signature != population.identity_signature
        ):
            raise invalid("Entity Candidate identity contradicts its membership authority")
        if ("candidate.entity_output", 0) not in population.validation_results:
            raise invalid("Entity Candidate lacks its native output validation")
    if (
        meaning.objective != definition.objective
        or meaning.method_id != definition.method_id
        or meaning.approximation != definition.approximation
        or evidence.row_count != descriptor.storage_receipt.realized_row_count
    ):
        raise invalid("Candidate row meaning contradicts original search authority")
    producer = descriptor.dataset_materialization_contract.producer_id
    if producer.startswith("discover.") and (
        producer != "discover." + definition.objective
        or evidence.row_count != evidence.evaluation.emitted_candidate_count
    ):
        raise invalid("incomplete original Candidate discovery output")
    if (
        any(part.contract_id != "population_sampling_state" for part in descriptor.retained_parts)
        or descriptor.comparison_inputs
        or descriptor.delta_evidence is not None
        or descriptor.attribution_evidence is not None
        or descriptor.association_evidence is not None
        or descriptor.forecast_evidence is not None
        or descriptor.dataset_materialization_contract.finding_extractor_id != "none"
    ):
        raise invalid("unregistered Candidate retained inputs or Finding extractor")


def _float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise invalid("invalid finite Candidate generated float")
    return value


def _within(value: float, bounds: tuple[float, float] | None) -> bool:
    return bounds is not None and bounds[0] <= value <= bounds[1]


def validate_row(descriptor: ArtifactDescriptor, values: Mapping[str, object]) -> None:
    """Validate one retained Candidate row against its exact original definition."""
    from marivo.analysis.operators.candidate_values import candidate_item_id

    evidence = _evidence(descriptor)
    definition, evaluation = evidence.definition, evidence.evaluation
    if isinstance(evaluation, EntityCandidateEvaluationSummary):
        raise invalid("Entity Candidate rows require their registered native validation")
    if set(values) != {field.name for field in descriptor.realized_schema.columns}:
        raise invalid("Candidate row does not match its complete output schema")
    for field in descriptor.realized_schema.columns:
        value = values[field.name]
        if value is None and not field.nullable:
            raise invalid("Candidate row contains an unavailable required field")
        if field.logical_type_id == "date" and value is not None and type(value) is not date:
            raise invalid("Candidate date coordinate changed its exact scalar type")
        if (
            field.logical_type_id == "timestamp"
            and value is not None
            and not isinstance(value, datetime)
        ):
            raise invalid("Candidate timestamp coordinate changed its exact scalar type")
    reasons = values["reason_codes"]
    if not isinstance(reasons, (list, tuple)) or tuple(reasons) != (
        reason_code(definition.objective),
    ):
        raise invalid("Candidate reason vocabulary contradicts objective")
    score = _float(values["score"])
    if score < definition.threshold or not _within(score, evaluation.score_range):
        raise invalid("Candidate score contradicts original threshold or search range")
    if values["direction"] not in ("high", "low"):
        raise invalid("invalid Candidate direction")
    item = values["item_id"]
    if type(item) is not str or item != candidate_item_id(
        definition, descriptor.row_contract, values
    ):
        raise invalid("Candidate item digest contradicts original typed coordinates or authority")
    if definition.objective == "point_anomalies":
        observed = _float(values["observed_value"])
        baseline = _float(values["baseline_value"])
        deviation = _float(values["signed_deviation"])
        difference = observed - baseline
        if not math.isfinite(difference) or difference != deviation:
            raise invalid("Candidate signed deviation contradicts observed minus baseline")
        if (
            not _within(baseline, evaluation.baseline_mean_range)
            or ("high" if deviation > 0 else "low") != values["direction"]
        ):
            raise invalid("Candidate baseline or direction contradicts original evaluation")
        stddev = evaluation.baseline_stddev_range
        assert stddev is not None
        # A bounded range proves consistency without persisting per-series samples.
        implied = abs(deviation) / score
        tolerance = max(math.ulp(implied), math.ulp(stddev[0]), math.ulp(stddev[1])) * 4
        if not stddev[0] - tolerance <= implied <= stddev[1] + tolerance:
            raise invalid("Candidate z-score contradicts original baseline deviation")
        return
    for start, end in (("window_start", "window_end"), ("baseline_start", "baseline_end")):
        if (
            values[start] is None
            or values[end] is None
            or compare_value(values[start], values[end]) > 0
        ):
            raise invalid("Candidate window endpoints are absent or reversed")
    if _float(values["peak_absolute_zscore"]) != score:
        raise invalid("Candidate peak score contradicts ranking score")
    if definition.objective == "interesting_windows":
        count = _int(values["point_count"], minimum=1)
        if count > evaluation.evaluated_unit_count or (
            compare_value(values["baseline_start"], values["window_start"]) > 0
            or compare_value(values["window_end"], values["baseline_end"]) > 0
        ):
            raise invalid("Candidate run exceeds original baseline or evaluated points")
    else:
        size = _int(values["window_size"], minimum=7)
        bounds = evaluation.window_size_range
        if bounds is None or not bounds[0] <= size <= bounds[1]:
            raise invalid("Candidate trailing window size contradicts original evaluation")


def _order(left: Mapping[str, object], right: Mapping[str, object], keys: tuple[str, ...]) -> int:
    compared = compare_value(right["score"], left["score"])
    if compared:
        return compared
    for name in (*keys, "item_id"):
        compared = compare_value(left[name], right[name])
        if compared:
            return compared
    return 0


def validate_rows(descriptor: ArtifactDescriptor, batches: Iterable[pa.RecordBatch]) -> None:
    """Check complete stored rows, key uniqueness and original deterministic order."""
    from marivo.analysis.operators.candidate_values import candidate_item_id

    validate_descriptor(descriptor)
    evidence = _evidence(descriptor)
    if isinstance(evidence.evaluation, EntityCandidateEvaluationSummary):
        raise invalid("Entity Candidate validation cannot collect identity rows")
    keys = row_key_names(descriptor.row_contract)
    seen: set[str] = set()
    previous: Mapping[str, object] | None = None
    scores: list[float] = []
    producing = descriptor.dataset_materialization_contract.producer_id.startswith("discover.")
    for batch in batches:
        for raw in batch.to_pylist():
            values: dict[str, object] = {str(key): value for key, value in raw.items()}
            validate_row(descriptor, values)
            # The digest is recomputed from the complete typed business key, so its
            # uniqueness checks both repeated keys and retained digest contradictions.
            item = candidate_item_id(evidence.definition, descriptor.row_contract, values)
            if item in seen:
                raise invalid("duplicate Candidate business coordinate or item digest")
            seen.add(item)
            if producing and previous is not None and _order(previous, values, keys) > 0:
                raise invalid("Candidate output violates deterministic discovery ordering")
            previous = values
            scores.append(_float(values["score"]))
    if len(seen) != evidence.row_count:
        raise invalid("incomplete Candidate publication scan")
    if producing and scores:
        bounds = evidence.evaluation.score_range
        if bounds is None or max(scores) != bounds[1]:
            raise invalid("Candidate output omits the strongest original score")
        if (
            evidence.evaluation.pre_limit_candidate_count == len(scores)
            and min(scores) != bounds[0]
        ):
            raise invalid("Candidate output contradicts complete original scoring range")


def build_candidate_publication(
    descriptor: ArtifactDescriptor,
    batches: Iterable[pa.RecordBatch] | None,
    *,
    artifact_ref: str,
    session_ref: str,
    definition: CandidateDefinition | None,
    evaluation: CandidateEvaluationSummary | EntityCandidateEvaluationSummary | None,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    """Build one atomic Candidate Evidence result; Candidates are never Findings."""
    if definition is None or evaluation is None:
        raise invalid("missing original Candidate definition or evaluation proof")
    published = replace(
        descriptor,
        candidate_evidence=CandidateEvidenceSummary(
            row_count=descriptor.storage_receipt.realized_row_count,
            emitted_finding_count=0,
            finding_set_digest=finding_set_digest(()),
            definition=definition,
            evaluation=evaluation,
        ),
    )
    if isinstance(evaluation, EntityCandidateEvaluationSummary):
        if batches is not None:
            raise invalid("Entity Candidate publication requires native identity-safe proof")
        validate_descriptor(published)
    else:
        if batches is None:
            raise invalid("time Candidate publication requires complete validated rows")
        validate_rows(published, batches)
    return published, ()
