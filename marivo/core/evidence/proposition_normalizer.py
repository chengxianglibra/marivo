"""Proposition identity normalization for the evidence pipeline (Phase 4e-2).

Implements the canonical identity normalization described in
``docs/analysis/evidence-engine/finding-proposition-seeding.md`` §Phase 6 and
``docs/analysis/evidence-engine/schemas/proposition.md`` §标识归一化.

## Identity contract

A stable ``identity_key`` is derived from judgment-semantic fields only.
Fields explicitly excluded from identity (proposition.md):

- ``template_id``, ``template_version``, ``schema_version``, ``created_at``
- ``seed_finding_refs`` ordering
- assessment status / confidence / supporting / opposing evidence

## Canonical JSON

The identity object is serialized to canonical JSON before hashing:

- Dict keys sorted lexically at **all** nesting levels.
- Float values emitted as canonical decimal numbers (no scientific notation,
  trailing zeros stripped): ``0.050`` ≡ ``0.05``.
- Int, str, bool, and ``None`` use their native JSON representations.
- ``1`` and ``"1"`` are different values — no type coercion.

## Origin partitioning

``origin_kind`` is included in the identity object so that
``system_seeded`` and ``agent_authored`` propositions can never share an
``identity_key`` even when their judgment-semantic payload is identical.

## Per-type identity fields

| Type | Subject included | Payload identity fields |
|---|---|---|
| ``change`` | yes | ``change_kind``, ``comparison_window``, ``direction_of_interest``, ``dimension_keys`` |
| ``decomposition`` | no (captured by ``scope_delta_ref``) | ``dimension``, ``dimension_keys``, ``contribution_role``, ``scope_delta_ref`` |
| ``anomaly`` | yes | ``candidate_ref``, ``validation_goal`` |
| ``correlation`` | no (derivable from left/right) | ``left_subject``, ``right_subject``, ``relationship_of_interest``, ``method_family``, ``join_basis``, ``aligned_window`` |
| ``test_hypothesis`` | no (derivable from left/right) | ``hypothesis_family``, ``alternative``, ``left_subject``, ``right_subject``, ``method_family``, ``alpha`` |
| ``forecast`` | yes | ``forecast_window``, ``horizon_index``, ``expectation_direction`` |

Phase: 4e-2
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Canonical decimal
# ---------------------------------------------------------------------------


def _canonical_decimal(v: float) -> str:
    """Return the canonical decimal string for *v*.

    Rules (proposition.md §标识归一化):
    - No scientific notation.
    - Trailing zeros stripped: ``0.050`` → ``"0.05"``.
    - ``1.0`` → ``"1"``; ``0.0`` → ``"0"``; ``1e-4`` → ``"0.0001"``.
    """
    return format(Decimal(str(v)).normalize(), "f")


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Serialize *obj* to canonical JSON.

    - Dict keys sorted lexically at every nesting level.
    - Float values emitted via :func:`_canonical_decimal` (numeric, not string).
    - ``bool`` checked before ``int`` because ``bool`` is a subclass of ``int``.
    - No unnecessary whitespace.
    """
    if isinstance(obj, dict):
        inner = ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{_canonical_json(v)}"
            for k, v in sorted(obj.items())
        )
        return "{" + inner + "}"
    if isinstance(obj, list):
        return "[" + ",".join(_canonical_json(v) for v in obj) + "]"
    if isinstance(obj, bool):  # must come before int
        return "true" if obj else "false"
    if isinstance(obj, float):
        return _canonical_decimal(obj)
    if isinstance(obj, int):
        return str(obj)
    if obj is None:
        return "null"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    raise TypeError(f"Cannot canonically serialize {type(obj)!r}")


# ---------------------------------------------------------------------------
# Per-type identity field extractors
# ---------------------------------------------------------------------------


def _subject_fields(subject: dict[str, Any]) -> dict[str, Any]:
    """Extract the 5 stable subject fields used in proposition identity.

    ``proposition.md`` §标识边界 only explicitly lists ``metric``, ``entity``,
    and ``slice`` as core identity inputs, but ``grain`` and ``analysis_axis``
    are also included here because they are part of ``PropositionSubject`` and
    carry independent semantic meaning:

    - ``grain``: "daily DAU" vs "weekly DAU" are different judgment objects.
    - ``analysis_axis``: always a constant for a given proposition type (e.g.
      ``"change"`` for all change propositions), so including it costs nothing
      and makes the identity object self-describing.

    Excluding them would make the identity *less* specific than the
    ``PropositionSubject`` contract warrants and could allow false dedup
    between propositions that differ only in grain.
    """
    return {
        "metric": subject.get("metric"),
        "entity": subject.get("entity"),
        "slice": subject.get("slice", {}),
        "grain": subject.get("grain"),
        "analysis_axis": subject.get("analysis_axis"),
    }


