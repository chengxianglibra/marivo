"""Content hashing helpers for persisted analysis artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from marivo.analysis.frames.base import BaseFrameMeta

_SESSION_LOCAL_META_FIELDS = {
    "ref",
    "session_id",
    "project_root",
    "produced_by_job",
    "created_at",
    "byte_size",
    "artifact_id",
    "content_hash",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_meta_payload(meta: BaseFrameMeta) -> dict[str, Any]:
    """Return metadata fields that define artifact content, excluding local identity."""
    payload = meta.model_dump(mode="json")
    return {
        key: value
        for key, value in sorted(payload.items())
        if key not in _SESSION_LOCAL_META_FIELDS
    }


def compute_frame_content_hash(*, meta: BaseFrameMeta, data_path: Path) -> str:
    """Compute a deterministic hash over parquet bytes and stable metadata."""
    payload = {
        "data_sha256": _sha256_file(data_path),
        "meta": stable_meta_payload(meta),
        "schema_version": 1,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
