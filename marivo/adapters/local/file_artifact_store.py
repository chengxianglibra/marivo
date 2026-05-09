"""File-backed ArtifactStore for local-mode runtime.

Layout on disk::

    <root>/<session_id>/<step_id>.json          -- committed artifact record
    <root>/<session_id>/<step_id>.findings.json  -- extracted findings sidecar (optional)
    <root>/<session_id>/_index.jsonl             -- append-only index for list_artifacts

Each artifact record is a JSON dict containing all metadata fields
(artifact_id, session_id, step_id, artifact_type, name, lifecycle,
artifact_schema_version, content, created_at) so that resolve_* methods
can return rich data without joining against a separate metadata table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from marivo.contracts.ids import ArtifactId, SessionId, StepId


class FileArtifactStore:
    """File-backed ArtifactStore adapter for local mode.

    Implements the ArtifactStore port protocol using filesystem storage.
    Each session gets a subdirectory; each artifact is stored as a JSON
    file keyed by step_id.  An append-only JSONL index enables
    efficient list_artifacts queries.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_dir(self, session_id: SessionId) -> Path:
        """Return (and create) the directory for a session's artifacts."""
        d = self._root / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _artifact_path(self, session_id: SessionId, step_id: StepId) -> Path:
        return self._session_dir(session_id) / f"{step_id}.json"

    def _index_path(self, session_id: SessionId) -> Path:
        return self._session_dir(session_id) / "_index.jsonl"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _generate_artifact_id() -> ArtifactId:
        return ArtifactId(f"art_{uuid4().hex[:12]}")

    def _append_index(self, session_id: SessionId, record: dict[str, Any]) -> None:
        """Append an artifact summary line to the session index."""
        index_path = self._index_path(session_id)
        # Only store lightweight fields in the index; content is in the file.
        summary = {
            "artifact_id": record["artifact_id"],
            "session_id": record["session_id"],
            "step_id": record["step_id"],
            "artifact_type": record["artifact_type"],
            "name": record["name"],
            "lifecycle": record["lifecycle"],
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, sort_keys=True) + "\n")

    def _read_index(self, session_id: SessionId) -> list[dict[str, Any]]:
        """Read all index entries for a session."""
        index_path = self._index_path(session_id)
        if not index_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries

    # ------------------------------------------------------------------
    # ArtifactStore port methods
    # ------------------------------------------------------------------

    def insert_artifact(
        self,
        session_id: SessionId,
        step_id: StepId,
        artifact_type: str,
        name: str,
        content: Any,
        *,
        lifecycle: str = "committed",
        artifact_schema_version: str | None = None,
        artifact_id: ArtifactId | None = None,
    ) -> ArtifactId:
        if artifact_id is None:
            artifact_id = self._generate_artifact_id()
        record: dict[str, Any] = {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "step_id": step_id,
            "artifact_type": artifact_type,
            "name": name,
            "lifecycle": lifecycle,
            "artifact_schema_version": artifact_schema_version,
            "content": content,
            "created_at": self._now_iso(),
        }
        # Atomic write: write to tmp then rename to prevent partial reads
        path = self._artifact_path(session_id, step_id)
        tmp = path.with_suffix(f".tmp-{uuid4().hex[:8]}")
        tmp.write_text(
            json.dumps(record, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(path)
        self._append_index(session_id, record)
        return artifact_id

    def commit_artifact_with_extraction(
        self,
        session_id: SessionId,
        step_id: StepId,
        artifact_type: str,
        name: str,
        content: Any,
        *,
        step_type: str | None = None,
        artifact_schema_version: str | None = None,
    ) -> ArtifactId:
        """Canonical commit boundary with optional finding extraction.

        Mirrors the server adapter's commit_artifact_with_extraction but
        writes findings as a sidecar JSON file instead of to a DB.
        Skips the canonical downstream pipeline (server-only).
        """
        from marivo.evidence_engine.canonical_finding import StepRef
        from marivo.evidence_engine.finding_extractor_registry import (
            default_finding_registry,
            validate_for_commit,
        )

        extractor = default_finding_registry.find(artifact_type, artifact_schema_version)

        if extractor is None:
            # Non-mandatory family: insert as committed directly.
            return self.insert_artifact(
                session_id,
                step_id,
                artifact_type,
                name,
                content,
                lifecycle="committed",
                artifact_schema_version=artifact_schema_version,
            )

        # Mandatory extraction family — run extraction then validate.
        artifact_id = self._generate_artifact_id()
        effective_step_ref = StepRef(
            session_id=str(session_id),
            step_id=str(step_id),
            step_type=step_type or artifact_type,
        )
        result = extractor.extract(artifact_id, content, effective_step_ref, str(session_id))
        validate_for_commit(extractor.family, result)

        # Write primary artifact as committed using the same artifact_id
        # that was passed to the extractor.
        aid = self.insert_artifact(
            session_id,
            step_id,
            artifact_type,
            name,
            content,
            lifecycle="committed",
            artifact_schema_version=artifact_schema_version,
            artifact_id=artifact_id,
        )

        # Write findings as sidecar if any.
        if result["findings"]:
            findings_path = self._session_dir(session_id) / f"{step_id}.findings.json"
            findings_path.write_text(
                json.dumps(result["findings"], sort_keys=True, default=str),
                encoding="utf-8",
            )

        return aid

    def resolve_artifact_for_ref(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> dict[str, Any] | None:
        """Return the artifact content dict for a step reference.

        Returns only the content field (matching the server adapter contract)
        or None if no committed artifact exists for the given session/step.
        """
        path = self._artifact_path(session_id, step_id)
        if not path.is_file():
            return None
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if record.get("lifecycle") != "committed":
            return None
        return record.get("content")

    def resolve_artifact_id_for_step(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> ArtifactId | None:
        """Return the ArtifactId for a step reference, or None."""
        path = self._artifact_path(session_id, step_id)
        if not path.is_file():
            return None
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if record.get("lifecycle") != "committed":
            return None
        return ArtifactId(record["artifact_id"])

    def resolve_artifact_with_id(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> tuple[ArtifactId, dict[str, Any]] | None:
        """Return (ArtifactId, content_dict) for a step reference, or None."""
        path = self._artifact_path(session_id, step_id)
        if not path.is_file():
            return None
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if record.get("lifecycle") != "committed":
            return None
        return ArtifactId(record["artifact_id"]), record.get("content", {})

    def list_artifacts(
        self,
        session_id: SessionId,
    ) -> list[dict[str, Any]]:
        """Return all committed artifact records for a session, in insertion order.

        Each dict contains metadata fields (artifact_id, session_id, step_id,
        artifact_type, name, lifecycle) plus the content payload, matching the
        shape callers expect for runtime status projections.
        """
        entries = self._read_index(session_id)
        result: list[dict[str, Any]] = []
        for entry in entries:
            if entry.get("lifecycle") != "committed":
                continue
            # Enrich from the per-step file to include content
            step_path = self._artifact_path(session_id, StepId(entry["step_id"]))
            if step_path.is_file():
                full = json.loads(step_path.read_text(encoding="utf-8"))
                entry["content"] = full.get("content", {})
            result.append(entry)
        return result
