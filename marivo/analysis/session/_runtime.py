"""Process-local session state and runtime helpers for the session facade.

This module owns:
- The process-level current session pointer (``_CURRENT_SESSION``).
- ``current()`` which resolves the current session from process state or
  the persisted store pointer.
- ``require_current_session()`` for callers that need a live session.
- ``_build_connection_runtime`` and ``_build_semantic_catalog`` which are
  runtime-only and must not be persisted.
- ``_session_from_row`` which builds a live ``Session`` from store metadata
  plus a runtime connection runtime.
- ``persist_frame`` and ``persist_job_record`` which combine layout I/O
  with store registration.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, cast

from ibis.backends import BaseBackend

from marivo._compat import UTC
from marivo.analysis.errors import NoActiveSessionError, SessionStateError
from marivo.analysis.session._layout import (
    PersistenceLayout,
    write_frame_to_disk,
)
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import Session
from marivo.analysis.timezone import ResolvedTimezone, resolve_system_timezone, zoneinfo_from_name
from marivo.telemetry import staged

if TYPE_CHECKING:
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.session._connections import AnalysisConnectionRuntime

from marivo.analysis.frames.base import BaseFrameMeta
from marivo.refs import RefPayloadV1, SemanticKind, _decode_ref_payload

# ---------------------------------------------------------------------------
# Process-level current session
# ---------------------------------------------------------------------------

_CURRENT_SESSION: Session | None = None


def _require_exact_object(value: object, *, fields: set[str], role: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"analysis job {role} must contain exactly {sorted(fields)}")
    return value


def _validate_metric_identity_payload(value: object, *, role: str) -> None:
    if type(value) is not dict:
        raise ValueError(f"analysis job {role} must be an object")
    kind = value.get("kind")
    if kind == "catalog":
        payload = _require_exact_object(
            value,
            fields={"kind", "metric_ref"},
            role=role,
        )
        ref = _decode_ref_payload(payload["metric_ref"])
        if ref.kind is not SemanticKind.METRIC:
            raise ValueError(f"analysis job {role}.metric_ref must be metric")
        return
    if kind == "runtime_expression":
        payload = _require_exact_object(
            value,
            fields={"kind", "expression_schema", "expression_fingerprint"},
            role=role,
        )
        if payload["expression_schema"] != "metric-expression/v1":
            raise ValueError(
                f"analysis job {role}.expression_schema must be 'metric-expression/v1'"
            )
        if (
            type(payload["expression_fingerprint"]) is not str
            or not payload["expression_fingerprint"]
        ):
            raise ValueError(f"analysis job {role}.expression_fingerprint must be non-empty")
        return
    raise ValueError(f"analysis job {role}.kind is invalid")


def _validate_job_subject(value: object, *, role: str) -> None:
    if type(value) is not dict:
        raise ValueError(f"analysis job {role} must be an object")
    kind = value.get("kind")
    if kind == "catalog_metric":
        payload = _require_exact_object(
            value,
            fields={"kind", "metric_ref"},
            role=role,
        )
        ref = _decode_ref_payload(payload["metric_ref"])
        if ref.kind is not SemanticKind.METRIC:
            raise ValueError(f"analysis job {role}.metric_ref must be metric")
        return
    if kind == "runtime_expression":
        payload = _require_exact_object(
            value,
            fields={"kind", "expression_schema", "expression_fingerprint"},
            role=role,
        )
        _validate_metric_identity_payload(
            {"kind": "runtime_expression", **payload},
            role=role,
        )
        return
    if kind == "delta_metric":
        payload = _require_exact_object(
            value,
            fields={"kind", "comparison"},
            role=role,
        )
        comparison = _require_exact_object(
            payload["comparison"],
            fields={
                "schema",
                "current",
                "baseline",
                "current_artifact_id",
                "baseline_artifact_id",
                "semantics",
                "alignment_policy_fingerprint",
                "attribution_basis_fingerprint",
            },
            role=f"{role}.comparison",
        )
        if comparison["schema"] != "delta-comparison/v2":
            raise ValueError(f"analysis job {role}.comparison.schema must be 'delta-comparison/v2'")
        _validate_metric_identity_payload(comparison["current"], role=f"{role}.comparison.current")
        _validate_metric_identity_payload(
            comparison["baseline"], role=f"{role}.comparison.baseline"
        )
        for field in (
            "current_artifact_id",
            "baseline_artifact_id",
            "alignment_policy_fingerprint",
        ):
            if type(comparison[field]) is not str or not comparison[field]:
                raise ValueError(f"analysis job {role}.comparison.{field} must be non-empty")
        semantics = comparison["semantics"]
        if not isinstance(semantics, dict):
            raise ValueError(f"analysis job {role}.comparison.semantics must be an object")
        semantics_schema = semantics.get("schema")
        if semantics_schema == "exact-comparison-semantics/v1":
            exact = _require_exact_object(
                semantics,
                fields={"schema", "comparable_semantics_fingerprint"},
                role=f"{role}.comparison.semantics",
            )
            value = exact["comparable_semantics_fingerprint"]
            if type(value) is not str or not value:
                raise ValueError(
                    f"analysis job {role}.comparison.semantics."
                    "comparable_semantics_fingerprint must be non-empty"
                )
        elif semantics_schema == "cumulative-equivalent-comparison-semantics/v1":
            cumulative = _require_exact_object(
                semantics,
                fields={
                    "schema",
                    "current_expression_fingerprint",
                    "baseline_expression_fingerprint",
                    "canonical_expression_fingerprint",
                    "current_comparable_semantics_fingerprint",
                    "baseline_comparable_semantics_fingerprint",
                    "canonical_comparable_semantics_fingerprint",
                },
                role=f"{role}.comparison.semantics",
            )
            for field in (
                "current_expression_fingerprint",
                "baseline_expression_fingerprint",
                "canonical_expression_fingerprint",
                "current_comparable_semantics_fingerprint",
                "baseline_comparable_semantics_fingerprint",
                "canonical_comparable_semantics_fingerprint",
            ):
                value = cumulative[field]
                if type(value) is not str or not value:
                    raise ValueError(
                        f"analysis job {role}.comparison.semantics.{field} must be non-empty"
                    )
        else:
            raise ValueError(f"analysis job {role}.comparison.semantics.schema is unsupported")
        if comparison["attribution_basis_fingerprint"] is not None and (
            type(comparison["attribution_basis_fingerprint"]) is not str
            or not comparison["attribution_basis_fingerprint"]
        ):
            raise ValueError(
                f"analysis job {role}.comparison.attribution_basis_fingerprint "
                "must be a non-empty string or null"
            )
        return
    if kind in {"event", "lifecycle", "subject_set"}:
        payload = _require_exact_object(
            value,
            fields={"kind", "subject_entity_ref", "subject_identity_signature"},
            role=role,
        )
        ref = _decode_ref_payload(payload["subject_entity_ref"])
        if ref.kind is not SemanticKind.ENTITY:
            raise ValueError(f"analysis job {role}.subject_entity_ref must be entity")
        signature = payload["subject_identity_signature"]
        if (
            not isinstance(signature, list)
            or not signature
            or any(type(component) is not str or not component for component in signature)
        ):
            raise ValueError(
                f"analysis job {role}.subject_identity_signature must be non-empty strings"
            )
        return
    raise ValueError(f"analysis job {role}.kind is invalid")


def _validate_cohort_payload(value: object) -> None:
    from marivo.analysis.frames.subject import SubjectCohortBinding

    if type(value) is not dict:
        raise ValueError("analysis job cohort must be an object")
    SubjectCohortBinding.model_validate(value)


def _validate_event_reducer_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.event import EventMatchingPolicy, PatternStep
    from marivo.analysis.frames.event import (
        GroupedFunnelReconciliationReceipt,
        SubjectAxisBinding,
    )

    if type(value) is not dict:
        raise ValueError("analysis job event_reducer must be an object")
    kind = value.get("kind")
    common_fields = {
        "kind",
        "source_artifact_ref",
        "source_artifact_fingerprint",
        "pattern_fingerprint",
        "matching",
        "coverage_basis",
    }
    shape_fields = {
        "funnel": {
            "axes",
            "grouped_reconciliation",
            "source_unused_event_count",
        },
        "time_to_event": {
            "start_step",
            "end_step",
            "axes",
            "source_unused_end_count",
        },
    }
    if kind not in shape_fields:
        raise ValueError("analysis job event_reducer.kind is invalid")
    payload = _require_exact_object(
        value,
        fields=common_fields | shape_fields[kind],
        role="event_reducer",
    )
    for field in (
        "source_artifact_ref",
        "source_artifact_fingerprint",
        "pattern_fingerprint",
    ):
        if type(payload[field]) is not str or not payload[field]:
            raise ValueError(f"analysis job event_reducer.{field} must be non-empty")
    TypeAdapter(EventMatchingPolicy).validate_python(payload["matching"])
    if payload["coverage_basis"] not in {
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    }:
        raise ValueError("analysis job event_reducer.coverage_basis is invalid")
    if kind == "funnel":
        TypeAdapter(list[SubjectAxisBinding]).validate_python(payload["axes"])
        GroupedFunnelReconciliationReceipt.model_validate(payload["grouped_reconciliation"])
        count_field = "source_unused_event_count"
    else:
        TypeAdapter(PatternStep).validate_json(json.dumps(payload["start_step"]))
        TypeAdapter(PatternStep).validate_json(json.dumps(payload["end_step"]))
        count_field = "source_unused_end_count"
    if type(payload[count_field]) is not int or payload[count_field] < 0:
        raise ValueError(f"analysis job event_reducer.{count_field} must be non-negative")


def _validate_funnel_comparison_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo._temporal import _validate_time_scope_data
    from marivo.analysis.event import CompletenessDeclaration, EventMatchingPolicy
    from marivo.analysis.frames.event import SubjectAxisBinding

    role = "funnel_comparison"
    payload = _require_exact_object(
        value,
        fields={
            "artifact_ref",
            "artifact_fingerprint",
            "source_current_ref",
            "source_baseline_ref",
            "source_current_fingerprint",
            "source_baseline_fingerprint",
            "source_current_journey_ref",
            "source_baseline_journey_ref",
            "pattern_fingerprint",
            "matching",
            "completion_through",
            "axes",
            "alignment_kind",
            "aligned_step_keys",
            "zero_filled_tuple_count",
            "current_cohort_window",
            "baseline_cohort_window",
            "current_coverage_basis",
            "baseline_coverage_basis",
            "current_completeness",
            "baseline_completeness",
        },
        role=role,
    )
    for field in (
        "artifact_ref",
        "artifact_fingerprint",
        "source_current_ref",
        "source_baseline_ref",
        "source_current_fingerprint",
        "source_baseline_fingerprint",
        "source_current_journey_ref",
        "source_baseline_journey_ref",
        "pattern_fingerprint",
        "completion_through",
    ):
        if type(payload[field]) is not str or not payload[field]:
            raise ValueError(f"analysis job {role}.{field} must be non-empty")
    if payload["source_current_ref"] == payload["source_baseline_ref"]:
        raise ValueError(f"analysis job {role} requires distinct source funnels")
    TypeAdapter(EventMatchingPolicy).validate_python(payload["matching"])
    TypeAdapter(list[SubjectAxisBinding]).validate_python(payload["axes"])
    if payload["alignment_kind"] != "step_key_and_axis_tuple":
        raise ValueError(f"analysis job {role}.alignment_kind is invalid")
    step_keys = payload["aligned_step_keys"]
    if (
        not isinstance(step_keys, list)
        or not step_keys
        or any(type(item) is not str or not item for item in step_keys)
        or len(set(step_keys)) != len(step_keys)
    ):
        raise ValueError(f"analysis job {role}.aligned_step_keys must be unique strings")
    if (
        type(payload["zero_filled_tuple_count"]) is not int
        or payload["zero_filled_tuple_count"] < 0
    ):
        raise ValueError(f"analysis job {role}.zero_filled_tuple_count must be non-negative")
    _validate_time_scope_data(payload["current_cohort_window"])
    _validate_time_scope_data(payload["baseline_cohort_window"])
    valid_coverage = {
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    }
    for side in ("current", "baseline"):
        if payload[f"{side}_coverage_basis"] not in valid_coverage:
            raise ValueError(f"analysis job {role}.{side}_coverage_basis is invalid")
        TypeAdapter(list[CompletenessDeclaration]).validate_json(
            json.dumps(payload[f"{side}_completeness"])
        )


def _validate_funnel_attribution_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.event import EventMatchingPolicy
    from marivo.analysis.frames.attribution import FunnelAttributionReconciliation
    from marivo.analysis.frames.event import SubjectAxisBinding
    from marivo.analysis.funnel import FunnelLossRate

    role = "funnel_attribution"
    payload = _require_exact_object(
        value,
        fields={
            "artifact_ref",
            "artifact_fingerprint",
            "source_delta_ref",
            "source_delta_fingerprint",
            "source_current_journey_ref",
            "source_baseline_journey_ref",
            "source_pattern_fingerprint",
            "matching",
            "coverage_basis",
            "target",
            "preceding_step_key",
            "axes",
            "mode",
            "reconciliation",
        },
        role=role,
    )
    for field in (
        "artifact_ref",
        "artifact_fingerprint",
        "source_delta_ref",
        "source_delta_fingerprint",
        "source_current_journey_ref",
        "source_baseline_journey_ref",
        "source_pattern_fingerprint",
        "preceding_step_key",
    ):
        if type(payload[field]) is not str or not payload[field]:
            raise ValueError(f"analysis job {role}.{field} must be non-empty")
    TypeAdapter(EventMatchingPolicy).validate_python(payload["matching"])
    if payload["coverage_basis"] not in {
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    }:
        raise ValueError(f"analysis job {role}.coverage_basis is invalid")
    FunnelLossRate.model_validate(payload["target"])
    axes = TypeAdapter(list[SubjectAxisBinding]).validate_python(payload["axes"])
    if not axes:
        raise ValueError(f"analysis job {role}.axes must be non-empty")
    mode = payload["mode"]
    if mode not in {None, "joint", "hierarchy"}:
        raise ValueError(f"analysis job {role}.mode is invalid")
    if (len(axes) == 1) != (mode is None):
        raise ValueError(f"analysis job {role}.mode does not match the axis count")
    FunnelAttributionReconciliation.model_validate(payload["reconciliation"])


def _validate_lifecycle_common(
    payload: dict[str, object],
    *,
    role: str,
) -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.frames.lifecycle import LifecycleStateBinding

    state_model_ref = _decode_ref_payload(cast("Any", payload["state_model_ref"]))
    if state_model_ref.kind is not SemanticKind.STATE_MODEL:
        raise ValueError(f"analysis job {role}.state_model_ref must be state_model")
    if (
        type(payload["state_model_fingerprint"]) is not str
        or not payload["state_model_fingerprint"]
    ):
        raise ValueError(f"analysis job {role}.state_model_fingerprint must be non-empty")
    states = payload["states"]
    if not isinstance(states, list) or not states:
        raise ValueError(f"analysis job {role}.states must be a non-empty list")
    decoded_states = TypeAdapter(list[LifecycleStateBinding]).validate_python(states)
    if any(item.state.model != RefPayloadV1.from_ref(state_model_ref) for item in decoded_states):
        raise ValueError(f"analysis job {role}.states must reference state_model_ref")


def _validate_lifecycle_history_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo._temporal import _validate_time_scope_data
    from marivo.analysis.event import CompletenessDeclaration
    from marivo.analysis.frames.event import EventInputCoverage
    from marivo.analysis.frames.lifecycle import (
        LifecycleTraceManifest,
        LifecycleTriggerBinding,
    )
    from marivo.analysis.lifecycle import FromInception

    fields = {
        "state_model_ref",
        "state_model_fingerprint",
        "states",
        "seed",
        "window",
        "violation_behavior_id",
        "triggers",
        "completeness",
        "input_coverage",
        "coverage_basis",
        "event_fingerprints",
        "event_identity_components",
        "query_refs",
        "population_count",
        "seeded_subject_count",
        "coverage_censored_subject_count",
        "interval_count",
        "violation_count",
        "pre_inception_ignored_counts",
        "violation_trace",
    }
    payload = _require_exact_object(value, fields=fields, role="lifecycle_history")
    _validate_lifecycle_common(payload, role="lifecycle_history")
    FromInception.model_validate(payload["seed"])
    _validate_time_scope_data(payload["window"])
    if payload["violation_behavior_id"] != "record_and_continue/v1":
        raise ValueError("analysis job lifecycle_history.violation_behavior_id is invalid")
    triggers = TypeAdapter(list[LifecycleTriggerBinding]).validate_python(payload["triggers"])
    if not triggers:
        raise ValueError("analysis job lifecycle_history.triggers must be non-empty")
    TypeAdapter(list[CompletenessDeclaration]).validate_json(json.dumps(payload["completeness"]))
    coverage = TypeAdapter(list[EventInputCoverage]).validate_python(payload["input_coverage"])
    if payload["coverage_basis"] not in {
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    }:
        raise ValueError("analysis job lifecycle_history.coverage_basis is invalid")
    event_fingerprints = payload["event_fingerprints"]
    if (
        not isinstance(event_fingerprints, dict)
        or not event_fingerprints
        or any(
            type(key) is not str or not key or type(digest) is not str or not digest
            for key, digest in event_fingerprints.items()
        )
    ):
        raise ValueError("analysis job lifecycle_history.event_fingerprints is invalid")
    expected_events = {trigger.event_ref.path for trigger in triggers}
    if set(event_fingerprints) != expected_events:
        raise ValueError("analysis job lifecycle_history.event_fingerprints must match triggers")
    if {item.event_ref.path for item in coverage} != expected_events:
        raise ValueError("analysis job lifecycle_history.input_coverage must match triggers")
    identities = payload["event_identity_components"]
    if not isinstance(identities, dict) or set(identities) != expected_events:
        raise ValueError("analysis job lifecycle_history.event_identity_components is invalid")
    for components in identities.values():
        if not isinstance(components, list) or not components:
            raise ValueError("analysis job Lifecycle Event identity components must be non-empty")
        for component in components:
            if _decode_ref_payload(component).kind is not SemanticKind.DIMENSION:
                raise ValueError(
                    "analysis job Lifecycle Event identity components must be dimensions"
                )
    query_refs = payload["query_refs"]
    if not isinstance(query_refs, list) or any(
        type(item) is not str or not item for item in query_refs
    ):
        raise ValueError("analysis job lifecycle_history.query_refs is invalid")
    for field in (
        "population_count",
        "seeded_subject_count",
        "coverage_censored_subject_count",
        "interval_count",
        "violation_count",
    ):
        if type(payload[field]) is not int or payload[field] < 0:
            raise ValueError(f"analysis job lifecycle_history.{field} must be non-negative")
    ignored = payload["pre_inception_ignored_counts"]
    if (
        not isinstance(ignored, dict)
        or set(ignored) != {trigger.key for trigger in triggers}
        or any(type(count) is not int or count < 0 for count in ignored.values())
    ):
        raise ValueError("analysis job lifecycle_history.pre_inception_ignored_counts is invalid")
    manifest = LifecycleTraceManifest.model_validate(payload["violation_trace"])
    if manifest.content_hash is None:
        raise ValueError("analysis job lifecycle_history.violation_trace requires content_hash")
    if manifest.row_count != payload["violation_count"]:
        raise ValueError("analysis job Lifecycle trace row count must equal violation_count")


def _validate_lifecycle_reducer_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.frames.lifecycle import (
        LifecycleAxisBinding,
        LifecycleStatePair,
    )

    if type(value) is not dict:
        raise ValueError("analysis job lifecycle_reducer must be an object")
    kind = value.get("kind")
    common = {
        "kind",
        "state_model_ref",
        "state_model_fingerprint",
        "states",
        "source_artifact_ref",
        "source_artifact_fingerprint",
    }
    shapes = {
        "distribution": {
            "at",
            "axes",
            "known_subject_counts",
            "coverage_censored_subject_counts",
            "grouped_reconciliation_hash",
        },
        "transitions": {"modeled_pairs", "modeled_transition_count"},
        "dwell": {"source_interval_count"},
        "violations": {"violation_count", "source_trace_content_hash"},
    }
    if kind not in shapes:
        raise ValueError("analysis job lifecycle_reducer.kind is invalid")
    payload = _require_exact_object(
        value,
        fields=common | shapes[kind],
        role="lifecycle_reducer",
    )
    _validate_lifecycle_common(payload, role="lifecycle_reducer")
    for field in ("source_artifact_ref", "source_artifact_fingerprint"):
        if type(payload[field]) is not str or not payload[field]:
            raise ValueError(f"analysis job lifecycle_reducer.{field} must be non-empty")
    if kind == "distribution":
        at = payload["at"]
        if not isinstance(at, list) or not at or len(set(at)) != len(at):
            raise ValueError("analysis job lifecycle_reducer.at must be non-empty and unique")
        TypeAdapter(list[LifecycleAxisBinding]).validate_python(payload["axes"])
        for field in (
            "known_subject_counts",
            "coverage_censored_subject_counts",
        ):
            counts = payload[field]
            if (
                not isinstance(counts, dict)
                or set(counts) != set(at)
                or any(type(count) is not int or count < 0 for count in counts.values())
            ):
                raise ValueError(f"analysis job lifecycle_reducer.{field} is invalid")
        digest = payload["grouped_reconciliation_hash"]
        if type(digest) is not str or not digest.startswith("sha256:"):
            raise ValueError("analysis job lifecycle_reducer grouped hash is invalid")
    elif kind == "transitions":
        TypeAdapter(list[LifecycleStatePair]).validate_python(payload["modeled_pairs"])
        if (
            type(payload["modeled_transition_count"]) is not int
            or payload["modeled_transition_count"] < 0
        ):
            raise ValueError("analysis job lifecycle_reducer.modeled_transition_count is invalid")
    elif kind == "dwell":
        if (
            type(payload["source_interval_count"]) is not int
            or payload["source_interval_count"] < 0
        ):
            raise ValueError("analysis job lifecycle_reducer.source_interval_count is invalid")
    else:
        if type(payload["violation_count"]) is not int or payload["violation_count"] < 0:
            raise ValueError("analysis job lifecycle_reducer.violation_count is invalid")
        digest = payload["source_trace_content_hash"]
        if type(digest) is not str or not digest.startswith("sha256:"):
            raise ValueError("analysis job lifecycle_reducer source trace hash is invalid")


def _validate_subject_set_payload(value: object) -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.frames.subject import SubjectSetSourceBinding
    from marivo.analysis.subject import SubjectSelection

    payload = _require_exact_object(
        value,
        fields={
            "source",
            "selection",
            "selection_fingerprint",
            "selected_count",
            "excluded_coverage_censored_count",
            "coverage_status",
        },
        role="subject_set",
    )
    SubjectSetSourceBinding.model_validate(payload["source"])
    selection: Any = TypeAdapter(SubjectSelection).validate_json(json.dumps(payload["selection"]))
    if payload["selection_fingerprint"] != selection.fingerprint:
        raise ValueError("analysis job subject_set.selection_fingerprint must match selection")
    for field in ("selected_count", "excluded_coverage_censored_count"):
        if type(payload[field]) is not int or payload[field] < 0:
            raise ValueError(f"analysis job subject_set.{field} must be non-negative")
    expected_coverage = (
        "coverage_censored" if payload["excluded_coverage_censored_count"] > 0 else "ready"
    )
    if payload["coverage_status"] != expected_coverage:
        raise ValueError(f"analysis job subject_set.coverage_status must be {expected_coverage!r}")


def _validate_event_journey_payload(value: object) -> None:
    payload = _require_exact_object(
        value,
        fields={
            "pattern",
            "matching",
            "cohort_window",
            "completion_through",
            "completeness",
            "input_coverage",
            "coverage_basis",
            "event_fingerprints",
            "event_identity_components",
            "role_endpoints",
            "query_refs",
            "unused_event_count",
            "unused_event_counts_by_step",
        },
        role="event_journey",
    )
    from pydantic import TypeAdapter

    from marivo._temporal import _validate_time_scope_data
    from marivo.analysis.event import (
        CompletenessDeclaration,
        EventMatchingPolicy,
        EventPattern,
    )
    from marivo.analysis.frames.event import EventInputCoverage

    pattern_payload = payload["pattern"]
    if type(pattern_payload) is not dict or not isinstance(pattern_payload.get("steps"), list):
        raise ValueError("analysis job event_journey.pattern is invalid")
    decoded_steps: list[dict[str, object]] = []
    for index, raw_step in enumerate(pattern_payload["steps"]):
        if type(raw_step) is not dict or type(raw_step.get("participant")) is not dict:
            raise ValueError(f"analysis job event_journey.pattern.steps[{index}] is invalid")
        participant = dict(raw_step["participant"])
        event_payload = participant.get("event")
        if not isinstance(event_payload, (RefPayloadV1, dict)):
            raise ValueError("analysis job event_journey pattern participant event is invalid")
        participant["event"] = _decode_ref_payload(event_payload)
        decoded_steps.append({**raw_step, "participant": participant})
    pattern = EventPattern.model_validate({**pattern_payload, "steps": decoded_steps})
    TypeAdapter(EventMatchingPolicy).validate_python(payload["matching"])
    _validate_time_scope_data(payload["cohort_window"])
    completeness_payload = payload["completeness"]
    if not isinstance(completeness_payload, list):
        raise ValueError("analysis job event_journey.completeness must be a list")
    decoded_completeness: list[dict[str, object]] = []
    for index, raw_declaration in enumerate(completeness_payload):
        if type(raw_declaration) is not dict or not isinstance(raw_declaration.get("inputs"), list):
            raise ValueError(f"analysis job event_journey.completeness[{index}] is invalid")
        decoded_completeness.append(
            {
                **raw_declaration,
                "inputs": [
                    _decode_ref_payload(event_ref) for event_ref in raw_declaration["inputs"]
                ],
            }
        )
    TypeAdapter(list[CompletenessDeclaration]).validate_python(decoded_completeness)
    TypeAdapter(list[EventInputCoverage]).validate_python(payload["input_coverage"])
    if type(payload["completion_through"]) is not str or not payload["completion_through"].strip():
        raise ValueError("analysis job event_journey.completion_through must be non-empty")
    if payload["coverage_basis"] not in {
        "observed_watermark",
        "declared_complete",
        "mixed",
        "unknown",
    }:
        raise ValueError("analysis job event_journey.coverage_basis is invalid")
    event_fingerprints = payload["event_fingerprints"]
    if (
        not isinstance(event_fingerprints, dict)
        or not event_fingerprints
        or any(
            type(key) is not str or not key or type(digest) is not str or not digest
            for key, digest in event_fingerprints.items()
        )
    ):
        raise ValueError("analysis job event_journey.event_fingerprints is invalid")
    event_identity_components = payload["event_identity_components"]
    if not isinstance(event_identity_components, dict) or set(event_identity_components) != set(
        event_fingerprints
    ):
        raise ValueError(
            "analysis job event_journey.event_identity_components must cover "
            "the exact Event fingerprint keys"
        )
    for event_ref, components in event_identity_components.items():
        if not isinstance(components, list) or not components:
            raise ValueError(
                f"analysis job event_journey identity for {event_ref!r} must be non-empty"
            )
        for component in components:
            if _decode_ref_payload(component).kind is not SemanticKind.DIMENSION:
                raise ValueError(
                    "analysis job event_journey identity components must be dimensions"
                )
    role_endpoints = payload["role_endpoints"]
    if not isinstance(role_endpoints, dict) or not role_endpoints:
        raise ValueError("analysis job event_journey.role_endpoints must be non-empty")
    for key, endpoint in role_endpoints.items():
        if type(key) is not str or not key:
            raise ValueError("analysis job event_journey role key must be non-empty")
        if _decode_ref_payload(endpoint).kind is not SemanticKind.ENTITY:
            raise ValueError("analysis job event_journey role endpoint must be entity")
    query_refs = payload["query_refs"]
    if not isinstance(query_refs, list) or any(
        type(query_ref) is not str or not query_ref for query_ref in query_refs
    ):
        raise ValueError("analysis job event_journey.query_refs must be strings")
    unused_event_count = payload["unused_event_count"]
    if type(unused_event_count) is not int or unused_event_count < 0:
        raise ValueError("analysis job event_journey.unused_event_count must be non-negative")
    unused_by_step = payload["unused_event_counts_by_step"]
    expected_step_keys = {step.key for step in pattern.steps}
    if (
        type(unused_by_step) is not dict
        or set(unused_by_step) != expected_step_keys
        or any(type(value) is not int or value < 0 for value in unused_by_step.values())
    ):
        raise ValueError(
            "analysis job event_journey.unused_event_counts_by_step must contain "
            "one non-negative count per PatternStep"
        )


def _validate_dependency_digest_payload(value: object, *, role: str) -> None:
    payload = _require_exact_object(
        value,
        fields={"schema", "entries", "digest"},
        role=role,
    )
    if payload["schema"] != "marivo.semantic_dependency_digest/v1":
        raise ValueError(
            f"analysis job {role}.schema must be 'marivo.semantic_dependency_digest/v1'"
        )
    if type(payload["digest"]) is not str or not payload["digest"].startswith("sha256:"):
        raise ValueError(f"analysis job {role}.digest must use the sha256: prefix")
    entries = payload["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"analysis job {role}.entries must be a non-empty list")
    for index, entry_value in enumerate(entries):
        entry = _require_exact_object(
            entry_value,
            fields={"ref", "body_digest", "fields", "bindings"},
            role=f"{role}.entries[{index}]",
        )
        _decode_ref_payload(entry["ref"])
        if entry["body_digest"] is not None and (
            type(entry["body_digest"]) is not str or not entry["body_digest"]
        ):
            raise ValueError(f"analysis job {role}.entries[{index}].body_digest is invalid")
        if not isinstance(entry["fields"], list):
            raise ValueError(f"analysis job {role}.entries[{index}].fields must be a list")
        bindings = entry["bindings"]
        if not isinstance(bindings, list):
            raise ValueError(f"analysis job {role}.entries[{index}].bindings must be a list")
        for binding_index, binding_value in enumerate(bindings):
            binding = _require_exact_object(
                binding_value,
                fields={"field_ref", "entity_position"},
                role=f"{role}.entries[{index}].bindings[{binding_index}]",
            )
            field_ref = _decode_ref_payload(binding["field_ref"])
            if field_ref.kind not in {
                SemanticKind.DIMENSION,
                SemanticKind.TIME_DIMENSION,
                SemanticKind.MEASURE,
            }:
                raise ValueError(f"analysis job {role} expression binding requires a field ref")
            if type(binding["entity_position"]) is not int or binding["entity_position"] < 0:
                raise ValueError(f"analysis job {role} expression binding position is invalid")


def get_process_current() -> Session | None:
    """Return the process-level current session, if any."""
    return _CURRENT_SESSION


def set_process_current(session: Session | None) -> None:
    """Set the process-level current session."""
    global _CURRENT_SESSION
    _CURRENT_SESSION = session


def reset_process_state() -> None:
    """Reset the process-level current session to ``None``.

    Used by test fixtures and teardown helpers.
    """
    set_process_current(None)


# ---------------------------------------------------------------------------
# current() — resolves from process state or store
# ---------------------------------------------------------------------------


def current() -> Session | None:
    """Return the current session, or ``None`` when no session is current.

    Resolution order:
    1. Process-current session (set by ``get_or_create`` or ``attach``).
    2. Persisted ``current_session_id`` in the store — load the session by id.
    3. If the stored id no longer matches a session row, clear the stale
       pointer and return ``None``.
    """
    proc = get_process_current()
    if proc is not None:
        return proc

    store = SessionStore()
    current_id = store.get_current_session_id()
    if current_id is None:
        return None

    row = store.get_session_by_id(current_id)
    if row is None:
        # Stale pointer — the session was deleted
        store.clear_current_session_id()
        return None

    connection_runtime = _build_connection_runtime(
        store.project_root, None, None, use_datasources=True
    )
    session = _session_from_row(store, row, connection_runtime)
    set_process_current(session)
    return session


def require_current_session() -> Session:
    """Return the current session, raising if none is current."""
    session = current()
    if session is None:
        raise NoActiveSessionError(
            message="no current analysis session",
            hint=(
                "Call mv.session.get_or_create("
                "name='<stable-session-name>', question='<business question>') "
                "before running analysis intents."
            ),
        )
    return session


# ---------------------------------------------------------------------------
# Runtime-only helpers (never persisted)
# ---------------------------------------------------------------------------


def _build_connection_runtime(
    project_root: Path,
    backends: dict[str, Callable[[], BaseBackend]] | None,
    backend_factory: Callable[[str], BaseBackend] | None,
    *,
    use_datasources: bool = True,
) -> AnalysisConnectionRuntime:
    """Build the session-owned datasource connection runtime."""
    if backends is not None and backend_factory is not None:
        raise SessionStateError(
            message="supply either backends={...} or backend_factory=..., not both",
        )
    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.datasource.runtime import DatasourceConnectionService

    return AnalysisConnectionRuntime(
        DatasourceConnectionService(
            project_root=project_root,
            backends=backends,
            backend_factory=backend_factory,
            use_datasources=use_datasources,
            include_semantic_layers=use_datasources,
        )
    )


def _build_semantic_catalog(project_root: Path) -> Any:
    """Build a SemanticCatalog from the project root, preserving not-ready state."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=project_root)
    project.load()
    return project.catalog()


