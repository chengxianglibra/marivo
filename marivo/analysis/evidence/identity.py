"""Stable identity helpers for evidence objects."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from marivo.analysis.evidence.types import Subject

_SUBJECT_HASH_LEN = 32  # SHA-256 first 16 bytes hex
_ID_HASH_LEN = 24


def canonical_json(value: Any) -> str:
    """RFC-8785-compatible canonicalization (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prefix: str, raw: str, length: int = _ID_HASH_LEN) -> str:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length]}"


def canonical_subject_key(subject: Subject) -> str:
    """Hash a Subject model to a 32-char hex key."""
    payload = subject.model_dump(mode="json", exclude_none=False)
    raw = canonical_json(payload)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:_SUBJECT_HASH_LEN]


def make_artifact_id(
    step_type: str,
    normalized_inputs: list[str],
    normalized_params: dict[str, Any],
    semantic_anchors: dict[str, Any],
) -> str:
    """Deterministic artifact ID from step type, inputs, params, and anchors."""
    raw = canonical_json(
        {
            "step_type": step_type,
            "inputs": sorted(normalized_inputs),
            "params": normalized_params,
            "semantic_anchors": semantic_anchors,
        }
    )
    return _hash("art_", raw)


def make_finding_id(artifact_id: str, finding_type: str, canonical_item_key: str) -> str:
    """Deterministic finding ID from artifact, type, and item key."""
    raw = f"{artifact_id}|{finding_type}|{canonical_item_key}"
    return _hash("fnd_", raw)


def make_proposition_id(
    *,
    proposition_type: str,
    origin_kind: str,
    derivation_version: str,
    subject_key: str,
    payload: dict[str, Any],
) -> str:
    """Deterministic proposition ID from type, origin, version, subject, and payload."""
    raw = canonical_json(
        {
            "type": proposition_type,
            "origin": origin_kind,
            "derivation_version": derivation_version,
            "subject_key": subject_key,
            "payload": payload,
        }
    )
    return _hash("prop_", raw)


def make_action_id(
    *,
    source_artifact_id: str,
    category: str,
    operator: str | None,
    input_refs: list[str],
    params: dict[str, Any],
) -> str:
    """Deterministic action ID from source artifact, category, operator, refs, and params."""
    raw = canonical_json(
        {
            "source_artifact_id": source_artifact_id,
            "category": category,
            "operator": operator,
            "input_refs": sorted(input_refs),
            "params": params,
        }
    )
    return _hash("act_", raw)


def make_issue_id(
    *,
    artifact_id: str,
    kind: str,
    source_refs: list[str],
) -> str:
    """Deterministic issue ID from artifact, kind, and source refs."""
    raw = canonical_json(
        {
            "artifact_id": artifact_id,
            "kind": kind,
            "source_refs": sorted(source_refs),
        }
    )
    return _hash("iss_", raw)


def make_assessment_id(
    *,
    proposition_id: str,
    session_id: str,
    snapshot_seq: int,
) -> str:
    """Deterministic assessment ID from proposition, session, and sequence."""
    raw = f"{session_id}|{proposition_id}|{snapshot_seq}"
    return _hash("ass_", raw)


def to_microseconds_utc(dt: Any) -> int:
    """Convert a tz-aware datetime to microseconds since unix epoch UTC."""
    from datetime import UTC, datetime

    if not isinstance(dt, datetime):
        raise TypeError(f"expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None:
        raise ValueError("datetime must be tz-aware")
    return int(dt.astimezone(UTC).timestamp() * 1_000_000)


def make_component_artifact_id(parent_ref: str) -> str:
    """Deterministic component frame ref derived from the parent's ref or artifact_id."""
    return _hash("comp_", parent_ref)


def make_coverage_artifact_id(parent_ref: str) -> str:
    """Deterministic coverage frame ref derived from the parent's ref or artifact_id."""
    return _hash("cov_", parent_ref)


__all__ = [
    "canonical_json",
    "canonical_subject_key",
    "make_action_id",
    "make_artifact_id",
    "make_assessment_id",
    "make_component_artifact_id",
    "make_coverage_artifact_id",
    "make_finding_id",
    "make_issue_id",
    "make_proposition_id",
    "to_microseconds_utc",
]
