"""Session-level canonical state surface materializer (Phase 5b).

Exposes:

* :func:`materialize_session_state_view` — builds a ``SessionStateView`` dict
  from the canonical DB state for a given session.

The view is exclusively derived from *externally visible* proposition-local
bundles.  Propositions with no publish switch executed yet appear with
``latest_assessment = null`` — they are *live* judgment-track objects, not
absent data.

Runtime scheduling truth (claim, lease, retry, backlog) must **not** enter the
returned dict.  That belongs to the operator-facing runtime status surface.

Design reference: ``docs/analysis/evidence-engine/schemas/state-surface-schema.md``
HTTP contract: ``docs/api/session-state.md``
"""

from __future__ import annotations

import json
from typing import Any

from app.evidence_engine.publish_switch import assemble_externally_visible_bundle
from app.evidence_engine.ref_boundary import assert_no_semantic_refs_in_canonical_payload
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)

SESSION_STATE_VIEW_SCHEMA_VERSION = "session_state_view.v1"
_DEFAULT_LIMIT = 50
_DEFAULT_SORT_KEY = "default_active_proposition_order_v1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stable_dedup(items: list[Any], key_fn: Any) -> list[Any]:
    """Return *items* deduplicated by *key_fn*, preserving first-seen order."""
    seen: set[Any] = set()
    result = []
    for item in items:
        k = key_fn(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result


def _canonical_slice_key(slice_val: Any) -> str:
    """Stable sort key for a proposition subject slice."""
    if not slice_val:
        return ""
    if isinstance(slice_val, dict):
        return json.dumps(slice_val, sort_keys=True, separators=(",", ":"))
    return str(slice_val)


def _build_active_proposition_entry(
    *,
    session_id: str,
    proposition: dict[str, Any],
    assessment_repo: AssessmentRepository,
    finding_repo: FindingRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    proposal_repo: ActionProposalRepository,
    proposition_repo: PropositionRepository,
) -> dict[str, Any]:
    """Assemble one ``ActivePropositionEntry`` for *proposition*.

    Returns the entry dict.  ``latest_assessment`` is ``None`` when the
    proposition has no externally visible bundle yet; in that case all
    assessment-derived ref fields are ``None`` (not ``[]``).
    """
    proposition_id: str = proposition["proposition_id"]

    bundle = assemble_externally_visible_bundle(
        session_id=session_id,
        proposition_id=proposition_id,
        assessment_repo=assessment_repo,
        gap_repo=gap_repo,
        finding_repo=finding_repo,
        proposal_repo=proposal_repo,
        inference_record_repo=inference_record_repo,
        proposition_repo=proposition_repo,
    )

    if bundle is None:
        draft_entry: dict[str, Any] = {
            "proposition": proposition,
            "latest_assessment": None,
            "supporting_finding_refs": None,
            "opposing_finding_refs": None,
            "blocking_gap_refs": None,
            "non_blocking_gap_refs": None,
            "applied_inference_record_refs": None,
            "artifact_refs": [],
            # Private: hydrated findings for backing_findings collection (stripped before response).
            "_live_findings": [],
        }
        assert_no_semantic_refs_in_canonical_payload(
            {key: value for key, value in draft_entry.items() if not key.startswith("_")},
            surface="session_state_entry",
        )
        return draft_entry

    latest_assessment: dict[str, Any] = bundle["latest_assessment"]
    live_closure = bundle["live_closure"]

    # --- finding refs (live support / oppose) --------------------------------
    supporting_finding_refs = [
        {"session_id": session_id, "finding_id": f["finding_id"]}
        for f in live_closure["supporting_findings"]
    ]
    opposing_finding_refs = [
        {"session_id": session_id, "finding_id": f["finding_id"]}
        for f in live_closure["opposing_findings"]
    ]

    # --- gap refs (split on blocking flag from assessment snapshot) ----------
    # gap_memberships_json is already deserialized by AssessmentRepository.get()
    gap_memberships: list[dict[str, Any]] = latest_assessment.get("gap_memberships_json") or []

    blocking_gap_refs = [
        {"gap_id": m["gap_ref"]["gap_id"], "proposition_id": proposition_id}
        for m in gap_memberships
        if m.get("blocking")
    ]
    non_blocking_gap_refs = [
        {"gap_id": m["gap_ref"]["gap_id"], "proposition_id": proposition_id}
        for m in gap_memberships
        if not m.get("blocking")
    ]

    # --- inference record refs -----------------------------------------------
    applied_inference_record_refs = [
        {
            "inference_record_id": r["inference_record_id"],
            "proposition_id": proposition_id,
            "assessment_id": r["assessment_id"],
        }
        for r in live_closure["applied_inference_records"]
    ]

    # --- artifact refs (from support + oppose findings, dedup by artifact_id) -
    all_findings = list(live_closure["supporting_findings"]) + list(
        live_closure["opposing_findings"]
    )
    artifact_refs = _stable_dedup(
        [
            {
                "artifact_id": f["artifact_id"],
                "step_ref": f["step_ref_json"],
            }
            for f in all_findings
            if f.get("artifact_id") and f.get("step_ref_json")
        ],
        key_fn=lambda r: r["artifact_id"],
    )

    published_entry: dict[str, Any] = {
        "proposition": proposition,
        "latest_assessment": latest_assessment,
        "supporting_finding_refs": supporting_finding_refs,
        "opposing_finding_refs": opposing_finding_refs,
        "blocking_gap_refs": blocking_gap_refs,
        "non_blocking_gap_refs": non_blocking_gap_refs,
        "applied_inference_record_refs": applied_inference_record_refs,
        "artifact_refs": artifact_refs,
        # Private: hydrated findings for backing_findings collection (stripped before response).
        "_live_findings": all_findings,
    }
    assert_no_semantic_refs_in_canonical_payload(
        {key: value for key, value in published_entry.items() if not key.startswith("_")},
        surface="session_state_entry",
    )
    return published_entry


def _apply_query_filters(
    entries: list[dict[str, Any]],
    query: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return filtered *entries*.

    The canonical case where ``assessment_presence="unassessed"`` is combined
    with ``assessment_statuses`` always returns ``[]`` (never a validation error).
    """
    assessment_presence: str | None = query.get("assessment_presence")
    assessment_statuses: list[str] | None = query.get("assessment_statuses")

    # canonical empty: unassessed + status filter → always empty
    if assessment_presence == "unassessed" and assessment_statuses:
        return []

    metric: str | None = query.get("metric")
    entity: str | None = query.get("entity")
    slice_filter: dict[str, Any] | None = query.get("slice")
    proposition_types: list[str] | None = query.get("proposition_types")
    origin_kinds: list[str] | None = query.get("origin_kinds")
    has_blocking_gaps: bool | None = query.get("has_blocking_gaps")

    result = []
    for entry in entries:
        prop: dict[str, Any] = entry["proposition"]
        subject: dict[str, Any] = prop.get("subject_json") or {}
        latest_assessment = entry["latest_assessment"]

        # --- metric / entity / slice -----------------------------------------
        if metric is not None and subject.get("metric") != metric:
            continue
        if entity is not None and subject.get("entity") != entity:
            continue
        if slice_filter is not None:
            prop_slice: dict[str, Any] = subject.get("slice") or {}
            # subset exact match: every k=v in slice_filter must be in prop_slice
            if any(prop_slice.get(k) != v for k, v in slice_filter.items()):
                continue

        # --- proposition_types -----------------------------------------------
        if proposition_types and prop.get("proposition_type") not in proposition_types:
            continue

        # --- origin_kinds ----------------------------------------------------
        if origin_kinds:
            origin: dict[str, Any] = prop.get("origin_json") or {}
            if origin.get("kind") not in origin_kinds:
                continue

        # --- assessment_presence ---------------------------------------------
        if assessment_presence == "assessed" and latest_assessment is None:
            continue
        if assessment_presence == "unassessed" and latest_assessment is not None:
            continue

        # --- assessment_statuses (only when assessed) ------------------------
        if (
            assessment_statuses
            and latest_assessment is not None
            and latest_assessment.get("status") not in assessment_statuses
        ):
            continue

        # --- has_blocking_gaps -----------------------------------------------
        blocking_gap_refs = entry.get("blocking_gap_refs") or []
        if has_blocking_gaps is True and (latest_assessment is None or len(blocking_gap_refs) == 0):
            # must be assessed + have blocking gaps
            continue
        if has_blocking_gaps is False and (latest_assessment is None or len(blocking_gap_refs) > 0):
            # must be assessed + no blocking gaps (unassessed excluded per spec)
            continue

        result.append(entry)

    return result


def _sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    """7-key canonical sort (ascending overall; highest-priority first)."""
    prop: dict[str, Any] = entry["proposition"]
    latest_assessment = entry["latest_assessment"]
    blocking_gap_refs = entry.get("blocking_gap_refs") or []

    is_assessed = latest_assessment is not None
    has_blocking = is_assessed and len(blocking_gap_refs) > 0
    blocking_count = len(blocking_gap_refs) if is_assessed else 0

    # Python tuple sort: (k1, k2, ...) ascending.
    # For "True first" / "descending" keys we negate booleans / counts.
    assessment_created_at: str = latest_assessment.get("created_at", "") if is_assessed else ""
    subject: dict[str, Any] = prop.get("subject_json") or {}
    metric: str | None = subject.get("metric")
    slice_key: str = _canonical_slice_key(subject.get("slice"))
    proposition_id: str = prop.get("proposition_id", "")
    proposition_type: str = prop.get("proposition_type", "")

    return (
        0 if has_blocking else 1,  # blocking first
        -blocking_count,  # more blocking gaps first
        # created_at descending → negate by string reversal trick isn't ideal,
        # but ISO 8601 strings sort lexicographically. Use negative sign on a
        # derived int is not possible for strings, so we negate via wrapper:
        _desc_str(assessment_created_at),  # newer assessments first; nulls last
        proposition_type,  # lexical ascending
        (1, "") if metric is None else (0, metric),  # nulls last
        slice_key,  # lexical ascending
        proposition_id,  # stable tiebreak
    )


def _desc_str(s: str) -> tuple[Any, ...]:
    """Sort helper: empty string sorts last, non-empty strings sort descending.

    The inversion maps each character c → chr(0x7F - ord(c)).  This is safe
    because ``assessment_created_at`` values are ISO 8601 ASCII strings
    (digits, hyphens, colons, 'T', 'Z') — all in the printable ASCII range
    [0x20, 0x7E].  Non-ASCII characters (ord ≥ 0x80) are silently dropped,
    which would corrupt the sort for non-ASCII input; callers must ensure this
    field only holds ISO 8601 strings.
    """
    if not s:
        return (1, "")
    # Prefix 0 so non-empty < empty; within non-empty, negate by reversing
    # code points — safe for ISO 8601 ASCII strings.
    inverted = "".join(chr(0x7F - ord(c)) for c in s if ord(c) < 0x80)
    return (0, inverted)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def materialize_session_state_view(
    *,
    session_id: str,
    query: dict[str, Any],
    proposition_repo: PropositionRepository,
    assessment_repo: AssessmentRepository,
    finding_repo: FindingRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    proposal_repo: ActionProposalRepository,
) -> dict[str, Any]:
    """Materialize a ``SessionStateView`` for *session_id*.

    ``query`` is a dict with optional keys matching ``SessionStateQuery`` fields
    plus the transport field ``page_token``.  All fields are optional; omitted
    fields apply no filter.

    The view is derived entirely from *externally visible* proposition-local
    bundles.  It must not expose runtime claim, lease, retry, or backlog state.

    Returns
    -------
    dict
        A ``SessionStateView``-shaped dict with ``schema_version`` and the
        transport field ``next_page_token`` (always ``null`` in v1).
    """
    # ------------------------------------------------------------------
    # 1. Load all active (non-invalidated) propositions for the session.
    #    Invalidation filter is pushed to SQL to avoid loading tombstoned rows.
    # ------------------------------------------------------------------
    all_propositions = proposition_repo.list_by_session(session_id, active_only=True)

    # ------------------------------------------------------------------
    # 2+3. Assemble ActivePropositionEntry for each proposition.
    # ------------------------------------------------------------------
    entries: list[dict[str, Any]] = []
    for prop in all_propositions:
        entry = _build_active_proposition_entry(
            session_id=session_id,
            proposition=prop,
            assessment_repo=assessment_repo,
            finding_repo=finding_repo,
            gap_repo=gap_repo,
            inference_record_repo=inference_record_repo,
            proposal_repo=proposal_repo,
            proposition_repo=proposition_repo,
        )
        entries.append(entry)

    # ------------------------------------------------------------------
    # 4. Apply SessionStateQuery filters.
    # ------------------------------------------------------------------
    filtered = _apply_query_filters(entries, query)

    # ------------------------------------------------------------------
    # 5. Sort by 7-key canonical order.
    # ------------------------------------------------------------------
    filtered.sort(key=_sort_key)

    # ------------------------------------------------------------------
    # 6. Apply limit + compute StateTruncation.
    # ------------------------------------------------------------------
    limit: int = int(query.get("limit") or _DEFAULT_LIMIT)
    total_count = len(filtered)
    is_truncated = total_count > limit
    returned_entries = filtered[:limit]

    truncation = {
        "is_truncated": is_truncated,
        "returned_count": len(returned_entries),
        "total_count": total_count,
        "sort_key": _DEFAULT_SORT_KEY,
        "applies_to": "active_propositions",
    }

    # ------------------------------------------------------------------
    # 7. backing_findings: dedup support+oppose findings from returned
    #    propositions only.
    #    Reuse the hydrated live_closure findings stored in _live_findings
    #    by _build_active_proposition_entry — avoids a second round of
    #    finding_repo.get() calls per finding ID.
    # ------------------------------------------------------------------
    backing_findings: list[dict[str, Any]] = []
    seen_finding_ids: set[str] = set()

    for entry in returned_entries:
        for f in entry.get("_live_findings") or []:
            fid: str = f["finding_id"]
            if fid not in seen_finding_ids:
                backing_findings.append(f)
                seen_finding_ids.add(fid)

    # Strip the internal _live_findings key before serialising the response.
    for entry in returned_entries:
        entry.pop("_live_findings", None)

    # ------------------------------------------------------------------
    # 8. focus_subjects: stable-dedup of backing_findings[*].subject_json.
    # ------------------------------------------------------------------
    focus_subjects: list[dict[str, Any]] = _stable_dedup(
        [f["subject_json"] for f in backing_findings if f.get("subject_json")],
        key_fn=lambda s: json.dumps(s, sort_keys=True, separators=(",", ":")),
    )

    # ------------------------------------------------------------------
    # 9a. blocking_gaps: hydrate unique blocking gap ids from returned
    #     propositions' blocking_gap_refs (status=open only).
    # ------------------------------------------------------------------
    seen_gap_ids: set[str] = set()
    blocking_gaps: list[dict[str, Any]] = []
    for entry in returned_entries:
        for ref in entry.get("blocking_gap_refs") or []:
            gid: str = ref["gap_id"]
            if gid not in seen_gap_ids:
                seen_gap_ids.add(gid)
                gap = gap_repo.get(gid)
                # Guard: verify session ownership before including.  gap_memberships
                # should always be scoped correctly, but check defensively to prevent
                # cross-session leaks from data-corruption edge cases.
                if (
                    gap is not None
                    and gap.get("session_id") == session_id
                    and gap.get("status") == "open"
                ):
                    blocking_gaps.append(gap)

    # ------------------------------------------------------------------
    # 9b. artifact_refs: stable-dedup from backing_findings.
    # ------------------------------------------------------------------
    artifact_refs: list[dict[str, Any]] = _stable_dedup(
        [
            {
                "artifact_id": f["artifact_id"],
                "step_ref": f["step_ref_json"],
            }
            for f in backing_findings
            if f.get("artifact_id") and f.get("step_ref_json")
        ],
        key_fn=lambda r: r["artifact_id"],
    )

    # ------------------------------------------------------------------
    # Return SessionStateView.
    # ------------------------------------------------------------------
    view: dict[str, Any] = {
        "session_id": session_id,
        "focus_subjects": focus_subjects,
        "active_propositions": returned_entries,
        "backing_findings": backing_findings,
        "blocking_gaps": blocking_gaps,
        "artifact_refs": artifact_refs,
        "truncation": truncation,
        "schema_version": SESSION_STATE_VIEW_SCHEMA_VERSION,
        "next_page_token": None,  # v1: no cursor pagination
    }
    assert_no_semantic_refs_in_canonical_payload(view, surface="session_state_view")
    return view


__all__ = [
    "SESSION_STATE_VIEW_SCHEMA_VERSION",
    "materialize_session_state_view",
]
