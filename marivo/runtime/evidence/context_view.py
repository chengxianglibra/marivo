"""Proposition-level canonical context surface materializer (Phase 5c).

Exposes:

* :func:`materialize_proposition_context_view` — builds a
  ``PropositionContextView`` dict from the canonical DB state for a given
  proposition.

The view is exclusively derived from the *externally visible*
proposition-local bundle.  Propositions with no publish switch executed yet
appear with ``latest_assessment = null``; all assessment-derived fields are
``null`` in that case (not ``[]``).

Runtime scheduling truth (claim, lease, retry, backlog) must **not** enter the
returned dict.  That belongs to the operator-facing runtime status surface.

Design reference: ``docs/analysis/evidence-engine/schemas/context-surface-schema.md``
HTTP contract: ``docs/api/context-surface.md``
"""

from __future__ import annotations

import json
from typing import Any

from marivo.adapters.server.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.runtime.evidence.publish_switch import assemble_externally_visible_bundle
from marivo.runtime.evidence.ref_boundary import assert_no_semantic_refs_in_canonical_payload

PROPOSITION_CONTEXT_VIEW_SCHEMA_VERSION = "proposition_context_view.v1"


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


def _finding_sort_key(f: dict[str, Any]) -> tuple[Any, ...]:
    """Canonical stable sort key per ``finding.md`` default ordering rules.

    Sort order (6 levels):
    1. ``subject.metric`` lexical, nulls last
    2. ``subject.slice`` canonicalised lexical
    3. ``finding_type`` lexical
    4. ``observed_window.kind`` lexical, nulls last
    5. kind-specific canonical time key ascending
    6. ``finding_id`` ascending
    """
    subj: dict[str, Any] = f.get("subject_json") or {}
    win: dict[str, Any] = f.get("observed_window_json") or {}

    metric: str | None = subj.get("metric")
    slice_val: dict[str, Any] = subj.get("slice") or {}
    finding_type: str = f.get("finding_type") or ""
    win_kind: str | None = win.get("kind")

    if win_kind == "range":
        time_key: str = win.get("start") or ""
    elif win_kind == "snapshot_now":
        time_key = win.get("observed_at") or ""
    elif win_kind == "latest_available":
        time_key = win.get("data_as_of") or ""
    elif win_kind == "as_of":
        time_key = win.get("at") or ""
    else:
        time_key = ""

    slice_str: str = json.dumps(slice_val, sort_keys=True) if slice_val else ""

    return (
        metric is None,  # nulls last: True > False in Python tuple sort
        metric or "",
        slice_str,
        finding_type,
        win_kind is None,  # nulls last
        win_kind or "",
        time_key,
        f.get("finding_id") or "",
    )


