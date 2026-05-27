"""Latest-snapshot assessment recompute. Slice-1 implements change family only."""

from __future__ import annotations

from datetime import UTC, datetime

from marivo.analysis_py.evidence.identity import make_assessment_id
from marivo.analysis_py.evidence.types import Assessment, AssessmentStatus, Finding, Proposition

_VALIDATED_CONFIDENCE = 0.9
_INCONCLUSIVE_CONFIDENCE = 0.3


def _direction_matches_interest(direction: str, interest: str) -> bool | None:
    """Compare finding direction against proposition direction_of_interest.

    Returns True if direction supports the interest, False if it opposes,
    None if inconclusive (e.g. direction is undefined).
    """
    if direction == "undefined":
        return None
    if interest == "any_non_flat":
        return direction in ("increase", "decrease")
    if interest == "increase":
        return direction == "increase"
    if interest == "decrease":
        return direction == "decrease"
    return None


def recompute_change_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Return (new_snapshot, edges) where edges is [(finding_id, role)]."""
    interest = proposition.payload.get("direction_of_interest", "any_non_flat")
    primary = seed_findings[0] if seed_findings else None
    direction = primary.payload.get("direction", "undefined") if primary else "undefined"

    matches = _direction_matches_interest(direction, interest)
    status: AssessmentStatus
    if matches is True:
        status = "validated"
        confidence = _VALIDATED_CONFIDENCE
        basis = "seed_delta_direction_matches"
    elif matches is False:
        status = "refuted"
        confidence = _VALIDATED_CONFIDENCE
        basis = "seed_delta_direction_opposes"
    else:
        status = "inconclusive"
        confidence = _INCONCLUSIVE_CONFIDENCE
        basis = "seed_delta_direction_undefined"

    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "magnitude": primary.payload.get("magnitude") if primary else None,
        "current": primary.payload.get("current") if primary else None,
        "baseline": primary.payload.get("baseline") if primary else None,
    }
    snapshot = Assessment(
        snapshot_id=snapshot_id,
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        supersedes_id=previous.snapshot_id if previous else None,
        status=status,
        confidence=confidence,
        confidence_basis=basis,
        payload=payload,
        created_at=datetime.now(UTC),
        is_latest=True,
    )

    if matches is True:
        role = "support"
    elif matches is False:
        role = "oppose"
    else:
        role = "support"
    edges: list[tuple[str, str]] = [(f.finding_id, role) for f in seed_findings]
    return snapshot, edges


__all__ = ["recompute_change_assessment"]
