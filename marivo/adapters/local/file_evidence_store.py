from __future__ import annotations

import contextlib
import hashlib
import json
import os
import uuid
from pathlib import Path

from marivo.contracts.errors import ErrorCode, IntegrityError, NotFoundError
from marivo.contracts.evidence import Evidence
from marivo.contracts.ids import EvidenceRef


class FileEvidenceStore:
    """File-backed EvidenceStore using SHA-256 hash addressing.

    - Write: serialize to canonical JSON (sorted keys, UTF-8), hash content
      excluding the derived ``ref`` field -> filename
    - The Evidence.ref field is set to the computed hash before storage
    - Atomic write via tmp-<uuid> then os.rename
    - Read: load and verify hash integrity by recomputing from content minus
      the ``ref`` field (IntegrityError on mismatch)
    - Idempotent: same content always maps to same file
    """

    def __init__(self, evidence_dir: Path) -> None:
        self._dir = evidence_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, evidence: Evidence) -> EvidenceRef:
        # Compute hash from content excluding the ref field (ref is derived)
        content_hash = self._compute_hash(evidence)
        ref = EvidenceRef(content_hash)

        # If already stored, return immediately (idempotent)
        target = self._dir / f"{content_hash}.json"
        if target.is_file():
            return ref

        # Store with the computed ref
        evidence_with_ref = evidence.model_copy(update={"ref": ref})
        stored_canonical = self._canonicalize(evidence_with_ref)

        tmp_path = self._dir / f"tmp-{uuid.uuid4().hex[:12]}"
        try:
            tmp_path.write_text(stored_canonical, encoding="utf-8")
            os.replace(str(tmp_path), str(target))
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
        return ref

    def read(self, ref: EvidenceRef) -> Evidence:
        path = self._dir / f"{ref}.json"
        if not path.is_file():
            raise NotFoundError(
                code=ErrorCode.EVIDENCE_NOT_FOUND,
                message=f"Evidence '{ref}' not found",
            )
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)

        # Verify integrity: recompute hash from content minus ref field
        # This must happen before model construction so that corrupt data
        # raises IntegrityError rather than a Pydantic ValidationError.
        data_for_hash = dict(data)
        data_for_hash.pop("ref", None)
        actual_hash = self._hash_from_dict(data_for_hash)
        if actual_hash != ref:
            raise IntegrityError(
                message=f"Evidence file '{ref}' is corrupt: content hash does not match",
            )

        return Evidence(**data)

    def _compute_hash(self, evidence: Evidence) -> str:
        """Compute SHA-256 hash from evidence content excluding the ref field."""
        dump = evidence.model_dump(mode="json")
        dump.pop("ref", None)
        return self._hash_from_dict(dump)

    @staticmethod
    def _hash_from_dict(data: dict[str, object]) -> str:
        """Compute SHA-256 hex digest from a dict via canonical JSON."""
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _canonicalize(self, evidence: Evidence) -> str:
        return json.dumps(
            evidence.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
