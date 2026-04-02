"""Proposition seeding run orchestration for the evidence pipeline (Phase 4e-3).

Implements the seeding run contract described in
``docs/analysis/evidence-engine/finding-proposition-seeding.md``
§Runtime Contract, §Matching Algorithm, and §v1 Template Catalog.

## Seeding run algorithm

1. Load committed findings for each ``trigger_finding_id``; skip missing.
2. Sort by ``(finding_type, artifact_id, finding_id)`` for deterministic ordering.
3. Route each finding to matching single-finding seed templates via
   ``seed_registry.find_by_finding_type(finding_type)``.
4. For each ``(finding, template)`` pair, evaluate the creation condition via
   the template-specific materializer.  A ``None`` return means the creation
   condition failed — no proposition is produced, no error is raised.
5. On creation condition pass: materialize the full proposition spec and call
   ``register_system_seeded_proposition()``.
6. Collect ``created_proposition_ids`` and ``existing_proposition_ids``.
7. Return ``SeedingRunResult`` with stable ``affected_proposition_ids``.

## Stability invariants

- Same ``trigger_finding_ids`` + same committed finding state + same registry
  snapshot → same ``affected_proposition_ids`` in the same order.
- Replay (same inputs, some/all propositions already exist) →
  ``created_proposition_ids = []``, ``affected_proposition_ids`` unchanged.
- ``affected_proposition_ids = sorted(set(created ∪ existing))``.

## Template catalogue (v1 single-finding)

T1  ``delta``               → ``change``          via ``_materialize_change_from_delta``
T2  ``decomposition_item``  → ``decomposition``   via ``_materialize_decomposition_from_item``
T3  ``anomaly_candidate``   → ``anomaly``         via ``_materialize_anomaly_from_candidate``
T4  ``correlation_result``  → ``correlation``     via ``_materialize_correlation_from_result``
T5  ``test_result``         → ``test_hypothesis`` via ``_materialize_test_from_result``
T6  ``forecast_point``      → ``forecast``        via ``_materialize_forecast_from_point``

``observation`` findings have no registered template; they are silently skipped.

Phase: 4e-3
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol, TypedDict, cast

from app.evidence_engine.proposition_registration import register_system_seeded_proposition
from app.evidence_engine.proposition_seed_registry import (
    SeedTemplateRegistry,
    SeedTemplateSpec,
    default_seed_registry,
)
from app.storage.evidence_repositories import FindingRepository, PropositionRepository
from app.storage.metadata import MetadataStore

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SEEDING_RUN_SCHEMA_VERSION = "finding_proposition_seeding_run.v1"


# ---------------------------------------------------------------------------
# SeedingRunResult
# ---------------------------------------------------------------------------


class SeedingRunResult(TypedDict):
    """Stable output contract for a single seeding run.

    All three ID lists are derived from the same run; their union equals
    ``affected_proposition_ids`` (which is sorted and deduplicated).

    Fields
    ------
    created_proposition_ids:
        Proposition IDs that were newly registered in this run
        (``register_system_seeded_proposition`` returned ``created=True``).
    existing_proposition_ids:
        Proposition IDs that already existed and were returned as registration
        hits (``created=False``).  The propositions themselves are NOT modified.
    affected_proposition_ids:
        ``sorted(set(created ∪ existing))``.  This is the canonical handoff set
        for assessment recompute — both new and hit propositions require
        recompute because the trigger findings may have changed their evidence.
    schema_version:
        Always ``SEEDING_RUN_SCHEMA_VERSION``.
    """

    created_proposition_ids: list[str]
    existing_proposition_ids: list[str]
    affected_proposition_ids: list[str]
    schema_version: str


# ---------------------------------------------------------------------------
# MaterializationContext protocol
# ---------------------------------------------------------------------------


class MaterializationContext(Protocol):
    """Context for resolving canonical refs during proposition materialization.

    Template materializers use this to dereference committed findings and
    artifact payloads without exceeding their authority boundary.
    Only committed canonical objects may be returned.
    """

    def get_finding(self, session_id: str, finding_id: str) -> dict[str, Any] | None:
        """Return the deserialized finding row or ``None`` if not found."""
        ...

    def get_artifact_payload(self, artifact_id: str) -> dict[str, Any] | None:
        """Return the deserialized ``content_json`` of the artifact or ``None``."""
        ...


# ---------------------------------------------------------------------------
# SimpleMaterializationContext
# ---------------------------------------------------------------------------


class SimpleMaterializationContext:
    """Concrete :class:`MaterializationContext` backed by the metadata store.

    Parameters
    ----------
    finding_repo:
        Repository for committed findings.
    metadata:
        Metadata store — used to query ``artifacts.content_json`` directly.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        metadata: MetadataStore,
    ) -> None:
        self._finding_repo = finding_repo
        self._metadata = metadata

    def get_finding(self, session_id: str, finding_id: str) -> dict[str, Any] | None:
        row = self._finding_repo.get(finding_id)
        if row is None:
            return None
        if row.get("session_id") != session_id:
            return None
        return row

    def get_artifact_payload(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._metadata.query_one(
            "SELECT content_json FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        if row is None or row.get("content_json") is None:
            return None
        content = row["content_json"]
        if isinstance(content, str):
            return cast("dict[str, Any]", json.loads(content))
        return cast("dict[str, Any]", content)


# ---------------------------------------------------------------------------
# Internal proposition spec TypedDict
# ---------------------------------------------------------------------------


class _PropositionSpec(TypedDict):
    """Intermediate proposition representation produced by a template materializer.

    Contains all fields needed to call ``register_system_seeded_proposition()``.
    """

    proposition_type: str
    subject: dict[str, Any]
    assessment_anchor: dict[str, Any]
    lineage: dict[str, Any]
    payload: dict[str, Any]
    seed_finding_refs: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal builder helpers
# ---------------------------------------------------------------------------


def _build_origin(template: SeedTemplateSpec) -> dict[str, Any]:
    return {
        "kind": "system_seeded",
        "template_id": template["template_id"],
        "template_version": template["template_version"],
    }


def _build_lineage(
    finding: dict[str, Any],
    template: SeedTemplateSpec,
    extra_artifact_ids: list[str] | None = None,
    extra_step_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the proposition lineage from the trigger finding and template.

    Parameters
    ----------
    finding:
        Deserialized finding row (JSON columns already parsed).
    template:
        The seed template that produced this proposition.
    extra_artifact_ids:
        Additional artifact IDs to include in ``source_artifact_lineages``
        (e.g. for T2 which involves both a decomposition artifact and a compare
        artifact).
    extra_step_refs:
        Additional step refs to include in ``source_step_refs``
        (e.g. for T2 which involves the delta finding's compare step as well).
    """
    artifact_ids: list[str] = [finding["artifact_id"]]
    if extra_artifact_ids:
        for aid in extra_artifact_ids:
            if aid and aid not in artifact_ids:
                artifact_ids.append(aid)
    artifact_ids.sort()  # stable lexical order (schema §Lineage rules)

    source_artifact_lineages = [
        {"artifact_id": aid, "artifact_schema_version": None, "extractor_version": None}
        for aid in artifact_ids
    ]

    # step_ref_json has already been deserialized by FindingRepository.get()
    step_ref = finding["step_ref_json"]
    step_refs: list[dict[str, Any]] = [step_ref] if step_ref else []
    if extra_step_refs:
        seen_step_ids: set[str] = {s["step_id"] for s in step_refs if s.get("step_id")}
        for sr in extra_step_refs:
            if sr and sr.get("step_id") and sr["step_id"] not in seen_step_ids:
                step_refs.append(sr)
                seen_step_ids.add(sr["step_id"])
    step_refs.sort(key=lambda s: s.get("step_id", ""))

    return {
        "creation_mode": "seeded",
        "source_artifact_lineages": source_artifact_lineages,
        "source_step_refs": step_refs,
        "derived_from_proposition_ref": None,
        "derivation_version": template["derivation_version"],
    }


def _seed_ref_primary(session_id: str, finding_id: str) -> dict[str, Any]:
    return {
        "finding_ref": {"session_id": session_id, "finding_id": finding_id},
        "role": "primary",
    }


def _seed_ref_context(session_id: str, finding_id: str) -> dict[str, Any]:
    return {
        "finding_ref": {"session_id": session_id, "finding_id": finding_id},
        "role": "context",
    }


# ---------------------------------------------------------------------------
# Segment key decode helper (used by T1 segmented_delta dimension_keys)
# ---------------------------------------------------------------------------


def _decode_seg_component(s: str) -> str:
    """Reverse the percent-encoding applied by compare_extractor._escape_seg_component."""
    # Decode order: %7C → |, %3D → =, %25 → % (reverse of encode order)
    return s.replace("%7C", "|").replace("%3D", "=").replace("%25", "%")


def _parse_segment_key(canonical_item_key: str) -> dict[str, str] | None:
    """Parse the segment portion of a 'rows:k=v|k=v' canonical_item_key.

    Returns a ``str→str`` dict of dimension key-value pairs, or ``None``
    if the key does not start with ``"rows:"`` or is malformed.

    Note: values are always strings after decoding because the encoding does
    not preserve original value types.  Callers should treat these as
    string-typed dimension values.
    """
    if not canonical_item_key.startswith("rows:"):
        return None
    seg_part = canonical_item_key[5:]
    if not seg_part:
        return None
    result: dict[str, str] = {}
    for pair in seg_part.split("|"):
        if "=" not in pair:
            return None
        raw_k, raw_v = pair.split("=", 1)
        result[_decode_seg_component(raw_k)] = _decode_seg_component(raw_v)
    return result


# ---------------------------------------------------------------------------
# CorrelationJoinBasis parser
# ---------------------------------------------------------------------------

_VALID_GRAINS = frozenset({"hour", "day", "week", "month"})
_VALID_JOIN_BASIS_KINDS = frozenset({"time_aligned", "shared_key"})


def _parse_correlation_join_basis(raw: Any) -> dict[str, Any] | None:
    """Try to parse *raw* as a structured ``CorrelationJoinBasis``.

    v1 rule (finding-proposition-seeding.md T4 §Creation condition):
    If the join_basis cannot be parsed as a structured CorrelationJoinBasis,
    the creation condition is False.

    A structured join_basis must be a dict with ``kind`` in
    ``{"time_aligned", "shared_key"}``.  A plain string cannot be parsed
    and returns ``None``.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in _VALID_JOIN_BASIS_KINDS:
        return None
    key_fields = list(raw.get("key_fields") or [])
    if kind == "time_aligned":
        grain = raw.get("grain")
        if grain not in _VALID_GRAINS:
            return None
        return {"kind": "time_aligned", "grain": grain, "key_fields": key_fields}
    if kind == "shared_key":
        grain = raw.get("grain")
        if grain is not None and grain not in _VALID_GRAINS:
            return None
        return {"kind": "shared_key", "key_fields": key_fields, "grain": grain}
    return None


# ---------------------------------------------------------------------------
# Canonical subject key (for bilateral focus-anchor ordering in T4/T5)
# ---------------------------------------------------------------------------


def _canonical_subject_key(subject: dict[str, Any]) -> str:
    """Stable sort key for a PropositionSubject dict.

    Used by T4/T5 bilateral focus-anchor algorithm:
    base_subject = left if canonical_key(left) <= canonical_key(right) else right.
    """
    return json.dumps(
        {
            "entity": subject.get("entity"),
            "grain": subject.get("grain"),
            "metric": subject.get("metric"),
            "slice": subject.get("slice", {}),
        },
        sort_keys=True,
    )


def _bilateral_focus_anchor(
    left_subject: dict[str, Any],
    right_subject: dict[str, Any],
    analysis_axis: str,
) -> dict[str, Any]:
    """Derive the base subject for a bilateral proposition (T4/T5).

    Rule (finding-proposition-seeding.md §Subject rules):
    - Compute canonical subject key for left and right.
    - Take the lexically smaller one; on equal, take left.
    - Set ``analysis_axis`` to the provided value.
    """
    base = (
        left_subject
        if _canonical_subject_key(left_subject) <= _canonical_subject_key(right_subject)
        else right_subject
    )
    return {**base, "analysis_axis": analysis_axis}


# ---------------------------------------------------------------------------
# Template materializers (T1-T6)
# ---------------------------------------------------------------------------


def _materialize_change_from_delta(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T1: delta finding → change proposition.

    Creation condition rules (finding-proposition-seeding.md T1):
    - direction == "flat" → creation condition false.
    - direction == "undefined" + presence not in {left_only, right_only} → false.
    - comparison_window (left/right time scopes) must be resolvable from artifact.
    - segmented_delta: dimension_keys must be parseable from canonical_item_key.
    """
    payload = finding["payload_json"]
    direction: str = payload.get("direction") or "undefined"
    presence: str | None = payload.get("presence")
    delta_kind: str = payload.get("delta_kind") or ""

    # Creation condition: direction check
    if direction == "flat":
        return None
    if direction == "undefined" and presence not in ("left_only", "right_only"):
        return None

    # direction_of_interest mapping
    if direction == "increase":
        direction_of_interest = "increase"
    elif direction == "decrease":
        direction_of_interest = "decrease"
    else:  # undefined + left_only or right_only
        direction_of_interest = "any_non_flat"

    # change_kind mapping
    if delta_kind == "scalar_delta":
        change_kind = "scalar_change"
    elif delta_kind == "segmented_delta":
        change_kind = "segment_change"
    else:
        return None

    # comparison_window: resolve from compare artifact
    artifact_payload = ctx.get_artifact_payload(finding["artifact_id"])
    if not artifact_payload:
        return None
    resolved = artifact_payload.get("resolved_input_summary") or {}
    left_time_scope = resolved.get("left_time_scope")
    right_time_scope = resolved.get("right_time_scope")
    if not left_time_scope or not right_time_scope:
        return None
    comparison_window = {"left": left_time_scope, "right": right_time_scope}

    # comparison_basis: from artifact if available, else default
    comparison_basis = artifact_payload.get("comparison_basis") or "left_vs_right"

    # dimension_keys: scalar_delta → None; segmented_delta → parse from canonical_item_key
    dimension_keys: dict[str, Any] | None = None
    if delta_kind == "segmented_delta":
        provenance = finding.get("provenance_json") or {}
        canonical_item_key: str = provenance.get("canonical_item_key") or ""
        dimension_keys = _parse_segment_key(canonical_item_key)
        if dimension_keys is None:
            return None

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "change"}

    prop_payload: dict[str, Any] = {
        "change_kind": change_kind,
        "comparison_window": comparison_window,
        "direction_of_interest": direction_of_interest,
        "comparison_basis": comparison_basis,
        "unit": payload.get("unit"),
        "dimension_keys": dimension_keys,
    }

    return _PropositionSpec(
        proposition_type="change",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=_build_lineage(finding, template),
        payload=prop_payload,
        seed_finding_refs=[_seed_ref_primary(session_id, finding["finding_id"])],
    )


def _materialize_decomposition_from_item(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T2: decomposition_item finding → decomposition proposition.

    Creation condition rules (finding-proposition-seeding.md T2):
    - contribution_value == 0 AND contribution_share == 0 → false.
    - contribution_value is None AND contribution_share is None → false.
    - scope_delta_ref must resolve to a committed delta finding.
    - comparison_window must be resolvable from the delta finding's compare artifact.
    - contribution_role must be determinable.
    """
    payload = finding["payload_json"]

    # Creation condition: zero contribution check
    cv = payload.get("contribution_value")
    cs = payload.get("contribution_share")
    if cv is None and cs is None:
        return None
    if cv is not None and cs is not None and cv == 0 and cs == 0:
        return None

    scope_delta_ref = payload.get("scope_delta_ref")
    if not scope_delta_ref or not scope_delta_ref.get("finding_id"):
        return None

    # Resolve scope_delta_ref → delta finding
    delta_finding = ctx.get_finding(session_id, scope_delta_ref["finding_id"])
    if delta_finding is None:
        return None

    # Resolve comparison_window from the delta finding's compare artifact
    delta_payload = delta_finding.get("payload_json") or {}
    artifact_payload = ctx.get_artifact_payload(delta_finding["artifact_id"])
    if not artifact_payload:
        return None
    resolved = artifact_payload.get("resolved_input_summary") or {}
    left_time_scope = resolved.get("left_time_scope")
    right_time_scope = resolved.get("right_time_scope")
    if not left_time_scope or not right_time_scope:
        return None
    comparison_window = {"left": left_time_scope, "right": right_time_scope}

    # contribution_role: compare scope delta direction with item direction
    scope_direction: str = delta_payload.get("direction") or "undefined"
    item_direction: str = payload.get("direction") or "undefined"
    rank: int | None = payload.get("rank")

    contribution_role: str | None = None
    _directional = frozenset({"increase", "decrease"})
    if scope_direction in _directional and item_direction in _directional:
        if scope_direction != item_direction:
            contribution_role = "offsetting_factor"
        else:
            if rank == 1:
                contribution_role = "primary_driver"
            elif rank is not None and rank > 1:
                contribution_role = "secondary_driver"
            else:
                # rank unknown but same direction
                contribution_role = "secondary_driver"
    else:
        # Directions not comparable (undefined/flat) but contribution is nonzero
        cv_nonzero = cv is not None and cv != 0
        cs_nonzero = cs is not None and cs != 0
        if cv_nonzero or cs_nonzero:
            contribution_role = "material_component"

    if contribution_role is None:
        return None

    dimension = payload.get("dimension") or ""
    dimension_keys = payload.get("keys") or {}

    if not dimension or not dimension_keys:
        return None

    # subject: from trigger (decomposition_item) finding's subject (schema T2 Resolution)
    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "decomposition"}

    prop_payload: dict[str, Any] = {
        "dimension": dimension,
        "dimension_keys": dimension_keys,
        "contribution_role": contribution_role,
        "scope_delta_ref": scope_delta_ref,
        "comparison_window": comparison_window,
    }

    lineage = _build_lineage(
        finding,
        template,
        extra_artifact_ids=[delta_finding["artifact_id"]],
        extra_step_refs=[delta_finding["step_ref_json"]],
    )

    return _PropositionSpec(
        proposition_type="decomposition",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=lineage,
        payload=prop_payload,
        seed_finding_refs=[
            _seed_ref_primary(session_id, finding["finding_id"]),
            _seed_ref_context(session_id, scope_delta_ref["finding_id"]),
        ],
    )


def _materialize_anomaly_from_candidate(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T3: anomaly_candidate finding → anomaly proposition.

    Creation condition rules (finding-proposition-seeding.md T3):
    - candidate_ref must be present and well-formed (has artifact_id).
    - observed_window must not be None.
    """
    payload = finding["payload_json"]
    candidate_ref = payload.get("candidate_ref")
    observed_window = finding.get("observed_window_json")

    if not candidate_ref or not candidate_ref.get("artifact_id"):
        return None
    if not observed_window:
        return None

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "anomaly"}

    prop_payload: dict[str, Any] = {
        "anomaly_kind": "candidate",
        "candidate_ref": candidate_ref,
        "expected_behavior_ref": None,
        "observed_window": observed_window,
        "validation_goal": "validate_candidate",
    }

    return _PropositionSpec(
        proposition_type="anomaly",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=_build_lineage(finding, template),
        payload=prop_payload,
        seed_finding_refs=[_seed_ref_primary(session_id, finding["finding_id"])],
    )


def _materialize_correlation_from_result(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T4: correlation_result finding → correlation proposition.

    Creation condition rules (finding-proposition-seeding.md T4):
    - left_subject / right_subject must be resolvable from the correlate artifact.
    - join_basis must be parseable as a structured CorrelationJoinBasis dict.
    - observed_window (aligned_window) must not be None.
    """
    payload = finding["payload_json"]
    observed_window = finding.get("observed_window_json")
    if not observed_window:
        return None

    # join_basis: must be parseable as structured CorrelationJoinBasis
    raw_join_basis = payload.get("join_basis")
    join_basis = _parse_correlation_join_basis(raw_join_basis)
    if join_basis is None:
        return None

    # left/right subjects: from correlate artifact
    artifact_payload = ctx.get_artifact_payload(finding["artifact_id"])
    if not artifact_payload:
        return None
    left_metric: str | None = artifact_payload.get("left_metric")
    right_metric: str | None = artifact_payload.get("right_metric")
    if not left_metric or not right_metric:
        return None

    # Build left_subject / right_subject as PropositionSubjects
    left_subject: dict[str, Any] = {
        "metric": left_metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "correlation",
    }
    right_subject: dict[str, Any] = {
        "metric": right_metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "correlation",
    }

    # relationship_of_interest from coefficient
    coefficient = payload.get("coefficient")
    if coefficient is not None:
        if coefficient > 0:
            relationship_of_interest = "positive_association"
        elif coefficient < 0:
            relationship_of_interest = "negative_association"
        else:
            relationship_of_interest = "any_association"
    else:
        relationship_of_interest = "any_association"

    # Focus-anchor base subject
    subject = _bilateral_focus_anchor(left_subject, right_subject, "correlation")

    method: str = payload.get("method") or "auto"

    prop_payload: dict[str, Any] = {
        "left_subject": left_subject,
        "right_subject": right_subject,
        "method_family": method,
        "relationship_of_interest": relationship_of_interest,
        "join_basis": join_basis,
        "aligned_window": observed_window,
    }

    return _PropositionSpec(
        proposition_type="correlation",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=_build_lineage(finding, template),
        payload=prop_payload,
        seed_finding_refs=[_seed_ref_primary(session_id, finding["finding_id"])],
    )


def _materialize_test_from_result(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T5: test_result finding → test_hypothesis proposition.

    Creation condition rules (finding-proposition-seeding.md T5):
    - left_subject / right_subject must be resolvable from upstream observation
      artifacts (left_ref.artifact_id / right_ref.artifact_id).
    - alpha must be a valid non-None float.
    """
    payload = finding["payload_json"]

    alpha = payload.get("alpha")
    if alpha is None:
        return None
    try:
        alpha_float = float(alpha)
    except (TypeError, ValueError):
        return None

    # left/right subjects: from upstream observation artifacts
    left_ref: dict[str, Any] = payload.get("left_ref") or {}
    right_ref: dict[str, Any] = payload.get("right_ref") or {}
    left_artifact_id: str = left_ref.get("artifact_id") or ""
    right_artifact_id: str = right_ref.get("artifact_id") or ""

    if not left_artifact_id or not right_artifact_id:
        return None

    left_artifact = ctx.get_artifact_payload(left_artifact_id)
    right_artifact = ctx.get_artifact_payload(right_artifact_id)
    if not left_artifact or not right_artifact:
        return None

    left_metric: str | None = left_artifact.get("metric")
    right_metric: str | None = right_artifact.get("metric")
    if not left_metric or not right_metric:
        return None

    left_subject: dict[str, Any] = {
        "metric": left_metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "test",
    }
    right_subject: dict[str, Any] = {
        "metric": right_metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "test",
    }

    subject = _bilateral_focus_anchor(left_subject, right_subject, "test")
    method: str = payload.get("method") or "auto"

    prop_payload: dict[str, Any] = {
        "hypothesis_family": "difference",
        "alternative": "two_sided",
        "left_subject": left_subject,
        "right_subject": right_subject,
        "method_family": method,
        "alpha": alpha_float,
        "hypothesis_label": None,
    }

    return _PropositionSpec(
        proposition_type="test_hypothesis",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=_build_lineage(finding, template),
        payload=prop_payload,
        seed_finding_refs=[_seed_ref_primary(session_id, finding["finding_id"])],
    )


def _materialize_forecast_from_point(
    finding: dict[str, Any],
    session_id: str,
    template: SeedTemplateSpec,
    ctx: MaterializationContext,
) -> _PropositionSpec | None:
    """T6: forecast_point finding → forecast proposition.

    Creation condition rules (finding-proposition-seeding.md T6):
    - bucket_start and bucket_end must be non-empty strings.
    - horizon_index must be a non-None non-negative integer.
    """
    payload = finding["payload_json"]

    bucket_start: str = payload.get("bucket_start") or ""
    bucket_end: str = payload.get("bucket_end") or ""
    if not bucket_start or not bucket_end:
        return None

    horizon_index = payload.get("horizon_index")
    if horizon_index is None:
        return None
    try:
        horizon_int = int(horizon_index)
    except (TypeError, ValueError):
        return None
    if horizon_int < 0:
        return None

    prediction_interval = payload.get("prediction_interval")
    forecast_kind = "interval_forecast" if prediction_interval is not None else "point_forecast"

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "forecast"}

    prop_payload: dict[str, Any] = {
        "forecast_kind": forecast_kind,
        "forecast_window": {"kind": "range", "start": bucket_start, "end": bucket_end},
        "horizon_index": horizon_int,
        "expectation_direction": "open",
        "forecast_basis_ref": {"session_id": session_id, "finding_id": finding["finding_id"]},
    }

    return _PropositionSpec(
        proposition_type="forecast",
        subject=subject,
        assessment_anchor={"assessment_type": template["assessment_type"]},
        lineage=_build_lineage(finding, template),
        payload=prop_payload,
        seed_finding_refs=[_seed_ref_primary(session_id, finding["finding_id"])],
    )


# ---------------------------------------------------------------------------
# Materializer dispatcher
# ---------------------------------------------------------------------------

_MaterializerFn = Callable[
    [dict[str, Any], str, SeedTemplateSpec, MaterializationContext],
    _PropositionSpec | None,
]

_MATERIALIZER_DISPATCH: dict[str, _MaterializerFn] = {
    "delta": _materialize_change_from_delta,
    "decomposition_item": _materialize_decomposition_from_item,
    "anomaly_candidate": _materialize_anomaly_from_candidate,
    "correlation_result": _materialize_correlation_from_result,
    "test_result": _materialize_test_from_result,
    "forecast_point": _materialize_forecast_from_point,
}


# ---------------------------------------------------------------------------
# Public seeding run entry point
# ---------------------------------------------------------------------------


def run_system_seeded_propositions(
    *,
    session_id: str,
    trigger_finding_ids: list[str],
    proposition_repo: PropositionRepository,
    finding_repo: FindingRepository,
    ctx: MaterializationContext,
    seed_registry: SeedTemplateRegistry | None = None,
) -> SeedingRunResult:
    """Run system-seeded proposition registration for a batch of committed findings.

    Parameters
    ----------
    session_id:
        The session that owns all trigger findings and the resulting propositions.
    trigger_finding_ids:
        IDs of the committed findings that entered the seeding pipeline.
        Order does not affect output ordering — findings are sorted internally.
    proposition_repo:
        Repository for reading/writing propositions.
    finding_repo:
        Repository for loading trigger findings and resolving ``scope_delta_ref``
        in T2 templates.
    ctx:
        Materialization context for artifact payload dereference.
    seed_registry:
        Template registry to use.  Defaults to ``default_seed_registry``
        (the v1 bootstrap registry).

    Returns
    -------
    SeedingRunResult
        Stable output with ``created_proposition_ids``,
        ``existing_proposition_ids``, and ``affected_proposition_ids``.
        All three lists are sorted.  ``affected_proposition_ids`` is
        ``sorted(set(created ∪ existing))``.
    """
    if seed_registry is None:
        seed_registry = default_seed_registry

    # --- Phase 1: load and sort trigger findings ----------------------------
    loaded: list[dict[str, Any]] = []
    for fid in trigger_finding_ids:
        f = finding_repo.get(fid)
        if f is not None:
            loaded.append(f)

    # Stable sort: (finding_type, artifact_id, finding_id)
    findings = sorted(
        loaded,
        key=lambda f: (f["finding_type"], f["artifact_id"], f["finding_id"]),
    )

    # --- Phase 2: route, materialize, and register --------------------------
    created_ids: list[str] = []
    existing_ids: list[str] = []

    for finding in findings:
        finding_type: str = finding["finding_type"]
        templates = seed_registry.find_by_finding_type(finding_type)

        for template in templates:
            materializer = _MATERIALIZER_DISPATCH.get(finding_type)
            if materializer is None:
                continue

            spec: _PropositionSpec | None = materializer(finding, session_id, template, ctx)
            if spec is None:
                # Creation condition failed — skip silently
                continue

            origin = _build_origin(template)

            result = register_system_seeded_proposition(
                proposition_repo,
                session_id=session_id,
                proposition_type=spec["proposition_type"],
                subject=spec["subject"],
                origin=origin,
                assessment_anchor=spec["assessment_anchor"],
                lineage=spec["lineage"],
                payload=spec["payload"],
                seed_finding_refs=spec["seed_finding_refs"],
            )

            pid = result["proposition_id"]
            if result["created"]:
                created_ids.append(pid)
            else:
                existing_ids.append(pid)

    # --- Phase 3: build output ----------------------------------------------
    affected_set = sorted(set(created_ids) | set(existing_ids))

    return SeedingRunResult(
        created_proposition_ids=sorted(created_ids),
        existing_proposition_ids=sorted(existing_ids),
        affected_proposition_ids=affected_set,
        schema_version=SEEDING_RUN_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "SEEDING_RUN_SCHEMA_VERSION",
    "MaterializationContext",
    "SeedingRunResult",
    "SimpleMaterializationContext",
    "run_system_seeded_propositions",
]
