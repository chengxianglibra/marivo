"""Closed Attribution row authority and bounded complete-resolution Evidence."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics

from marivo.analysis.materialization.contracts import (
    AttributionEvidenceSummary,
    _array,
    _hash,
    _int,
    _obj,
    _text,
    invalid,
)


def attribution_evidence_payload(value: AttributionEvidenceSummary | None) -> object:
    if value is None:
        return None
    return {
        "schema": "marivo.attribution_evidence/v1",
        "method": value.method,
        "origin_definition_fingerprint": value.origin_definition_fingerprint,
        "complete_row_count": value.complete_row_count,
        "scope_count": value.scope_count,
        "resolution_count": value.resolution_count,
        "mapped_membership_digest": value.mapped_membership_digest,
        "mapped_membership_contract_id": "complete_scope_resolution_rows@v1",
        "max_reconciliation_error": value.max_reconciliation_error,
        "status_counts": value.status_counts,
        "approximate": value.approximate,
        "complete": value.complete,
        "top_k": value.top_k,
        "eligible_finding_count": value.eligible_finding_count,
        "emitted_finding_count": value.emitted_finding_count,
        "finding_truncated": value.finding_truncated,
        "finding_set_digest": value.finding_set_digest,
    }


def decode_attribution_evidence(value: object) -> AttributionEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value,
        "schema method origin_definition_fingerprint complete_row_count scope_count resolution_count mapped_membership_digest mapped_membership_contract_id max_reconciliation_error status_counts approximate complete top_k eligible_finding_count emitted_finding_count finding_truncated finding_set_digest",
    )
    if obj["schema"] != "marivo.attribution_evidence/v1" or obj["method"] not in (
        "additive_difference@v1",
        "component_mix@v1",
    ):
        raise invalid("unregistered Attribution Evidence method or schema")
    if obj["mapped_membership_contract_id"] != "complete_scope_resolution_rows@v1":
        raise invalid("unregistered Attribution mapped membership digest contract")
    origin = _text(obj["origin_definition_fingerprint"])
    if re.fullmatch(r"ds_[0-9a-f]{64}", origin) is None:
        raise invalid("invalid Attribution proof origin")
    error = obj["max_reconciliation_error"]
    if type(error) is not float or not math.isfinite(error) or error < 0:
        raise invalid("invalid Attribution reconciliation maximum")
    for name in ("approximate", "complete", "finding_truncated"):
        if type(obj[name]) is not bool:
            raise invalid("invalid Attribution Evidence boolean")
    pairs = tuple(_array(item) for item in _array(obj["status_counts"]))
    if (
        len(pairs) != 2
        or any(len(pair) != 2 for pair in pairs)
        or tuple(pair[0] for pair in pairs) != ("ok", "zero_total_delta")
    ):
        raise invalid("invalid Attribution status counts")
    counts = tuple((_text(pair[0]), _int(pair[1])) for pair in pairs)
    top_k = None if obj["top_k"] is None else _int(obj["top_k"], minimum=1)
    if top_k is not None and top_k > 1000:
        raise invalid("invalid Attribution Top-K bound")
    result = AttributionEvidenceSummary(
        _text(obj["method"]),
        origin,
        _int(obj["complete_row_count"]),
        _int(obj["scope_count"]),
        _int(obj["resolution_count"]),
        _text(obj["mapped_membership_digest"]),
        error,
        counts,
        obj["approximate"] is True,
        obj["complete"] is True,
        top_k,
        _int(obj["eligible_finding_count"]),
        _int(obj["emitted_finding_count"]),
        obj["finding_truncated"] is True,
        _text(obj["finding_set_digest"]),
    )
    _hash(result.mapped_membership_digest)
    _hash(result.finding_set_digest)
    if (
        sum(count for _, count in counts) != result.complete_row_count
        or result.scope_count > result.resolution_count
        or result.resolution_count > result.complete_row_count
        or result.eligible_finding_count > result.complete_row_count
        or result.emitted_finding_count != min(1000, result.eligible_finding_count)
        or result.finding_truncated != (result.eligible_finding_count > 1000)
        or (not result.complete and result.eligible_finding_count != 0)
    ):
        raise invalid("inconsistent Attribution Evidence counts")
    return result


def attribution_semantics_payload(value: object) -> dict[str, object]:
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics

    if not isinstance(value, AttributionSemantics):
        raise invalid("missing exact Attribution row semantics")
    return {
        "kind": value.kind,
        "metric_ref": value.metric_ref,
        "metric_unit": value.metric_unit,
        "numeric_type": value.numeric_type,
        "scope_field_ids": [item.value for item in value.scope_field_ids],
        "axis_field_ids": [item.value for item in value.axis_field_ids],
        "resolution_prefixes": [
            [item.value for item in prefix] for prefix in value.resolution_prefixes
        ],
        "current_time_field_name": value.current_time_field_name,
        "baseline_time_field_name": value.baseline_time_field_name,
        "method": value.method,
        "approximation_class": value.approximation_class,
        "resolution_semantics": value.resolution_semantics,
        "rollup_safe": value.rollup_safe,
    }


def decode_attribution_semantics(value: object) -> AttributionSemantics:
    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics

    obj = _obj(
        value,
        "kind metric_ref metric_unit numeric_type scope_field_ids axis_field_ids resolution_prefixes current_time_field_name baseline_time_field_name method approximation_class resolution_semantics rollup_safe",
    )
    if (
        obj["kind"] != "attribution/metric@v1"
        or obj["resolution_semantics"] != "rollup"
        or obj["rollup_safe"] is not True
    ):
        raise invalid("unregistered Attribution resolution semantics")
    if obj["method"] not in ("additive_difference@v1", "component_mix@v1"):
        raise invalid("unregistered Attribution method")
    if obj["approximation_class"] not in ("exact", "sampled_population"):
        raise invalid("unregistered Attribution approximation class")
    return AttributionSemantics(
        _token=d._CORE_TOKEN,
        metric_ref=_text(obj["metric_ref"]),
        metric_unit=None if obj["metric_unit"] is None else _text(obj["metric_unit"]),
        numeric_type=_text(obj["numeric_type"]),
        scope_field_ids=tuple(
            d._make_field_id(_text(item)) for item in _array(obj["scope_field_ids"])
        ),
        axis_field_ids=tuple(
            d._make_field_id(_text(item)) for item in _array(obj["axis_field_ids"])
        ),
        resolution_prefixes=tuple(
            tuple(d._make_field_id(_text(item)) for item in _array(prefix))
            for prefix in _array(obj["resolution_prefixes"])
        ),
        current_time_field_name=None
        if obj["current_time_field_name"] is None
        else _text(obj["current_time_field_name"]),
        baseline_time_field_name=None
        if obj["baseline_time_field_name"] is None
        else _text(obj["baseline_time_field_name"]),
        method="additive_difference@v1"
        if obj["method"] == "additive_difference@v1"
        else "component_mix@v1",
        approximation_class="exact"
        if obj["approximation_class"] == "exact"
        else "sampled_population",
    )
