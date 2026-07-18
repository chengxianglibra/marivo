"""Replay-stable identity helpers for typed evidence values."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from marivo.analysis.evidence.types import Subject

_SUBJECT_HASH_LEN = 32
_ID_HASH_LEN = 24


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for supported evidence payloads."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prefix: str, value: Any, length: int = _ID_HASH_LEN) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length]}"


def canonical_subject_key(subject: Subject) -> str:
    """Hash a subject's complete normalized semantic content."""
    digest = hashlib.sha256(canonical_json(subject).encode("utf-8")).hexdigest()
    return digest[:_SUBJECT_HASH_LEN]


def make_artifact_id(
    step_type: str,
    normalized_inputs: list[str],
    normalized_params: dict[str, Any],
    semantic_anchors: dict[str, Any],
) -> str:
    """Build the deterministic identity of a canonical artifact."""
    return _hash(
        "art_",
        {
            "step_type": step_type,
            "inputs": sorted(normalized_inputs),
            "params": normalized_params,
            "semantic_anchors": semantic_anchors,
        },
    )


def make_finding_id(artifact_id: str, finding_type: str, canonical_item_key: str) -> str:
    """Build an identity from finding semantics, excluding persistence time."""
    return _hash(
        "fnd_",
        {
            "artifact_id": artifact_id,
            "finding_type": finding_type,
            "canonical_item_key": canonical_item_key,
        },
    )


def make_digest_item_id(
    *, artifact_ref: str, item_kind: str, source_finding_refs: tuple[str, ...]
) -> str:
    """Build a digest-item identity from its artifact, kind, and source findings."""
    return _hash(
        "itm_",
        {
            "artifact_ref": artifact_ref,
            "item_kind": item_kind,
            "source_finding_refs": sorted(source_finding_refs),
        },
    )


def make_digest_fingerprint(digest_payload: BaseModel | dict[str, Any]) -> str:
    """Fingerprint normalized digest semantics, excluding the fingerprint field."""
    if isinstance(digest_payload, BaseModel):
        payload = digest_payload.model_dump(
            mode="json", exclude={"fingerprint"}, exclude_none=False
        )
    else:
        payload = dict(digest_payload)
        payload.pop("fingerprint", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_issue_id(*, artifact_id: str, kind: str, source_refs: tuple[str, ...]) -> str:
    """Build an immutable artifact-issue identity."""
    return _hash(
        "iss_",
        {
            "artifact_id": artifact_id,
            "kind": kind,
            "source_refs": sorted(source_refs),
        },
    )


def to_microseconds_utc(dt: datetime) -> int:
    """Convert a timezone-aware datetime to microseconds since Unix epoch."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(dt.astimezone(UTC).timestamp() * 1_000_000)


def make_component_artifact_id(parent_ref: str) -> str:
    """Build a deterministic component artifact identity."""
    return _hash("comp_", {"parent_ref": parent_ref})


def make_coverage_artifact_id(parent_ref: str) -> str:
    """Build a deterministic coverage artifact identity."""
    return _hash("cov_", {"parent_ref": parent_ref})


__all__ = [
    "canonical_json",
    "canonical_subject_key",
    "make_artifact_id",
    "make_component_artifact_id",
    "make_coverage_artifact_id",
    "make_digest_fingerprint",
    "make_digest_item_id",
    "make_finding_id",
    "make_issue_id",
    "to_microseconds_utc",
]
