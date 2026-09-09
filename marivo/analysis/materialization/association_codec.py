"""Closed Association family and bounded Evidence codecs."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization.contracts import (
    FINDING_CAP,
    _array,
    _hash,
    _int,
    _obj,
    _text,
    invalid,
)
from marivo.analysis.operators.association_contracts import (
    MAX_CANDIDATES,
    SELECTION_RULE_ID,
    STATUSES,
    AssociationSemantics,
    PairApproximationBinding,
)
from marivo.semantic._quantile import decode_approximation


@dataclass(frozen=True, slots=True)
class AssociationEvidenceSummary:
    row_count: int
    status_counts: tuple[tuple[str, int], ...]
    emitted_finding_count: int
    finding_set_digest: str
    searched_pair_count: int
    searched_lag_count: int
    searched_series_count: int
    original_candidate_count: int
    complete_pair_range: tuple[int, int]
    null_pair_range: tuple[int, int]
    selection_rule_id: str
    pair_approximation_bindings: tuple[PairApproximationBinding, ...]
    eligible_finding_count: int
    finding_truncated: bool


def semantics_payload(value: AssociationSemantics) -> dict[str, object]:
    return {
        "kind": value.kind,
        "method": value.method,
        "input_shape": value.input_shape,
        "metric_keys": value.metric_keys,
        "metric_units": value.metric_units,
        "approximations": value.approximations,
        "lag_offsets": value.lag_offsets,
        "fold_authority": value.fold_authority,
    }


def decode_semantics(value: object) -> AssociationSemantics:
    obj = _obj(
        value,
        "kind method input_shape metric_keys metric_units approximations lag_offsets fold_authority",
    )
    method = obj["method"]
    if obj["kind"] != "association/metric@v1" or method not in ("pearson", "spearman", "kendall"):
        raise invalid("invalid Association method")
    lags = tuple(_int(k, minimum=-(2**63)) for k in _array(obj["lag_offsets"]))
    return AssociationSemantics(
        _token=d._CORE_TOKEN,
        method="pearson"
        if method == "pearson"
        else "spearman"
        if method == "spearman"
        else "kendall",
        input_shape=_text(obj["input_shape"]),
        metric_keys=tuple(_text(k) for k in _array(obj["metric_keys"])),
        metric_units=tuple(None if u is None else _text(u) for u in _array(obj["metric_units"])),
        approximations=tuple(_text(a) for a in _array(obj["approximations"])),
        lag_offsets=lags,
        fold_authority=_text(obj["fold_authority"]),
    )


def evidence_payload(value: AssociationEvidenceSummary | None) -> object:
    if value is None:
        return None
    from dataclasses import asdict

    return {"schema": "marivo.association_evidence/v1", **asdict(value)}


def decode_evidence(value: object) -> AssociationEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value,
        "schema row_count status_counts emitted_finding_count finding_set_digest searched_pair_count searched_lag_count searched_series_count original_candidate_count complete_pair_range null_pair_range selection_rule_id pair_approximation_bindings eligible_finding_count finding_truncated",
    )
    if obj["schema"] != "marivo.association_evidence/v1":
        raise invalid("invalid Association Evidence schema")
    if obj["selection_rule_id"] != SELECTION_RULE_ID or type(obj["finding_truncated"]) is not bool:
        raise invalid("invalid Association selection or Finding disclosure")
    bindings = []
    for item in _array(obj["pair_approximation_bindings"]):
        binding = _obj(item, "metric_key_a metric_key_b approximation_a approximation_b")
        try:
            approximation_a = decode_approximation(binding["approximation_a"])
            approximation_b = decode_approximation(binding["approximation_b"])
        except ValueError:
            raise invalid("invalid Association pair approximation") from None
        bindings.append(
            PairApproximationBinding(
                _text(binding["metric_key_a"]),
                _text(binding["metric_key_b"]),
                approximation_a,
                approximation_b,
            )
        )
    counts = []
    for item in _array(obj["status_counts"]):
        pair = _array(item)
        if len(pair) != 2:
            raise invalid("invalid Association status count")
        counts.append((_text(pair[0]), _int(pair[1])))
    ranges = []
    for name in ("complete_pair_range", "null_pair_range"):
        bounds = _array(obj[name])
        if len(bounds) != 2:
            raise invalid("invalid Association pair range")
        lo, hi = _int(bounds[0]), _int(bounds[1])
        if lo > hi:
            raise invalid("reversed Association count range")
        ranges.append((lo, hi))
    result = AssociationEvidenceSummary(
        _int(obj["row_count"]),
        tuple(counts),
        _int(obj["emitted_finding_count"]),
        _text(obj["finding_set_digest"]),
        _int(obj["searched_pair_count"], minimum=1),
        _int(obj["searched_lag_count"], minimum=1),
        _int(obj["searched_series_count"]),
        _int(obj["original_candidate_count"]),
        ranges[0],
        ranges[1],
        SELECTION_RULE_ID,
        tuple(bindings),
        _int(obj["eligible_finding_count"]),
        obj["finding_truncated"] is True,
    )
    _hash(result.finding_set_digest)
    if (
        tuple(k for k, _ in counts) != STATUSES
        or sum(n for _, n in counts) != result.row_count
        or result.row_count > MAX_CANDIDATES
        or result.original_candidate_count > MAX_CANDIDATES
        or result.row_count > result.original_candidate_count
        or result.original_candidate_count
        != result.searched_pair_count * result.searched_lag_count * result.searched_series_count
        or len(bindings) != result.searched_pair_count
        or result.eligible_finding_count != dict(counts)["valid"]
        or result.emitted_finding_count != min(FINDING_CAP, result.eligible_finding_count)
        or result.finding_truncated
        != (result.eligible_finding_count > result.emitted_finding_count)
    ):
        raise invalid("inconsistent Association Evidence counts")
    return result
