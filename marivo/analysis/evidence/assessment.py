"""Latest-snapshot assessment recompute for evidence proposition families."""

from __future__ import annotations

from datetime import UTC, datetime

from marivo.analysis.evidence.identity import make_assessment_id
from marivo.analysis.evidence.types import Assessment, AssessmentStatus, Finding, Proposition

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


def recompute_driver_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Validate driver propositions when the seed has a concrete share."""
    primary = seed_findings[0] if seed_findings else None
    share = primary.payload.get("contribution_share") if primary else None
    direction = primary.payload.get("direction", "undefined") if primary else "undefined"
    role = proposition.payload.get("contribution_role", "material_component")

    status: AssessmentStatus
    if share is None or direction == "undefined":
        status = "inconclusive"
        confidence = _INCONCLUSIVE_CONFIDENCE
        basis = "driver_share_undefined"
    else:
        status = "validated"
        confidence = _VALIDATED_CONFIDENCE
        basis = f"driver_role_{role}"

    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "contribution_value": primary.payload.get("contribution_value") if primary else None,
        "contribution_share": share,
        "direction": direction,
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
    edges: list[tuple[str, str]] = [(f.finding_id, "support") for f in seed_findings]
    return snapshot, edges


def recompute_anomaly_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Keep anomaly candidates pending until reviewed."""
    primary = seed_findings[0] if seed_findings else None
    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "score": primary.payload.get("score") if primary else None,
        "flag_level": primary.payload.get("flag_level") if primary else None,
        "current_value": primary.payload.get("current_value") if primary else None,
        "baseline_value": primary.payload.get("baseline_value") if primary else None,
    }
    snapshot = Assessment(
        snapshot_id=snapshot_id,
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        supersedes_id=previous.snapshot_id if previous else None,
        status="pending",
        confidence=None,
        confidence_basis="anomaly_candidate_pending_review",
        payload=payload,
        created_at=datetime.now(UTC),
        is_latest=True,
    )
    edges: list[tuple[str, str]] = [(f.finding_id, "support") for f in seed_findings]
    return snapshot, edges


def recompute_association_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    alpha: float = 0.05,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Validate associations when p_value is below alpha."""
    primary = seed_findings[0] if seed_findings else None
    coefficient = primary.payload.get("coefficient") if primary else None
    p_value = primary.payload.get("p_value") if primary else None

    status: AssessmentStatus
    if coefficient is None or p_value is None:
        status = "inconclusive"
        confidence = _INCONCLUSIVE_CONFIDENCE
        basis = "association_stats_unavailable"
    elif p_value < alpha:
        status = "validated"
        confidence = _VALIDATED_CONFIDENCE
        basis = "association_p_lt_alpha"
    else:
        status = "inconclusive"
        confidence = _INCONCLUSIVE_CONFIDENCE
        basis = "association_p_ge_alpha"

    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "coefficient": coefficient,
        "p_value": p_value,
        "n": primary.payload.get("n") if primary else None,
        "alpha": alpha,
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
    edges: list[tuple[str, str]] = [(f.finding_id, "support") for f in seed_findings]
    return snapshot, edges


def recompute_test_hypothesis_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Validate or refute tested hypotheses from reject_null."""
    primary = seed_findings[0] if seed_findings else None
    reject = primary.payload.get("reject_null") if primary else None
    p_value = primary.payload.get("p_value") if primary else None

    status: AssessmentStatus
    if reject is True:
        status = "validated"
        confidence = _VALIDATED_CONFIDENCE
        basis = "test_p_lt_alpha"
        role = "support"
    elif reject is False:
        status = "refuted"
        confidence = _VALIDATED_CONFIDENCE
        basis = "test_p_ge_alpha"
        role = "oppose"
    else:
        status = "inconclusive"
        confidence = _INCONCLUSIVE_CONFIDENCE
        basis = "test_reject_null_undefined"
        role = "support"

    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "p_value": p_value,
        "estimate_value": primary.payload.get("estimate_value") if primary else None,
        "statistic_value": primary.payload.get("statistic_value") if primary else None,
        "reject_null": reject,
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
    edges: list[tuple[str, str]] = [(f.finding_id, role) for f in seed_findings]
    return snapshot, edges


def recompute_forecast_assessment(
    *,
    proposition: Proposition,
    seed_findings: list[Finding],
    snapshot_seq: int,
    previous: Assessment | None = None,
) -> tuple[Assessment, list[tuple[str, str]]]:
    """Keep forecasts pending until actual values are observed."""
    primary = seed_findings[0] if seed_findings else None
    snapshot_id = make_assessment_id(
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        snapshot_seq=snapshot_seq,
    )
    payload: dict[str, object] = {
        "predicted_value": primary.payload.get("predicted_value") if primary else None,
        "prediction_interval": primary.payload.get("prediction_interval") if primary else None,
        "horizon_index": primary.payload.get("horizon_index") if primary else None,
    }
    snapshot = Assessment(
        snapshot_id=snapshot_id,
        proposition_id=proposition.proposition_id,
        session_id=proposition.session_id,
        supersedes_id=previous.snapshot_id if previous else None,
        status="pending",
        confidence=None,
        confidence_basis="forecast_pending_actual",
        payload=payload,
        created_at=datetime.now(UTC),
        is_latest=True,
    )
    edges: list[tuple[str, str]] = [(f.finding_id, "support") for f in seed_findings]
    return snapshot, edges


__all__ = [
    "recompute_anomaly_assessment",
    "recompute_association_assessment",
    "recompute_change_assessment",
    "recompute_driver_assessment",
    "recompute_forecast_assessment",
    "recompute_test_hypothesis_assessment",
]