# ---------------------------------------------------------------------------
# Session construction from store row
# ---------------------------------------------------------------------------


def _read_report_timezone(layout: PersistenceLayout) -> ResolvedTimezone:
    meta_path = layout.session_dir / "meta.json"
    if not meta_path.is_file():
        return resolve_system_timezone()
    meta = json.loads(meta_path.read_text())
    name = meta.get("report_tz")
    if not isinstance(name, str) or not name:
        return resolve_system_timezone()
    return ResolvedTimezone(
        name=name,
        tz=zoneinfo_from_name(name),
        resolution=str(meta.get("report_tz_resolution") or "iana"),
        warning=meta.get("report_tz_warning")
        if isinstance(meta.get("report_tz_warning"), str)
        else None,
    )


def _session_from_row(
    store: SessionStore,
    row: Sqlite3RowLike,
    connection_runtime: Any,
) -> Session:
    """Build a live ``Session`` from a store row and a runtime connection runtime.

    Only persisted metadata is used: id, name, question, cwd, created_at,
    updated_at and report timezone from session meta.
    """
    # sqlite3.Row is not importable at type-check time; accept a duck-typed row.
    session_id = row["id"]
    store.validate_session_runtime_schema(str(session_id))
    project_root = store.project_root
    layout = PersistenceLayout(project_root=project_root, session_id=session_id)
    semantic_catalog = _build_semantic_catalog(project_root)
    from marivo.ontology import OntologyCatalog
    from marivo.ontology import load as load_ontology
    from marivo.ontology.errors import OntologyLoadError

    ontology_catalog: OntologyCatalog | None
    ontology_state: Literal["absent", "ready", "unavailable"]
    try:
        ontology_catalog = load_ontology(semantic=semantic_catalog)
    except OntologyLoadError as error:
        ontology_state = "unavailable"
        ontology_catalog = None
        ontology_issues = error.issues
    else:
        ontology_state = "ready" if ontology_catalog.configured else "absent"
        ontology_issues = ()

    resolved_report_tz = _read_report_timezone(layout)
    return Session(
        id=session_id,
        name=row["name"],
        question=row["question"],
        cwd=Path(row["cwd"]),
        project_root=project_root,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        connection_runtime=connection_runtime,
        layout=layout,
        semantic_catalog=semantic_catalog,
        store=store,
        report_tz=resolved_report_tz.tz,
        report_tz_name=resolved_report_tz.name,
        report_tz_resolution=resolved_report_tz.resolution,
        report_tz_warning=resolved_report_tz.warning,
        ontology_state=ontology_state,
        ontology_catalog=ontology_catalog,
        ontology_issues=ontology_issues,
    )