def _artifact_refs_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build stable-deduped artifact ref list from a list of findings."""
    return _stable_dedup(
        [
            {
                "artifact_id": f["artifact_id"],
                "step_ref": f["step_ref_json"],
            }
            for f in findings
            if f.get("artifact_id") and f.get("step_ref_json")
        ],
        key_fn=lambda r: r["artifact_id"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def materialize_proposition_context_view(
    *,
    session_id: str,
    proposition_id: str,
    proposition_repo: PropositionRepository,
    assessment_repo: AssessmentRepository,
    finding_repo: FindingRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    proposal_repo: ActionProposalRepository,
) -> dict[str, Any]:
    """Materialize a ``PropositionContextView`` for *proposition_id*.

    Returns the proposition-level minimal canonical closure: the proposition
    object, creation-time seed hydration, the latest externally visible
    assessment and its live evidence closure (findings, gaps, inference records,
    assessment dependencies), and provenance artifact handles.

    Parameters
    ----------
    session_id:
        Owning session.  Used for scope validation throughout.
    proposition_id:
        Target proposition.

    Returns
    -------
    dict
        A ``PropositionContextView``-shaped dict with ``schema_version``.

    Raises
    ------
    KeyError
        When *proposition_id* does not exist or does not belong to
        *session_id*.
    """
    # ------------------------------------------------------------------
    # 1. Load and validate the target proposition.
    # ------------------------------------------------------------------
    proposition = proposition_repo.get(proposition_id)
    if proposition is None or proposition.get("session_id") != session_id:
        raise KeyError(f"proposition {proposition_id!r} not found in session {session_id!r}")

    # ------------------------------------------------------------------
    # 2. Hydrate seed_entries from creation-time seed_finding_refs_json.
    #    Preserve canonical order; unresolvable refs surface as finding=null.
    # ------------------------------------------------------------------
    raw_seed_refs: list[dict[str, Any]] = proposition.get("seed_finding_refs_json") or []
    seed_entries: list[dict[str, Any]] = []
    for seed_ref in raw_seed_refs:
        finding_ref = seed_ref.get("finding_ref") or {}
        finding_id: str | None = finding_ref.get("finding_id")
        finding: dict[str, Any] | None = None
        if finding_id:
            candidate = finding_repo.get(finding_id)
            # Scope guard: only include findings that belong to this session.
            if candidate is not None and candidate.get("session_id") == session_id:
                finding = candidate
        seed_entries.append({"seed_ref": seed_ref, "finding": finding})

    # ------------------------------------------------------------------
    # 3. Assemble the externally visible bundle (read-only; returns None
    #    when no publish switch has been executed yet).
    #    ValueError means the externally_visible_assessment_id pointer is
    #    dangling — treat as "not found" so the API returns 404 not 500.
    # ------------------------------------------------------------------
    try:
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
    except ValueError as exc:
        raise KeyError(str(exc)) from exc

    # ------------------------------------------------------------------
    # 4. No externally visible bundle yet → return minimal unassessed view.
    #    artifact_refs still covers provenance from seed findings.
    # ------------------------------------------------------------------
    if bundle is None:
        seed_findings = [e["finding"] for e in seed_entries if e["finding"] is not None]
        view = {
            "proposition": proposition,
            "seed_entries": seed_entries,
            "relevant_findings": [],
            "latest_assessment": None,
            "blocking_gaps": None,
            "non_blocking_gaps": None,
            "applied_inference_records": None,
            "assessment_dependencies": None,
            "artifact_refs": _artifact_refs_from_findings(seed_findings),
            "schema_version": PROPOSITION_CONTEXT_VIEW_SCHEMA_VERSION,
        }
        assert_no_semantic_refs_in_canonical_payload(view, surface="proposition_context_view")
        return view

    # ------------------------------------------------------------------
    # 5. Externally visible bundle exists — build full closure.
    # ------------------------------------------------------------------
    latest_assessment: dict[str, Any] = bundle["latest_assessment"]
    live_closure = bundle["live_closure"]

    # ------------------------------------------------------------------
    # 6. applied_inference_records: from live_closure (already hydrated).
    #    Must be assembled before relevant_findings so we can include their
    #    direct finding inputs in the relevant set.
    # ------------------------------------------------------------------
    applied_inference_records: list[dict[str, Any]] = list(
        live_closure["applied_inference_records"]
    )

    # ------------------------------------------------------------------
    # 7. relevant_findings: support + oppose from committed assessment
    #    closure PLUS direct finding inputs from applied_inference_records,
    #    then stable-dedup by finding_id and sorted canonically.
    #
    #    Per schema: "relevant_findings 必须足以覆盖
    #    latest_assessment.supporting_finding_ids、
    #    latest_assessment.opposing_finding_ids 以及当前
    #    applied_inference_records 的直接 finding 输入".
    # ------------------------------------------------------------------
    all_live_findings: list[dict[str, Any]] = list(live_closure["supporting_findings"]) + list(
        live_closure["opposing_findings"]
    )

    # Add direct finding inputs from inference records (scope-guarded).
    for rec in applied_inference_records:
        for fid in rec.get("input_finding_ids_json") or []:
            f = finding_repo.get(fid)
            if f is not None and f.get("session_id") == session_id:
                all_live_findings.append(f)

    deduped = _stable_dedup(all_live_findings, key_fn=lambda f: f["finding_id"])
    relevant_findings = sorted(deduped, key=_finding_sort_key)

    # ------------------------------------------------------------------
    # 8. blocking_gaps / non_blocking_gaps: hydrated from the assessment's
    #    gap_memberships_json snapshot (anchored to this bundle).
    # ------------------------------------------------------------------
    gap_memberships: list[dict[str, Any]] = latest_assessment.get("gap_memberships_json") or []
    blocking_gaps: list[dict[str, Any]] = []
    non_blocking_gaps: list[dict[str, Any]] = []
    for membership in gap_memberships:
        gap_ref = membership.get("gap_ref") or {}
        gap_id: str | None = gap_ref.get("gap_id")
        if not gap_id:
            continue
        gap = gap_repo.get(gap_id)
        if gap is None or gap.get("session_id") != session_id:
            continue
        if membership.get("blocking"):
            blocking_gaps.append(gap)
        else:
            non_blocking_gaps.append(gap)

    # ------------------------------------------------------------------
    # 9. assessment_dependencies: direct, stable-dedup input assessment
    #    closure from applied_inference_records.  No recursion.
    # ------------------------------------------------------------------
    all_input_ids: list[str] = []
    for rec in applied_inference_records:
        for aid in rec.get("input_assessment_ids_json") or []:
            all_input_ids.append(aid)
    unique_input_ids = list(dict.fromkeys(all_input_ids))  # stable dedup preserving order

    assessment_dependencies: list[dict[str, Any]] = []
    for aid in unique_input_ids:
        dep = assessment_repo.get(aid)
        if dep is not None:
            assessment_dependencies.append(dep)

    # ------------------------------------------------------------------
    # 10. artifact_refs: stable-dedup from seed findings + relevant_findings.
    # ------------------------------------------------------------------
    seed_findings = [e["finding"] for e in seed_entries if e["finding"] is not None]
    artifact_source_findings = _stable_dedup(
        seed_findings + relevant_findings,
        key_fn=lambda f: f["finding_id"],
    )
    artifact_refs = _artifact_refs_from_findings(artifact_source_findings)

    # ------------------------------------------------------------------
    # 11. Return PropositionContextView.
    # ------------------------------------------------------------------
    view = {
        "proposition": proposition,
        "seed_entries": seed_entries,
        "relevant_findings": relevant_findings,
        "latest_assessment": latest_assessment,
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "applied_inference_records": applied_inference_records,
        "assessment_dependencies": assessment_dependencies,
        "artifact_refs": artifact_refs,
        "schema_version": PROPOSITION_CONTEXT_VIEW_SCHEMA_VERSION,
    }
    assert_no_semantic_refs_in_canonical_payload(view, surface="proposition_context_view")
    return view
