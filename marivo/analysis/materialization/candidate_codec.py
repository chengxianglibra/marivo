"""Closed Candidate row meaning and bounded original search Evidence codecs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence._dataset_codec import finding_set_digest
from marivo.analysis.materialization.contracts import _array, _int, _obj, _text, invalid
from marivo.analysis.operators.candidate_contracts import (
    METHODS,
    REASON_CODES,
    CandidateDefinition,
    CandidateEvaluationSummary,
    CandidateObjective,
    CandidateSemantics,
    EntityCandidateEvaluationSummary,
)


@dataclass(frozen=True, slots=True)
class CandidateEvidenceSummary:
    row_count: int
    emitted_finding_count: int
    finding_set_digest: str
    definition: CandidateDefinition
    evaluation: CandidateEvaluationSummary | EntityCandidateEvaluationSummary


def semantics_payload(value: CandidateSemantics) -> dict[str, object]:
    return {
        "kind": value.kind,
        "objective": value.objective,
        "method_id": value.method_id,
        "approximation": value.approximation,
        "item_id_field_id": value.item_id_field_id.value,
        "score_field_id": value.score_field_id.value,
        "reason_codes_field_id": value.reason_codes_field_id.value,
    }


def _objective(value: object) -> CandidateObjective:
    result = _text(value)
    for objective in METHODS:
        if objective == result:
            return objective
    raise invalid("unregistered Candidate objective")


def decode_semantics(value: object) -> CandidateSemantics:
    obj = _obj(
        value,
        "kind objective method_id approximation item_id_field_id score_field_id reason_codes_field_id",
    )
    objective = _objective(obj["objective"])
    method = _text(obj["method_id"])
    if obj["kind"] != "candidate/discovery@v1" or method != METHODS[objective]:
        raise invalid("inconsistent Candidate objective and method")
    return CandidateSemantics(
        _token=d._CORE_TOKEN,
        objective=objective,
        method_id=METHODS[objective],
        approximation=_text(obj["approximation"]),
        item_id_field_id=d._make_field_id(_text(obj["item_id_field_id"])),
        score_field_id=d._make_field_id(_text(obj["score_field_id"])),
        reason_codes_field_id=d._make_field_id(_text(obj["reason_codes_field_id"])),
    )


def _finite(value: object) -> float:
    if type(value) is not int and type(value) is not float:
        raise invalid("invalid Candidate finite number")
    try:
        number = float(value)
    except OverflowError:
        raise invalid("invalid Candidate finite number") from None
    if not math.isfinite(number):
        raise invalid("invalid Candidate finite number")
    return number


def _range(value: object) -> tuple[float, float] | None:
    if value is None:
        return None
    pair = _array(value)
    if len(pair) != 2:
        raise invalid("invalid Candidate range")
    lo, hi = _finite(pair[0]), _finite(pair[1])
    if lo > hi:
        raise invalid("reversed Candidate range")
    return lo, hi


def _decode_definition(value: object) -> CandidateDefinition:
    obj = _obj(
        value,
        "objective method_id input_state_kind input_authority threshold limit approximation fold_authority baseline_fold_authority metric_key metric_unit",
    )
    objective = _objective(obj["objective"])
    state = obj["input_state_kind"]
    if state not in ("logical", "materialized"):
        raise invalid("invalid Candidate input authority kind")
    method = _text(obj["method_id"])
    if method != METHODS[objective]:
        raise invalid("invalid Candidate method")
    return CandidateDefinition(
        objective=objective,
        method_id=METHODS[objective],
        input_state_kind="logical" if state == "logical" else "materialized",
        input_authority=_text(obj["input_authority"]),
        threshold=_finite(obj["threshold"]),
        limit=_int(obj["limit"], minimum=1),
        approximation=_text(obj["approximation"]),
        fold_authority=_text(obj["fold_authority"]),
        baseline_fold_authority=None
        if obj["baseline_fold_authority"] is None
        else _text(obj["baseline_fold_authority"]),
        metric_key=_text(obj["metric_key"]),
        metric_unit=None if obj["metric_unit"] is None else _text(obj["metric_unit"]),
    )


def _decode_evaluation(value: object) -> CandidateEvaluationSummary:
    obj = _obj(
        value,
        "input_row_count series_count evaluated_series_count insufficient_series_count constant_series_count unavailable_series_count searched_unit_count evaluated_unit_count pre_limit_candidate_count emitted_candidate_count score_range reason_counts baseline_mean_range baseline_stddev_range window_size_range",
    )
    reasons = []
    for item in _array(obj["reason_counts"]):
        pair = _array(item)
        if len(pair) != 2:
            raise invalid("invalid Candidate reason count")
        reasons.append((_text(pair[0]), _int(pair[1])))
    windows: tuple[int, int] | None = None
    if obj["window_size_range"] is not None:
        pair = _array(obj["window_size_range"])
        if len(pair) != 2:
            raise invalid("invalid Candidate window size range")
        windows = (_int(pair[0], minimum=7), _int(pair[1], minimum=7))
        if windows[0] > windows[1]:
            raise invalid("reversed Candidate window size range")
    return CandidateEvaluationSummary(
        input_row_count=_int(obj["input_row_count"]),
        series_count=_int(obj["series_count"]),
        evaluated_series_count=_int(obj["evaluated_series_count"]),
        insufficient_series_count=_int(obj["insufficient_series_count"]),
        constant_series_count=_int(obj["constant_series_count"]),
        unavailable_series_count=_int(obj["unavailable_series_count"]),
        searched_unit_count=_int(obj["searched_unit_count"]),
        evaluated_unit_count=_int(obj["evaluated_unit_count"]),
        pre_limit_candidate_count=_int(obj["pre_limit_candidate_count"]),
        emitted_candidate_count=_int(obj["emitted_candidate_count"]),
        score_range=_range(obj["score_range"]),
        reason_counts=tuple(reasons),
        baseline_mean_range=_range(obj["baseline_mean_range"]),
        baseline_stddev_range=_range(obj["baseline_stddev_range"]),
        window_size_range=windows,
    )


def reason_code(objective: str) -> str:
    return REASON_CODES[_objective(objective)]


def _decode_entity_evaluation(value: object) -> EntityCandidateEvaluationSummary:
    obj = _obj(
        value,
        "input_row_count non_null_value_count null_value_count center scale scale_method pre_limit_candidate_count emitted_candidate_count score_range reason_counts",
    )
    method = obj["scale_method"]
    if method not in ("mad", "mean_absolute_deviation"):
        raise invalid("unregistered Entity Candidate scale method")
    reasons = []
    for item in _array(obj["reason_counts"]):
        pair = _array(item)
        if len(pair) != 2:
            raise invalid("invalid Entity Candidate reason count")
        reasons.append((_text(pair[0]), _int(pair[1])))
    return EntityCandidateEvaluationSummary(
        input_row_count=_int(obj["input_row_count"]),
        non_null_value_count=_int(obj["non_null_value_count"]),
        null_value_count=_int(obj["null_value_count"]),
        center=_finite(obj["center"]),
        scale=_finite(obj["scale"]),
        scale_method="mad" if method == "mad" else "mean_absolute_deviation",
        pre_limit_candidate_count=_int(obj["pre_limit_candidate_count"]),
        emitted_candidate_count=_int(obj["emitted_candidate_count"]),
        score_range=_range(obj["score_range"]),
        reason_counts=tuple(reasons),
    )


def _validate_entity_evidence(
    value: CandidateEvidenceSummary, evaluation: EntityCandidateEvaluationSummary
) -> None:
    definition = value.definition
    for count in (
        value.row_count,
        value.emitted_finding_count,
        evaluation.input_row_count,
        evaluation.non_null_value_count,
        evaluation.null_value_count,
        evaluation.pre_limit_candidate_count,
        evaluation.emitted_candidate_count,
    ):
        _int(count)
    _finite(evaluation.center)
    _finite(evaluation.scale)
    if (
        definition.objective != "entity_outliers"
        or value.emitted_finding_count != 0
        or value.finding_set_digest != finding_set_digest(())
        or evaluation.non_null_value_count < 3
        or evaluation.input_row_count
        != evaluation.non_null_value_count + evaluation.null_value_count
        or evaluation.scale <= 0
        or evaluation.scale_method not in ("mad", "mean_absolute_deviation")
        or evaluation.pre_limit_candidate_count > evaluation.non_null_value_count
        or evaluation.emitted_candidate_count
        != min(definition.limit, evaluation.pre_limit_candidate_count)
        or value.row_count > evaluation.emitted_candidate_count
        or evaluation.reason_counts
        != ((reason_code(definition.objective), evaluation.pre_limit_candidate_count),)
    ):
        raise invalid("inconsistent Entity Candidate original evaluation")
    bounds = evaluation.score_range
    if (bounds is None) != (evaluation.pre_limit_candidate_count == 0) or (
        bounds is not None
        and (
            len(bounds) != 2
            or any(not math.isfinite(_finite(number)) for number in bounds)
            or not definition.threshold <= bounds[0] <= bounds[1]
        )
    ):
        raise invalid("inconsistent Entity Candidate original score range")


def validate_evidence(value: CandidateEvidenceSummary) -> None:
    from marivo.analysis.operators.discovery import validate_definition

    definition, evaluation = value.definition, value.evaluation
    validate_definition(definition)
    if isinstance(evaluation, EntityCandidateEvaluationSummary):
        _validate_entity_evidence(value, evaluation)
        return
    if definition.objective == "entity_outliers":
        raise invalid("Entity Candidate requires its exact median and scale evaluation")
    counts = (
        value.row_count,
        value.emitted_finding_count,
        evaluation.input_row_count,
        evaluation.series_count,
        evaluation.evaluated_series_count,
        evaluation.insufficient_series_count,
        evaluation.constant_series_count,
        evaluation.unavailable_series_count,
        evaluation.searched_unit_count,
        evaluation.evaluated_unit_count,
        evaluation.pre_limit_candidate_count,
        evaluation.emitted_candidate_count,
    )
    for count in counts:
        _int(count)
    if value.emitted_finding_count != 0 or value.finding_set_digest != finding_set_digest(()):
        raise invalid("Candidate publication must contain zero Findings")
    if (
        evaluation.evaluated_series_count < 1
        or evaluation.series_count > evaluation.input_row_count
        or evaluation.series_count
        != evaluation.evaluated_series_count
        + evaluation.insufficient_series_count
        + evaluation.constant_series_count
        + evaluation.unavailable_series_count
        or not 0 < evaluation.evaluated_unit_count <= evaluation.searched_unit_count
        or evaluation.searched_unit_count > evaluation.input_row_count
        or evaluation.pre_limit_candidate_count > evaluation.evaluated_unit_count
        or evaluation.emitted_candidate_count
        != min(definition.limit, evaluation.pre_limit_candidate_count)
        or value.row_count > evaluation.emitted_candidate_count
    ):
        raise invalid("inconsistent Candidate original evaluation counts")
    for bounds in (
        evaluation.score_range,
        evaluation.baseline_mean_range,
        evaluation.baseline_stddev_range,
    ):
        if bounds is not None and (
            len(bounds) != 2 or not all(math.isfinite(v) for v in bounds) or bounds[0] > bounds[1]
        ):
            raise invalid("invalid Candidate scoring range")
    if (
        evaluation.baseline_mean_range is None
        or evaluation.baseline_stddev_range is None
        or evaluation.baseline_stddev_range[0] <= 0
        or (evaluation.score_range is None) != (evaluation.pre_limit_candidate_count == 0)
        or (evaluation.score_range is not None and evaluation.score_range[0] < definition.threshold)
        or evaluation.reason_counts
        != ((reason_code(definition.objective), evaluation.pre_limit_candidate_count),)
    ):
        raise invalid("inconsistent Candidate scoring summary")
    windows = evaluation.window_size_range
    if definition.objective == "period_shifts":
        if (
            windows is None
            or type(windows[0]) is not int
            or type(windows[1]) is not int
            or not 7 <= windows[0] <= windows[1] <= max(7, evaluation.input_row_count // 10)
        ):
            raise invalid("inconsistent Candidate trailing window summary")
    elif windows is not None:
        raise invalid("unexpected Candidate trailing window summary")


def evidence_payload(value: CandidateEvidenceSummary | None) -> object:
    return None if value is None else {"schema": "marivo.candidate_evidence/v1", **asdict(value)}


def decode_evidence(value: object) -> CandidateEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value, "schema row_count emitted_finding_count finding_set_digest definition evaluation"
    )
    if obj["schema"] != "marivo.candidate_evidence/v1":
        raise invalid("invalid Candidate Evidence schema")
    definition = _decode_definition(obj["definition"])
    result = CandidateEvidenceSummary(
        row_count=_int(obj["row_count"]),
        emitted_finding_count=_int(obj["emitted_finding_count"]),
        finding_set_digest=_text(obj["finding_set_digest"]),
        definition=definition,
        evaluation=_decode_entity_evaluation(obj["evaluation"])
        if definition.objective == "entity_outliers"
        else _decode_evaluation(obj["evaluation"]),
    )
    validate_evidence(result)
    return result