# Type alias for duck-typed sqlite3.Row objects
Sqlite3RowLike = Any  # sqlite3.Row is not available at type-check time


# ---------------------------------------------------------------------------
# Persistence helpers: write to disk + register in store
# ---------------------------------------------------------------------------


@staged("persist")
def persist_frame(session: Session, frame: BaseFrame) -> BaseFrameMeta:
    """Write a frame to disk and register it in the session store.

    Writes parquet and ``meta.json`` first, then inserts or replaces the
    ``artifacts`` row.  If the store write fails, the file may remain as
    an orphan; this is acceptable because the store is the source of truth.

    Args:
        session: The owning session.
        frame: The frame to persist.

    Returns:
        Updated ``BaseFrameMeta`` with on-disk ``byte_size`` populated.
    """
    updated = write_frame_to_disk(session._layout, frame)
    session._store.record_artifact(
        session_id=session.id,
        artifact_id=updated.ref,
        kind=updated.kind,
        path=session._layout.relative_path(
            session._layout.frames_dir / updated.ref / "data.parquet"
        ),
        meta_path=session._layout.relative_path(
            session._layout.frames_dir / updated.ref / "meta.json"
        ),
        content_hash=updated.content_hash,
        produced_by_job=updated.produced_by_job,
        evidence_status=updated.evidence_status,
        artifact_schema_version=updated.artifact_schema_version,
        finding_count=updated.finding_count,
        created_at=updated.created_at.isoformat(),
    )
    return updated


