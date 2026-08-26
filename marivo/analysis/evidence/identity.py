"""Replay-stable identity helpers for typed evidence values."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import math
from collections.abc import Mapping
from contextlib import suppress
from datetime import date, datetime, time
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel

from marivo._compat import UTC
from marivo.analysis.evidence.types import EvidenceSubject

_SUBJECT_HASH_LEN = 32
_ID_HASH_LEN = 24
_TYPED_ITEM_KEY_PREFIX = "typed-coordinate/v1:"


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for supported evidence payloads."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prefix: str, value: Any, length: int = _ID_HASH_LEN) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length]}"


def canonical_subject_key(subject: EvidenceSubject) -> str:
    """Hash a subject's complete normalized semantic content."""
    digest = hashlib.sha256(canonical_json(subject).encode("utf-8")).hexdigest()
    return digest[:_SUBJECT_HASH_LEN]


def make_scope_fingerprint(scope: Any) -> str:
    """Fingerprint one normalized artifact scope for typed evidence ownership."""

    return hashlib.sha256(canonical_json(scope).encode("utf-8")).hexdigest()


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


def _typed_item_key_scalar(value: Any) -> dict[str, str | None]:
    """Encode one scalar without collapsing distinct Python value types."""
    missing = False
    if not isinstance(value, (list, tuple, dict)):
        with suppress(TypeError, ValueError):
            missing = bool(pd.isna(value))
    if value is None or missing:
        return {"type": "null", "value": None}
    if isinstance(value, np.datetime64):
        return {"type": "datetime", "value": pd.Timestamp(value).isoformat()}
    if isinstance(value, pd.Timestamp):
        return {"type": "datetime", "value": value.isoformat()}
    item = getattr(value, "item", None)
    if callable(item):
        with suppress(TypeError, ValueError):
            value = item()
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, bool):
        return {"type": "bool", "value": "true" if value else "false"}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {"type": "null", "value": None}
        if math.isinf(value):
            return {"type": "float", "value": "Infinity" if value > 0 else "-Infinity"}
        return {"type": "float", "value": repr(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    raise TypeError(
        "canonical item-key coordinates require null, string, bool, int, float, or temporal scalars; "
        f"received {type(value).__name__}"
    )


def make_typed_item_key(
    *,
    namespace: str,
    coordinates: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> str:
    """Build a versioned, injective key for source-row coordinates."""

    def encoded_items(values: Mapping[str, Any]) -> list[dict[str, object]]:
        if any(not isinstance(name, str) for name in values):
            raise TypeError("canonical item-key coordinate names must be strings")
        return [
            {"name": name, **_typed_item_key_scalar(value)}
            for name, value in sorted(values.items())
        ]

    payload = {
        "namespace": namespace,
        "context": encoded_items(context or {}),
        "coordinates": encoded_items(coordinates),
    }
    return _TYPED_ITEM_KEY_PREFIX + canonical_json(payload)


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


def make_coverage_artifact_id(parent_ref: str, *, node_id: str | None = None) -> str:
    """Build a deterministic coverage artifact identity."""
    payload = {"parent_ref": parent_ref}
    if node_id is not None:
        payload["node_id"] = node_id
    return _hash("cov_", payload)


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
    "make_scope_fingerprint",
    "make_typed_item_key",
    "to_microseconds_utc",
]
