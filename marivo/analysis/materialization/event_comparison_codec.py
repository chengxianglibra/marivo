"""Closed retained contracts for Event Delta and loss-rate Attribution."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.domains.event_attribution import FunnelAttributionSemantics
from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics, compatible
from marivo.analysis.materialization.contracts import FINDING_CAP, _array, _obj, _text, invalid
from marivo.analysis.materialization.event_codec import _count


@dataclass(frozen=True, slots=True)
class FunnelEvidenceSummary:
    row_count: int
    eligible_finding_count: int
    emitted_finding_count: int
    finding_set_digest: str
    status_counts: tuple[tuple[str, int], ...]
    resolution_count: int


def semantics_payload(
    value: FunnelDeltaSemantics | FunnelAttributionSemantics,
) -> dict[str, object]:
    if isinstance(value, FunnelDeltaSemantics):
        return {
            "kind": value.kind,
            "current_json": value.current_json,
            "baseline_json": value.baseline_json,
        }
    return {
        "kind": value.kind,
        "delta_current_json": value.delta_current_json,
        "delta_baseline_json": value.delta_baseline_json,
        "step_key": value.step_key,
        "top_k": value.top_k,
        "axis_field_ids": [f.value for f in value.axis_field_ids],
        "resolution_prefixes": [[f.value for f in prefix] for prefix in value.resolution_prefixes],
    }


def decode_semantics(value: object) -> FunnelDeltaSemantics | FunnelAttributionSemantics:
    if isinstance(value, dict) and value.get("kind") == "delta/funnel@v1":
        obj = _obj(value, "kind current_json baseline_json")
        delta = FunnelDeltaSemantics(
            _token=d._CORE_TOKEN,
            current_json=_text(obj["current_json"]),
            baseline_json=_text(obj["baseline_json"]),
        )
        compatible(delta.current, delta.baseline)
        return delta
    obj = _obj(
        value,
        "kind delta_current_json delta_baseline_json step_key top_k axis_field_ids resolution_prefixes",
    )
    if obj["kind"] != "attribution/funnel-loss-rate@v1":
        raise invalid("unknown Event comparison semantics")
    result = FunnelAttributionSemantics(
        _token=d._CORE_TOKEN,
        delta_current_json=_text(obj["delta_current_json"]),
        delta_baseline_json=_text(obj["delta_baseline_json"]),
        step_key=_text(obj["step_key"]),
        top_k=None if obj["top_k"] is None else _count(obj["top_k"]),
        axis_field_ids=tuple(d._make_field_id(_text(v)) for v in _array(obj["axis_field_ids"])),
        resolution_prefixes=tuple(
            tuple(d._make_field_id(_text(v)) for v in _array(prefix))
            for prefix in _array(obj["resolution_prefixes"])
        ),
    )
    compatible(result.delta.current, result.delta.baseline)
    return result


def evidence_payload(value: FunnelEvidenceSummary | None) -> object:
    if value is None:
        return None
    return {
        "schema": "marivo.funnel_operator_evidence/v1",
        "row_count": value.row_count,
        "eligible_finding_count": value.eligible_finding_count,
        "finding_count": value.emitted_finding_count,
        "finding_set_digest": value.finding_set_digest,
        "status_counts": [list(pair) for pair in value.status_counts],
        "resolution_count": value.resolution_count,
    }


def decode_evidence(value: object) -> FunnelEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value,
        "schema row_count eligible_finding_count finding_count finding_set_digest status_counts resolution_count",
    )
    if obj["schema"] != "marivo.funnel_operator_evidence/v1":
        raise invalid("invalid Event comparison Evidence schema")
    counts = []
    for entry in _array(obj["status_counts"]):
        pair = _array(entry)
        if len(pair) != 2:
            raise invalid("invalid Event comparison status pair")
        counts.append((_text(pair[0]), _count(pair[1])))
    result = FunnelEvidenceSummary(
        _count(obj["row_count"]),
        _count(obj["eligible_finding_count"]),
        _count(obj["finding_count"]),
        _text(obj["finding_set_digest"]),
        tuple(counts),
        _count(obj["resolution_count"]),
    )
    if (
        sum(n for _, n in counts) != result.row_count
        or result.emitted_finding_count > result.eligible_finding_count
        or result.eligible_finding_count > result.row_count
        or len({s for s, _ in counts}) != len(counts)
        or counts != sorted(counts)
        or any(n == 0 for _, n in counts)
        or result.emitted_finding_count != min(FINDING_CAP, result.eligible_finding_count)
        or len(result.finding_set_digest) != 64
        or any(c not in "0123456789abcdef" for c in result.finding_set_digest)
    ):
        raise invalid("inconsistent Event comparison Evidence totals")
    return result