def register_frame_artifact(session: Session, frame: BaseFrame | BaseFrameMeta) -> None:
    """Register an already-persisted frame in the session store.

    Use this when the frame data and meta.json are already on disk
    (e.g. written by the evidence pipeline) and only the store
    registration is missing.  For new frames that need both disk write
    and registration, prefer :func:`persist_frame`.

    Args:
        session: The owning session.
        frame: The frame or frame meta whose files are already on disk.
    """
    meta = frame if isinstance(frame, BaseFrameMeta) else frame.meta
    session._store.record_artifact(
        session_id=session.id,
        artifact_id=meta.ref,
        kind=meta.kind,
        path=session._layout.relative_path(session._layout.frames_dir / meta.ref / "data.parquet"),
        meta_path=session._layout.relative_path(
            session._layout.frames_dir / meta.ref / "meta.json"
        ),
        content_hash=meta.content_hash,
        produced_by_job=meta.produced_by_job,
        evidence_status=meta.evidence_status,
        artifact_schema_version=meta.artifact_schema_version,
        finding_count=meta.finding_count,
        created_at=meta.created_at.isoformat(),
    )


def _persist_run_success_from_legacy_record(session: Session, record: dict[str, Any]) -> None:
    """Finish one canonical Run from an existing intent-owned success payload."""
    from collections.abc import Mapping

    from marivo.analysis.session._runs import active_run_admission, project_run_arguments

    active = active_run_admission()
    run_id = active.run_id if active is not None else str(record["id"])
    params = record.get("params")
    argument_source = (
        dict(cast("Mapping[str, object]", params)) if isinstance(params, Mapping) else {}
    )
    raw_queries = record.get("queries")
    if isinstance(raw_queries, list):
        argument_source["__queries"] = [
            {
                "id": query.get("query_id"),
                "datasource": query.get("datasource"),
                "dialect": query.get("dialect"),
                "digest": query.get("sql_digest"),
                "row_count": query.get("row_count"),
                "duration_ms": query.get("duration_ms"),
                "status": query.get("status"),
                "output_ref": query.get("output_ref"),
            }
            for query in raw_queries
            if isinstance(query, Mapping)
        ]
    projected, omitted = project_run_arguments(argument_source)
    raw_capability_id = str(record["intent"])
    capability_id = {
        "attribute.funnel_loss_rate": "attribute",
        "compare.funnel": "compare",
        "decompose": "attribute",
        "select_metric": "MetricFrame.metric",
    }.get(raw_capability_id, raw_capability_id)
    if capability_id == "transform" and isinstance(params, Mapping):
        op = params.get("op")
        if isinstance(op, str):
            capability_id = f"transform.{op}"
    elif capability_id == "discover" and isinstance(params, Mapping):
        objective = params.get("objective")
        if isinstance(objective, str):
            capability_id = f"discover.{objective}"
    input_refs_value = record.get("input_frame_refs", ())
    input_refs = (
        tuple(dict.fromkeys(str(value) for value in input_refs_value))
        if isinstance(input_refs_value, list | tuple)
        else ()
    )
    if active is None:
        input_refs = tuple(
            ref for ref in input_refs if session._store.get_artifact(session.id, ref) is not None
        )
    if session._store.get_run(session.id, run_id) is None:
        session._store.begin_run(
            session_id=session.id,
            run_id=run_id,
            capability_id=capability_id,
            analysis_purpose=cast("str | None", record.get("analysis_purpose")),
            arguments=projected,
            omitted_argument_names=omitted,
            input_artifact_refs=input_refs,
            started_at=str(record["started_at"]),
        )
    output_ref = record.get("output_frame_ref") or record.get("output_artifact_id")
    if not isinstance(output_ref, str) or not output_ref:
        raise SessionStateError(
            message="succeeded materializing Run requires one output Artifact",
            expected="non-empty output Artifact ref",
            received=str(output_ref),
            location=f"Run {run_id!r} terminal transition",
        )
    record_intent = str(record["intent"])
    completes_active = active is None or (
        active.capability_id == record_intent
        or (active.capability_id.startswith("transform.") and record_intent == "transform")
        or (active.capability_id.startswith("discover.") and record_intent == "discover")
        or (active.capability_id == "MetricFrame.metric" and record_intent == "select_metric")
        or (active.capability_id == "compare" and record_intent == "compare.funnel")
        or (active.capability_id == "attribute" and record_intent == "attribute.funnel_loss_rate")
    )
    if active is not None and not completes_active:
        return
    output_mode = "reused" if record.get("reused_artifact") is True else "produced"
    artifact_row = session._store.get_artifact(session.id, output_ref)
    if artifact_row is not None and artifact_row["produced_by_job"] != run_id:
        output_mode = "reused"
    if active is not None:
        active.succeed(
            output_ref,
            output_mode=output_mode,
            finished_at=str(record.get("finished_at") or datetime.now(UTC).isoformat()),
            arguments=projected,
            omitted_argument_names=omitted,
        )
    else:
        session._store.complete_run(
            session_id=session.id,
            run_id=run_id,
            output_artifact_ref=output_ref,
            output_mode=output_mode,
            finished_at=str(record.get("finished_at") or datetime.now(UTC).isoformat()),
            arguments=projected,
            omitted_argument_names=omitted,
        )