def _identity_fields_change(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``change`` — metric/entity/slice + change_kind/window/direction/dimension_keys.

    Excluded from identity: ``comparison_basis``, ``unit``.
    """
    return {
        "subject": _subject_fields(subject),
        "payload": {
            "change_kind": payload["change_kind"],
            "comparison_window": payload["comparison_window"],
            "direction_of_interest": payload["direction_of_interest"],
            "dimension_keys": payload.get("dimension_keys"),
        },
    }


def _identity_fields_decomposition(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``decomposition`` — dimension/keys/role/scope_delta_ref.

    The base subject is intentionally excluded: it is implicitly captured by
    ``scope_delta_ref``, which points to a finding scoped to a specific
    metric/artifact.  Including a redundant copy would create a dependency on
    the caller correctly deriving the subject from the finding, which is out
    of scope for the normalization layer.
    """
    return {
        "payload": {
            "dimension": payload["dimension"],
            "dimension_keys": payload["dimension_keys"],
            "contribution_role": payload["contribution_role"],
            "scope_delta_ref": payload["scope_delta_ref"],
        },
    }


def _identity_fields_anomaly(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``anomaly`` — subject + candidate_ref/validation_goal.

    Excluded from identity: ``anomaly_kind``, ``expected_behavior_ref``,
    ``observed_window``.
    """
    return {
        "subject": _subject_fields(subject),
        "payload": {
            "candidate_ref": payload.get("candidate_ref"),
            "validation_goal": payload["validation_goal"],
        },
    }


def _identity_fields_correlation(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``correlation`` — left/right subjects + relationship/method/join_basis/window.

    The base ``subject`` (focus anchor) is excluded: it is deterministically
    derived from ``left_subject``/``right_subject`` via the double-sided
    focus-anchor algorithm and is not an independent identity input
    (proposition.md §subject §双侧命题的 base subject 额外约束).
    """
    return {
        "payload": {
            "left_subject": payload["left_subject"],
            "right_subject": payload["right_subject"],
            "relationship_of_interest": payload["relationship_of_interest"],
            "method_family": payload["method_family"],
            "join_basis": payload["join_basis"],
            "aligned_window": payload["aligned_window"],
        },
    }


def _identity_fields_test_hypothesis(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``test_hypothesis`` — hypothesis_family/alt/left+right/method/alpha.

    The base ``subject`` is excluded for the same reason as ``correlation``.
    Excluded from identity: ``hypothesis_label``.
    """
    return {
        "payload": {
            "hypothesis_family": payload["hypothesis_family"],
            "alternative": payload["alternative"],
            "left_subject": payload["left_subject"],
            "right_subject": payload["right_subject"],
            "method_family": payload["method_family"],
            "alpha": payload.get("alpha"),
        },
    }


def _identity_fields_forecast(
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """``forecast`` — subject + forecast_window/horizon_index/expectation_direction.

    Excluded from identity: ``forecast_kind``, ``forecast_basis_ref``.
    """
    return {
        "subject": _subject_fields(subject),
        "payload": {
            "forecast_window": payload["forecast_window"],
            "horizon_index": payload.get("horizon_index"),
            "expectation_direction": payload["expectation_direction"],
        },
    }


_IDENTITY_EXTRACTORS: dict[str, Any] = {
    "change": _identity_fields_change,
    "decomposition": _identity_fields_decomposition,
    "anomaly": _identity_fields_anomaly,
    "correlation": _identity_fields_correlation,
    "test_hypothesis": _identity_fields_test_hypothesis,
    "forecast": _identity_fields_forecast,
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PROPOSITION_ID_PREFIX = "prop_"
_PROPOSITION_ID_HASH_LEN = 24  # hex chars taken from the 64-char SHA-256 digest


def normalize_proposition_identity(
    *,
    session_id: str,
    origin_kind: str,
    proposition_type: str,
    derivation_version: str,
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Compute the stable ``identity_key`` for a proposition.

    Returns a 64-character SHA-256 hex digest of the canonical JSON
    serialization of the proposition's judgment-semantic fields.

    Parameters
    ----------
    session_id:
        Propositions are session-local; always included in identity.
    origin_kind:
        ``"system_seeded"`` or ``"agent_authored"``.  Included so that seeded
        and authored propositions with identical payloads cannot share an
        ``identity_key`` (finding-proposition-seeding.md §2).
    proposition_type:
        One of ``"change"``, ``"decomposition"``, ``"anomaly"``,
        ``"correlation"``, ``"test_hypothesis"``, ``"forecast"``.
    derivation_version:
        From ``lineage.derivation_version``.  Breaking seeding upgrades must
        bump this value to produce a new identity boundary.
    subject:
        Proposition subject dict (``metric``, ``entity``, ``slice``, ``grain``,
        ``analysis_axis``).
    payload:
        Proposition subtype payload dict.  The per-type extractor picks only
        the judgment-semantic fields from this dict.

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest (the ``identity_key``).

    Raises
    ------
    KeyError
        If *proposition_type* is not a recognised type, or a required payload
        field is missing.
    """
    extractor = _IDENTITY_EXTRACTORS[proposition_type]
    type_fields = extractor(subject, payload)

    identity_obj: dict[str, Any] = {
        "derivation_version": derivation_version,
        "origin_kind": origin_kind,
        "proposition_type": proposition_type,
        "session_id": session_id,
        **type_fields,
    }

    canonical = _canonical_json(identity_obj)
    return hashlib.sha256(canonical.encode()).hexdigest()


def make_proposition_id(identity_key: str) -> str:
    """Derive a stable ``proposition_id`` from *identity_key*.

    Format: ``"prop_"`` + first 24 hex chars of the 64-char SHA-256 identity_key.

    The ``"prop_"`` prefix distinguishes proposition IDs from finding IDs
    (``"fnd_"``) in cross-references and log output.
    """
    return f"{_PROPOSITION_ID_PREFIX}{identity_key[:_PROPOSITION_ID_HASH_LEN]}"


__all__ = [
    "make_proposition_id",
    "normalize_proposition_identity",
]
