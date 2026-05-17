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

import contextlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from marivo.contracts.ids import ArtifactId, SessionId, StepId

logger = logging.getLogger(__name__)


class FileArtifactStore:
    """File-backed ArtifactStore adapter for local mode.

    Implements the ArtifactStore port protocol using filesystem storage.
    Each session gets a subdirectory; each artifact is stored as a JSON
    file keyed by step_id.  An append-only JSONL index enables
    efficient list_artifacts queries.
    """

    def __init__(
        self,
        root: Path,
        *,
        metadata_store: Any | None = None,
        evidence_repos: dict[str, Any] | None = None,
    ) -> None:
        self._root = root
        self._metadata_store = metadata_store
        self._evidence_repos = evidence_repos
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

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    def _sync_metadata_artifact(
        self,
        *,
        session_id: SessionId,
        step_id: StepId,
        artifact_type: str,
        name: str,
        content: Any,
        lifecycle: str,
        artifact_schema_version: str | None,
        artifact_id: ArtifactId,
        findings: Sequence[Mapping[str, Any]],
    ) -> None:
        """Mirror a local file artifact into metadata-backed canonical evidence tables."""
        metadata = self._metadata_store
        if metadata is None:
            return

        with metadata.connect() as con:
            metadata.execute_sql(
                con,
                metadata.insert_ignore_sql(
                    "artifacts",
                    [
                        "artifact_id",
                        "session_id",
                        "step_id",
                        "artifact_type",
                        "name",
                        "content_json",
                        "lifecycle",
                        "artifact_schema_version",
                    ],
                ),
                [
                    str(artifact_id),
                    str(session_id),
                    str(step_id),
                    artifact_type,
                    name,
                    self._dump(content),
                    lifecycle,
                    artifact_schema_version,
                ],
            )
            for finding in findings:
                metadata.execute_sql(
                    con,
                    metadata.insert_ignore_sql(
                        "findings",
                        [
                            "finding_id",
                            "session_id",
                            "artifact_id",
                            "step_ref_json",
                            "finding_type",
                            "canonical_item_key",
                            "subject_json",
                            "observed_window_json",
                            "quality_json",
                            "provenance_json",
                            "payload_json",
                            "schema_version",
                        ],
                    ),
                    [
                        str(finding["finding_id"]),
                        str(session_id),
                        str(artifact_id),
                        self._dump(finding["step_ref"]),
                        str(finding["finding_type"]),
                        str(finding["provenance"]["canonical_item_key"]),
                        self._dump(finding["subject"]),
                        self._dump(finding["observed_window"])
                        if finding.get("observed_window") is not None
                        else None,
                        self._dump(finding["quality"]),
                        self._dump(finding["provenance"]),
                        self._dump(finding["payload"]),
                        "v1",
                    ],
                )
            con.commit()

    def _run_canonical_downstream(
        self,
        *,
        session_id: SessionId,
        findings: Sequence[Mapping[str, Any]],
    ) -> None:
        repos = self._evidence_repos
        metadata = self._metadata_store
        if not findings or repos is None or metadata is None:
            return

        from marivo.runtime.evidence.canonical_pipeline import run_canonical_downstream

        try:
            run_canonical_downstream(
                session_id=str(session_id),
                trigger_finding_ids=[str(f["finding_id"]) for f in findings],
                finding_repo=repos["finding_repo"],
                proposition_repo=repos["proposition_repo"],
                assessment_repo=repos["assessment_repo"],
                gap_repo=repos["gap_repo"],
                inference_record_repo=repos["inference_record_repo"],
                proposal_repo=repos["proposal_repo"],
                metadata_store=metadata,
            )
        except Exception:
            logger.warning(
                "canonical downstream error for local artifact sync (non-fatal)",
                exc_info=True,
            )

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

    def _write_artifact_record(
        self,
        *,
        session_id: SessionId,
        step_id: StepId,
        artifact_type: str,
        name: str,
        content: Any,
        lifecycle: str,
        artifact_schema_version: str | None,
        artifact_id: ArtifactId,
        findings: Sequence[Mapping[str, Any]],
    ) -> None:
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
        path = self._artifact_path(session_id, step_id)
        artifact_tmp = path.with_suffix(f".tmp-{uuid4().hex[:8]}")
        artifact_tmp.write_text(
            json.dumps(record, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        findings_tmp: Path | None = None
        findings_path: Path | None = None
        if findings:
            findings_path = self._session_dir(session_id) / f"{step_id}.findings.json"
            findings_tmp = (
                self._session_dir(session_id) / f"{step_id}.findings.tmp-{uuid4().hex[:8]}"
            )
            findings_tmp.write_text(
                json.dumps(findings, sort_keys=True, default=str),
                encoding="utf-8",
            )

        try:
            self._sync_metadata_artifact(
                session_id=session_id,
                step_id=step_id,
                artifact_type=artifact_type,
                name=name,
                content=content,
                lifecycle=lifecycle,
                artifact_schema_version=artifact_schema_version,
                artifact_id=artifact_id,
                findings=findings,
            )
            artifact_tmp.replace(path)
            if findings_tmp is not None and findings_path is not None:
                findings_tmp.replace(findings_path)
            self._append_index(session_id, record)
        except BaseException:
            with contextlib.suppress(OSError):
                artifact_tmp.unlink()
            if findings_tmp is not None:
                with contextlib.suppress(OSError):
                    findings_tmp.unlink()
            raise

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
        self._write_artifact_record(
            session_id=session_id,
            step_id=step_id,
            artifact_type=artifact_type,
            name=name,
            content=content,
            lifecycle=lifecycle,
            artifact_schema_version=artifact_schema_version,
            artifact_id=artifact_id,
            findings=[],
        )
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
        artifact_id: ArtifactId | None = None,
    ) -> ArtifactId:
        """Canonical commit boundary with optional finding extraction.

        Mirrors the server adapter's commit_artifact_with_extraction but
        writes findings as a sidecar JSON file instead of to a DB.
        Skips the canonical downstream pipeline (server-only).
        """
        from marivo.core.evidence.canonical_finding import StepRef
        from marivo.runtime.evidence.finding_extractor_registry import (
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
                artifact_id=artifact_id,
            )

        # Mandatory extraction family — run extraction then validate.
        if artifact_id is None:
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
        self._write_artifact_record(
            session_id=session_id,
            step_id=step_id,
            artifact_type=artifact_type,
            name=name,
            content=content,
            lifecycle="committed",
            artifact_id=artifact_id,
            artifact_schema_version=artifact_schema_version,
            findings=result["findings"],
        )

        if result["findings"]:
            self._run_canonical_downstream(session_id=session_id, findings=result["findings"])

        return artifact_id

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

    def resolve_artifact_by_id(
        self,
        session_id: SessionId,
        artifact_id: ArtifactId,
    ) -> dict[str, Any] | None:
        """Return committed artifact content by session-scoped ArtifactId."""
        for entry in reversed(self._read_index(session_id)):
            if entry.get("artifact_id") != str(artifact_id):
                continue
            if entry.get("lifecycle") != "committed":
                continue

            step_id = entry.get("step_id")
            if step_id is None:
                return None
            path = self._artifact_path(session_id, StepId(step_id))
            if not path.is_file():
                return None

            record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if record.get("artifact_id") != str(artifact_id):
                return None
            if record.get("lifecycle") != "committed":
                return None
            return record.get("content")

        return None

    def resolve_artifact_with_step_by_id(
        self,
        session_id: SessionId,
        artifact_id: ArtifactId,
    ) -> tuple[StepId, dict[str, Any]] | None:
        """Return (StepId, content) for a committed session-scoped ArtifactId."""
        for entry in reversed(self._read_index(session_id)):
            if entry.get("artifact_id") != str(artifact_id):
                continue
            if entry.get("lifecycle") != "committed":
                continue

            step_id_raw = entry.get("step_id")
            if step_id_raw is None:
                return None
            step_id = StepId(str(step_id_raw))
            path = self._artifact_path(session_id, step_id)
            if not path.is_file():
                return None

            record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if record.get("artifact_id") != str(artifact_id):
                return None
            if record.get("lifecycle") != "committed":
                return None
            return step_id, record.get("content", {})

        return None

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