@staged("persist")
def persist_job_record(session: Session, record: dict[str, Any]) -> None:
    """Validate an intent success payload and finish its canonical persisted Run.

    This private intent-to-Run commit boundary accepts the established
    intent-owned payload shape, but does not expose it through public reads.

    Args:
        session: The owning session.
        record: Job record dict; must contain ``"id"``, ``"intent"``,
            ``"status"``, ``"started_at"``, and optionally ``"finished_at"``
            and ``"output_frame_ref"`` or ``"output_artifact_id"``.
    """
    supplied_schema = record.get("schema")
    if supplied_schema not in {None, "marivo.analysis_job/v2"}:
        raise ValueError(
            f"job record schema must be 'marivo.analysis_job/v2'; received {supplied_schema!r}"
        )
    forbidden = {"semantic_model", "semantic_anchors", "metric_id", "metric_ids"} & set(record)
    if forbidden:
        raise ValueError(
            f"analysis job semantic identity must use named structured roles; got {sorted(forbidden)}"
        )
    fingerprint = record.get("catalog_definition_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("analysis job requires catalog_definition_fingerprint")
    has_subject = "subject" in record
    has_subjects = "subjects" in record
    if has_subject == has_subjects:
        raise ValueError("analysis job requires exactly one subject or subjects role")
    if has_subject:
        _validate_job_subject(record["subject"], role="subject")
    else:
        subjects = record["subjects"]
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("analysis job subjects must be a non-empty list")
        for index, subject in enumerate(subjects):
            _validate_job_subject(subject, role=f"subjects[{index}]")
    if "cohort" in record:
        _validate_cohort_payload(record["cohort"])
    funnel_roles = {"funnel_comparison", "funnel_attribution"} & set(record)
    if len(funnel_roles) > 1:
        raise ValueError("analysis funnel job requires exactly one funnel semantic role")
    if funnel_roles:
        if has_subjects or record["subject"].get("kind") != "event":
            raise ValueError("analysis funnel job requires one event subject")
        forbidden_funnel_fields = {
            "semantic_dependency_digest",
            "semantic_dependency_digests",
            "dimension_refs",
            "time_dimension_ref",
            "slice_predicates",
            "event_journey",
            "event_reducer",
            "lifecycle_history",
            "lifecycle_reducer",
            "subject_set",
        } & set(record)
        if forbidden_funnel_fields:
            raise ValueError(
                "analysis funnel job rejects unrelated semantic fields "
                f"{sorted(forbidden_funnel_fields)}"
            )
        role = next(iter(funnel_roles))
        payload = record[role]
        if role == "funnel_comparison":
            _validate_funnel_comparison_payload(payload)
        else:
            _validate_funnel_attribution_payload(payload)
        persisted = {"schema": "marivo.analysis_job/v2", **record}
        _persist_run_success_from_legacy_record(session, persisted)
        return
    lifecycle_roles = {"lifecycle_history", "lifecycle_reducer"} & set(record)
    if len(lifecycle_roles) > 1:
        raise ValueError("analysis Lifecycle job requires exactly one Lifecycle semantic role")
    if lifecycle_roles:
        if has_subjects or record["subject"].get("kind") != "lifecycle":
            raise ValueError("analysis Lifecycle job requires one lifecycle subject")
        forbidden_lifecycle_fields = {
            "semantic_dependency_digest",
            "semantic_dependency_digests",
            "dimension_refs",
            "time_dimension_ref",
            "slice_predicates",
            "event_journey",
            "event_reducer",
            "subject_set",
        } & set(record)
        if forbidden_lifecycle_fields:
            raise ValueError(
                "analysis Lifecycle job rejects unrelated semantic fields "
                f"{sorted(forbidden_lifecycle_fields)}"
            )
        if "lifecycle_history" in lifecycle_roles:
            _validate_lifecycle_history_payload(record["lifecycle_history"])
        else:
            _validate_lifecycle_reducer_payload(record["lifecycle_reducer"])
        persisted = {"schema": "marivo.analysis_job/v2", **record}
        _persist_run_success_from_legacy_record(session, persisted)
        return
    event_roles = {"event_journey", "event_reducer"} & set(record)
    if len(event_roles) > 1:
        raise ValueError("analysis Event job requires exactly one Event semantic role")
    if event_roles:
        if has_subjects or record["subject"].get("kind") != "event":
            raise ValueError("analysis Event job requires one event subject")
        forbidden_event_fields = {
            "semantic_dependency_digest",
            "semantic_dependency_digests",
            "dimension_refs",
            "time_dimension_ref",
            "slice_predicates",
        } & set(record)
        if forbidden_event_fields:
            raise ValueError(
                "analysis Event Journey job rejects metric semantic fields "
                f"{sorted(forbidden_event_fields)}"
            )
        if "event_journey" in event_roles:
            _validate_event_journey_payload(record["event_journey"])
        else:
            _validate_event_reducer_payload(record["event_reducer"])
        persisted = {"schema": "marivo.analysis_job/v2", **record}
        _persist_run_success_from_legacy_record(session, persisted)
        return
    if "subject_set" in record:
        if has_subjects or record["subject"].get("kind") != "subject_set":
            raise ValueError("analysis SubjectSet job requires one subject_set subject")
        forbidden_subject_fields = {
            "semantic_dependency_digest",
            "semantic_dependency_digests",
            "dimension_refs",
            "time_dimension_ref",
            "slice_predicates",
            "cohort",
        } & set(record)
        if forbidden_subject_fields:
            raise ValueError(
                "analysis SubjectSet job rejects unrelated semantic fields "
                f"{sorted(forbidden_subject_fields)}"
            )
        _validate_subject_set_payload(record["subject_set"])
        persisted = {"schema": "marivo.analysis_job/v2", **record}
        _persist_run_success_from_legacy_record(session, persisted)
        return
    has_digest = "semantic_dependency_digest" in record
    has_digests = "semantic_dependency_digests" in record
    if has_digest == has_digests:
        raise ValueError(
            "analysis job requires exactly one semantic_dependency_digest or "
            "semantic_dependency_digests role"
        )
    if has_digest:
        _validate_dependency_digest_payload(
            record["semantic_dependency_digest"],
            role="semantic_dependency_digest",
        )
    else:
        digests = record["semantic_dependency_digests"]
        if not isinstance(digests, list) or not digests:
            raise ValueError("analysis job semantic_dependency_digests must be non-empty")
        for index, digest in enumerate(digests):
            _validate_dependency_digest_payload(
                digest,
                role=f"semantic_dependency_digests[{index}]",
            )
    for field in ("dimension_refs",):
        values = record.get(field)
        if not isinstance(values, list):
            raise ValueError(f"analysis job {field} must be a list")
        for payload in values:
            decoded = _decode_ref_payload(payload)
            if decoded.kind is not SemanticKind.DIMENSION:
                raise ValueError("analysis job dimension_refs entries must be dimension refs")
    time_payload = record.get("time_dimension_ref")
    if time_payload is not None:
        decoded = _decode_ref_payload(time_payload)
        if decoded.kind.value != "time_dimension":
            raise ValueError("analysis job time_dimension_ref must be time_dimension")
    predicates = record.get("slice_predicates")
    if not isinstance(predicates, list):
        raise ValueError("analysis job slice_predicates must be a list")
    for predicate in predicates:
        if not isinstance(predicate, dict) or set(predicate) != {"dimension_ref", "value"}:
            raise ValueError("analysis job slice predicate fields are invalid")
        decoded = _decode_ref_payload(predicate["dimension_ref"])
        if decoded.kind.value not in {"dimension", "time_dimension"}:
            raise ValueError("analysis job slice predicate requires a dimension ref")
    persisted = {"schema": "marivo.analysis_job/v2", **record}
    _persist_run_success_from_legacy_record(session, persisted)


def persist_reused_artifact_job(
    session: Session,
    *,
    intent: str,
    analysis_purpose: str | None,
    params: dict[str, Any],
    input_frame_refs: list[str],
    output_frame_ref: str,
    semantics: dict[str, Any],
    started_at: datetime,
    started_monotonic: float,
    semantic_project_root: str,
) -> str:
    """Record one invocation that reused an already-committed artifact.

    The artifact identity dedups, but every invocation must keep an
    independent, recoverable job record carrying its own analysis_purpose
    (issue #38).  The frame meta is never rewritten, so the artifact keeps its
    original producer/purpose while this job marks the reuse explicitly.
    """
    from marivo.analysis.session._runs import active_run_id

    job_ref = active_run_id() or f"run_{secrets.token_hex(12)}"
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": intent,
            **semantics,
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": input_frame_refs,
            "output_frame_ref": output_frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_ms": int((monotonic() - started_monotonic) * 1000),
            "status": "succeeded",
            "reused_artifact": True,
            "error": None,
            "semantic_project_root": semantic_project_root,
        },
    )
    return job_ref
