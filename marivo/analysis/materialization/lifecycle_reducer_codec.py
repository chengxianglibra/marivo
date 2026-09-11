"""Closed Lifecycle summary codecs and bounded identity-safe execution Evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.lifecycle import LifecycleSemantics
from marivo.analysis.domains.lifecycle_reducers import (
    REDUCER_TYPES,
    DistributionSemantics,
    DwellSemantics,
    ReducerSemantics,
    TransitionsSemantics,
    ViolationsSemantics,
    check_at,
    history_semantics,
)
from marivo.analysis.materialization.contracts import _array, _obj, _text, invalid
from marivo.analysis.materialization.event_codec import coverage_payload, decode_coverage
from marivo.analysis.materialization.lifecycle_codec import decode_semantics as decode_history

if TYPE_CHECKING:
    from marivo.analysis.materialization.contracts import ArtifactDescriptor


def decode_semantics(value: object) -> ReducerSemantics:
    if not isinstance(value, dict):
        raise invalid("invalid Lifecycle reducer semantics")
    kind = value.get("kind")
    result: ReducerSemantics
    if kind == "lifecycle/distribution@v1":
        obj = _obj(value, "kind history_json at axis_refs axis_dependency_fingerprints")
        result = DistributionSemantics(
            _token=d._CORE_TOKEN,
            history_json=_text(obj["history_json"]),
            at=tuple(_text(v) for v in _array(obj["at"])),
            axis_refs=tuple(_text(v) for v in _array(obj["axis_refs"])),
            axis_dependency_fingerprints=tuple(
                _text(v) for v in _array(obj["axis_dependency_fingerprints"])
            ),
        )
    elif kind == "lifecycle/dwell@v1":
        obj = _obj(value, "kind history_json estimand")
        result = DwellSemantics(_token=d._CORE_TOKEN, history_json=_text(obj["history_json"]))
        if obj["estimand"] != result.estimand:
            raise invalid("changed Lifecycle fragment estimand")
    elif kind in ("lifecycle/transitions@v1", "lifecycle/violations@v1"):
        obj = _obj(value, "kind history_json")
        result = (
            TransitionsSemantics(_token=d._CORE_TOKEN, history_json=_text(obj["history_json"]))
            if kind == "lifecycle/transitions@v1"
            else ViolationsSemantics(_token=d._CORE_TOKEN, history_json=_text(obj["history_json"]))
        )
    else:
        raise invalid("unregistered Lifecycle reducer shape")
    try:
        decode_history(json.loads(result.history_json))
    except (ValueError, TypeError, DatasetConstructionError):
        raise invalid("invalid retained Lifecycle reducer model authority") from None
    return result


@dataclass(frozen=True, slots=True)
class LifecycleReducerEvidence:
    kind: Literal["lifecycle_reducer"]
    coverage: EventCoverageResolution
    row_count: int
    semantics: ReducerSemantics
    totals: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class LifecycleSelectionEvidence:
    kind: Literal["lifecycle_selection"]
    coverage: EventCoverageResolution
    row_count: int
    input_subject_count: int
    selected_subject_count: int
    history: LifecycleSemantics
    state: str
    at: str
    input_definition: str


ContinuationEvidence = LifecycleReducerEvidence | LifecycleSelectionEvidence
TOTALS = {
    "lifecycle/distribution@v1": ("subject_count",),
    "lifecycle/transitions@v1": ("transition_count",),
    "lifecycle/dwell@v1": (
        "interval_count",
        "completed_count",
        "right_censored_count",
        "coverage_censored_count",
        "left_clipped_completed_count",
    ),
    "lifecycle/violations@v1": (),
}


def count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise invalid("invalid bounded Lifecycle summary count")
    return value


def evidence_payload(value: ContinuationEvidence) -> object:
    return json.loads(json.dumps({**asdict(value), "coverage": coverage_payload(value.coverage)}))


def decode_evidence(value: object) -> ContinuationEvidence:
    if not isinstance(value, dict):
        raise invalid("invalid Lifecycle continuation Evidence")
    if value.get("kind") == "lifecycle_reducer":
        obj = _obj(value, "kind coverage row_count semantics totals")
        semantics = decode_semantics(obj["semantics"])
        totals: list[tuple[str, int]] = []
        for entry in _array(obj["totals"]):
            pair = _array(entry)
            if len(pair) != 2:
                raise invalid("invalid Lifecycle summary arity")
            totals.append((_text(pair[0]), count(pair[1])))
        if tuple(n for n, _ in totals) != TOTALS[semantics.kind]:
            raise invalid("changed Lifecycle summary fields")
        return LifecycleReducerEvidence(
            "lifecycle_reducer",
            decode_coverage(obj["coverage"]),
            count(obj["row_count"]),
            semantics,
            tuple(totals),
        )
    obj = _obj(
        value,
        "kind coverage row_count input_subject_count selected_subject_count history state at input_definition",
    )
    if obj["kind"] != "lifecycle_selection":
        raise invalid("unknown Lifecycle continuation Evidence")
    result = LifecycleSelectionEvidence(
        "lifecycle_selection",
        decode_coverage(obj["coverage"]),
        count(obj["row_count"]),
        count(obj["input_subject_count"]),
        count(obj["selected_subject_count"]),
        decode_history(obj["history"]),
        _text(obj["state"]),
        _text(obj["at"]),
        _text(obj["input_definition"]),
    )
    if (
        result.state not in result.history.states
        or not result.row_count <= result.selected_subject_count <= result.input_subject_count
    ):
        raise invalid("invalid complete Lifecycle selection counts or state")
    try:
        check_at(result.history, datetime.fromisoformat(result.at))
    except (ValueError, TypeError, DatasetConstructionError):
        raise invalid("invalid retained Lifecycle selection checkpoint") from None
    return result


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    value = descriptor.lifecycle_evidence
    if not isinstance(value, (LifecycleReducerEvidence, LifecycleSelectionEvidence)):
        raise invalid("missing Lifecycle continuation Evidence")
    checked = decode_evidence(evidence_payload(value))
    if checked.row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("Lifecycle continuation row count differs")
    if any(p.role != "population_sampling_state" for p in descriptor.retained_parts):
        raise invalid("unregistered Lifecycle continuation parts")
    if (
        descriptor.dataset_materialization_contract.finding_extractor_id != "none"
        or descriptor.dataset_materialization_contract.finding_policy_id != "zero_findings@v1"
    ):
        raise invalid("Lifecycle continuation must have zero Findings")
    if isinstance(checked, LifecycleReducerEvidence):
        if (
            not isinstance(descriptor.row_contract.family_semantics, REDUCER_TYPES)
            or descriptor.row_contract.family_semantics != checked.semantics
        ):
            raise invalid("Lifecycle reducer row meaning differs from Evidence")
        history = history_semantics(checked.semantics)
    else:
        if str(descriptor.row_contract.shape_id) != "population/entity-membership@v1":
            raise invalid("Lifecycle selection Evidence requires Population rows")
        history = checked.history
    authority = descriptor.population_authority
    if (
        authority.entity_ref != history.source.subject_entity_ref
        or authority.identity_signature != history.source.subject_identity_signature
    ):
        raise invalid("Lifecycle continuation subject identity differs")
    expected = tuple(
        dict.fromkeys(
            zip(
                (s.event.key for s in history.source.pattern.steps),
                history.source.step_event_fingerprints,
                history.source.source_origins,
                strict=True,
            )
        )
    )
    if (
        tuple(
            (f.event_ref, f.event_fingerprint, f.source_origin_ref) for f in checked.coverage.events
        )
        != expected
    ):
        raise invalid("Lifecycle continuation coverage differs from retained trigger authority")
