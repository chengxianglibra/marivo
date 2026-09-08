"""Closed retained comparison authority and bounded family Evidence codecs."""

from __future__ import annotations

import re
from dataclasses import replace

from marivo.analysis.materialization.contracts import (
    ComparisonInputAuthority,
    DeltaEvidenceSummary,
    PopulationAuthority,
    _array,
    _hash,
    _int,
    _obj,
    _scope,
    _selection,
    _signature,
    _text,
    _texts,
    _validation_results,
    decode_sampling,
    invalid,
    sampling_payload,
)


def population_payload(value: PopulationAuthority) -> dict[str, object]:
    return {
        "definition_fingerprint": value.definition_fingerprint,
        "entity_ref": value.entity_ref,
        "identity_signature": value.identity_signature,
        "membership_scope": value.membership_scope,
        "version_selection": value.version_selection,
        "validation_results": value.validation_results,
    }


def decode_population(value: object) -> PopulationAuthority:
    obj = _obj(
        value,
        "definition_fingerprint entity_ref identity_signature membership_scope version_selection validation_results",
    )
    definition = _text(obj["definition_fingerprint"])
    if re.fullmatch(r"ds_[0-9a-f]{64}", definition) is None:
        raise invalid("invalid comparison Population definition")
    return PopulationAuthority(
        definition,
        _text(obj["entity_ref"]),
        _signature(obj["identity_signature"]),
        _scope(obj["membership_scope"]),
        _selection(obj["version_selection"]),
        _validation_results(obj["validation_results"]),
    )


def comparison_inputs_payload(
    values: tuple[ComparisonInputAuthority, ...],
) -> list[dict[str, object]]:
    return [
        {
            "role": item.role,
            "definition_fingerprint": item.definition_fingerprint,
            "population_authority": population_payload(item.population_authority),
            "sampling_execution": sampling_payload(item.sampling_execution),
            "source_artifact_refs": item.source_artifact_refs,
            "comparison_basis": item.comparison_basis,
        }
        for item in values
    ]


def decode_comparison_inputs(value: object) -> tuple[ComparisonInputAuthority, ...]:
    from marivo.analysis.operators.contracts import decode_comparison_basis

    items = _array(value)
    if not items:
        return ()
    if len(items) != 2:
        raise invalid("comparison requires exactly two ordered operand authorities")
    result: list[ComparisonInputAuthority] = []
    for raw, role in zip(items, ("current", "baseline"), strict=True):
        obj = _obj(
            raw,
            "role definition_fingerprint population_authority sampling_execution source_artifact_refs comparison_basis",
        )
        if obj["role"] != role:
            raise invalid("comparison operand authority role mismatch")
        definition = _text(obj["definition_fingerprint"])
        if re.fullmatch(r"ds_[0-9a-f]{64}", definition) is None:
            raise invalid("invalid comparison operand definition")
        basis = comparison_basis_text(obj["comparison_basis"])
        decoded_basis = decode_comparison_basis(basis)
        population = decode_population(obj["population_authority"])
        if decoded_basis.membership_digest != population.definition_fingerprint:
            raise invalid("comparison compatibility snapshot contradicts its selected Population")
        result.append(
            ComparisonInputAuthority(
                "current" if role == "current" else "baseline",
                definition,
                population,
                decode_sampling(obj["sampling_execution"]),
                _texts(obj["source_artifact_refs"]),
                basis,
            )
        )
    current_basis = decode_comparison_basis(result[0].comparison_basis)
    baseline_basis = decode_comparison_basis(result[1].comparison_basis)
    if current_basis.model_copy(update={"observation_scope": None}) != baseline_basis.model_copy(
        update={"observation_scope": None}
    ):
        raise invalid("incompatible retained comparison operand selection")
    if replace(result[0].population_authority, validation_results=()) != replace(
        result[1].population_authority, validation_results=()
    ):
        raise invalid("comparison operand Population authorities contradict their compatibility")
    return tuple(result)


def comparison_basis_text(value: object) -> str:
    """Bound the complete typed compatibility snapshot independently of labels."""
    from marivo.analysis.operators.contracts import decode_comparison_basis

    if type(value) is not str:
        raise invalid("invalid comparison compatibility snapshot")
    decode_comparison_basis(value)
    return value


def delta_evidence_payload(value: DeltaEvidenceSummary | None) -> object:
    if value is None:
        return None
    return {
        "schema": "marivo.delta_evidence/v1",
        "coordinate_presence_counts": value.coordinate_presence_counts,
        "calculation_status_counts": value.calculation_status_counts,
        "relative_status_counts": value.relative_status_counts,
        "matched_count": value.matched_count,
        "unpaired_count": value.unpaired_count,
        "numeric_promotion_id": value.numeric_promotion_id,
        "approximate": value.approximate,
        "eligible_finding_count": value.eligible_finding_count,
        "emitted_finding_count": value.emitted_finding_count,
        "finding_truncated": value.finding_truncated,
        "finding_set_digest": value.finding_set_digest,
    }


def _counts(value: object, names: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    pairs = tuple(_array(item) for item in _array(value))
    if len(pairs) != len(names) or any(len(pair) != 2 for pair in pairs):
        raise invalid("incomplete Delta Evidence status counts")
    if tuple(pair[0] for pair in pairs) != names:
        raise invalid("unregistered Delta Evidence status")
    return tuple((name, _int(pair[1])) for name, pair in zip(names, pairs, strict=True))


def decode_delta_evidence(value: object) -> DeltaEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value,
        "schema coordinate_presence_counts calculation_status_counts relative_status_counts matched_count unpaired_count numeric_promotion_id approximate eligible_finding_count emitted_finding_count finding_truncated finding_set_digest",
    )
    if obj["schema"] != "marivo.delta_evidence/v1":
        raise invalid("unregistered Delta Evidence schema")
    presence = _counts(
        obj["coordinate_presence_counts"], ("matched", "current_only", "baseline_only")
    )
    calculation = _counts(obj["calculation_status_counts"], ("ok", "null_input", "missing_side"))
    relative = _counts(obj["relative_status_counts"], ("ok", "baseline_zero", "delta_unavailable"))
    approximate = obj["approximate"]
    truncated = obj["finding_truncated"]
    if type(approximate) is not bool or type(truncated) is not bool:
        raise invalid("invalid Delta Evidence boolean")
    result = DeltaEvidenceSummary(
        presence,
        calculation,
        relative,
        _int(obj["matched_count"]),
        _int(obj["unpaired_count"]),
        _text(obj["numeric_promotion_id"]),
        approximate,
        _int(obj["eligible_finding_count"]),
        _int(obj["emitted_finding_count"]),
        truncated,
        _text(obj["finding_set_digest"]),
    )
    _hash(result.finding_set_digest)
    total = sum(count for _, count in presence)
    if (
        total != sum(count for _, count in calculation)
        or total != sum(count for _, count in relative)
        or result.matched_count != presence[0][1]
        or result.unpaired_count != presence[1][1] + presence[2][1]
        or result.eligible_finding_count > calculation[0][1]
        or result.emitted_finding_count != min(result.eligible_finding_count, 1000)
        or result.finding_truncated != (result.eligible_finding_count > 1000)
    ):
        raise invalid("inconsistent Delta Evidence counts")
    return result
