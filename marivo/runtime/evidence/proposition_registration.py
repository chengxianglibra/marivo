"""Proposition registration runtime for the evidence pipeline (Phase 4e-2).

Implements the seeding registration protocol described in
``docs/analysis/evidence-engine/finding-proposition-seeding.md`` §Phase 7.

## Registration protocol

1. Compute ``identity_key`` from judgment-semantic fields via
   :func:`~marivo.core.evidence.proposition_normalizer.normalize_proposition_identity`.
2. Derive ``proposition_id`` via
   :func:`~marivo.core.evidence.proposition_normalizer.make_proposition_id`.
3. Look up existing proposition: ``PropositionRepository.get_by_identity_key``.
4. **On miss (CREATE)**:
   - Persist the proposition row (includes ``seed_finding_refs_json``).
   - Populate the ``proposition_seed_finding_refs`` junction table.
   - Return ``{proposition_id, created=True}``.
5. **On hit (HIT)**:
   - Return the existing ``proposition_id``.
   - No writes are performed — ``seed_finding_refs`` is NOT updated.
   - Return ``{proposition_id, created=False}``.

## Seed finding refs format

``seed_finding_refs`` must be a list of ``PropositionSeedRef``-shaped dicts:

    [{"finding_ref": {"session_id": "...", "finding_id": "..."}, "role": "primary"}]

- The full ``PropositionSeedRef`` structure is stored in
  ``propositions.seed_finding_refs_json`` at creation time (read-only after).
- ``finding_id`` + ``role`` are extracted for the
  ``proposition_seed_finding_refs`` junction table (reverse-lookup index).

Phase: 4e-2
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from marivo.adapters.server.evidence_repositories import PropositionRepository
from marivo.core.evidence.proposition_normalizer import (
    make_proposition_id,
    normalize_proposition_identity,
)


class PropositionRegistrationResult(TypedDict):
    """Outcome of a single proposition registration attempt."""

    proposition_id: str
    #: ``True`` when a new proposition was created; ``False`` on a dedup hit.
    created: bool


def register_system_seeded_proposition(
    repo: PropositionRepository,
    *,
    session_id: str,
    proposition_type: str,
    subject: dict[str, Any],
    origin: dict[str, Any],
    assessment_anchor: dict[str, Any],
    lineage: dict[str, Any],
    payload: dict[str, Any],
    seed_finding_refs: list[dict[str, Any]],
    schema_version: str = "v1",
) -> PropositionRegistrationResult:
    """Register a system-seeded proposition, deduplicating by identity_key.

    Parameters
    ----------
    repo:
        The :class:`~marivo.storage.evidence_repositories.PropositionRepository`
        instance for this session's metadata store.
    session_id:
        Session the proposition belongs to.
    proposition_type:
        Proposition family: ``"change"``, ``"decomposition"``, ``"anomaly"``,
        ``"correlation"``, ``"test_hypothesis"``, or ``"forecast"``.
    subject:
        Proposition subject dict (``metric``, ``entity``, ``slice``, ``grain``,
        ``analysis_axis``).
    origin:
        Origin dict — must have ``"kind"`` (``"system_seeded"``),
        ``"template_id"``, and ``"template_version"``.
    assessment_anchor:
        Assessment anchor dict — must have ``"assessment_type"``.
    lineage:
        Lineage dict — must have ``"derivation_version"`` plus the standard
        ``creation_mode``, ``source_artifact_lineages``,
        ``source_step_refs``, ``derived_from_proposition_ref`` fields.
    payload:
        Proposition subtype payload dict.
    seed_finding_refs:
        List of ``PropositionSeedRef``-shaped dicts, each with:

        - ``"finding_ref"``: ``{"session_id": ..., "finding_id": ...}``
        - ``"role"``: ``"primary"`` | ``"secondary"`` | ``"context"``

        Written to ``seed_finding_refs_json`` and the junction table on
        CREATE; never modified on HIT.
    schema_version:
        Schema version string (default ``"v1"``).

    Returns
    -------
    PropositionRegistrationResult
        ``{"proposition_id": "...", "created": True|False}``
        where ``created`` is ``True`` on a new row and ``False`` on a dedup hit.
    """
    if origin.get("kind") != "system_seeded":
        raise ValueError(
            f"register_system_seeded_proposition requires origin.kind='system_seeded'; "
            f"got {origin.get('kind')!r}. Use a dedicated path for agent_authored propositions."
        )

    identity_key = normalize_proposition_identity(
        session_id=session_id,
        origin_kind=origin["kind"],
        proposition_type=proposition_type,
        derivation_version=lineage["derivation_version"],
        subject=subject,
        payload=payload,
    )
    proposition_id = make_proposition_id(identity_key)

    existing = repo.get_by_identity_key(session_id, proposition_type, identity_key)
    if existing is not None:
        return PropositionRegistrationResult(
            proposition_id=existing["proposition_id"],
            created=False,
        )

    repo.create(
        {
            "proposition_id": proposition_id,
            "session_id": session_id,
            "proposition_type": proposition_type,
            "subject_json": json.dumps(subject),
            "origin_json": json.dumps(origin),
            "assessment_anchor_json": json.dumps(assessment_anchor),
            "lineage_json": json.dumps(lineage),
            "seed_finding_refs_json": json.dumps(seed_finding_refs),
            "payload_json": json.dumps(payload),
            "schema_version": schema_version,
            "identity_key": identity_key,
        }
    )

    # Populate the reverse-lookup junction table.
    # add_seed_finding_refs expects [{finding_id, role}] entries.
    junction_refs = [
        {"finding_id": r["finding_ref"]["finding_id"], "role": r.get("role", "primary")}
        for r in seed_finding_refs
    ]
    repo.add_seed_finding_refs(proposition_id, junction_refs)

    return PropositionRegistrationResult(
        proposition_id=proposition_id,
        created=True,
    )


__all__ = [
    "PropositionRegistrationResult",
    "register_system_seeded_proposition",
]
