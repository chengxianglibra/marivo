"""Server-mode ArtifactStore + StepStore adapters wrapping MetadataStore.

These adapters implement the ArtifactStore and StepStore port protocols
by delegating to the existing MetadataStore and finding-extraction
infrastructure.  They are used only in server mode; local mode uses
separate SQLite-backed adapters.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from app.contracts.ids import ArtifactId, SessionId, StepId
from app.contracts.session import Step

logger = logging.getLogger(__name__)


class MetadataArtifactStoreAdapter:
    """Wraps MetadataStore -> ArtifactStore.

    Delegates artifact CRUD and the mandatory-extraction commit
    boundary to the existing MetadataStore + FindingExtractorRegistry.
    """

    def __init__(
        self,
        metadata: Any,  # MetadataStore (late-bound to avoid circular import)
        step_metadata_repo: Any = None,
        svc: Any = None,  # SemanticLayerService (for downstream pipeline)
    ) -> None:
        self._metadata = metadata
        self._step_metadata_repo = step_metadata_repo
        self._svc = svc

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

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
    ) -> ArtifactId:
        artifact_id = f"art_{uuid4().hex[:12]}"
        self._metadata.execute(
            """
            INSERT INTO artifacts
                (artifact_id, session_id, step_id, artifact_type, name,
                 content_json, lifecycle, artifact_schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                artifact_id,
                session_id,
                step_id,
                artifact_type,
                name,
                self._dump(content),
                lifecycle,
                artifact_schema_version,
            ],
        )
        return ArtifactId(artifact_id)

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
        """Canonical commit boundary for mandatory-extraction artifacts.

        Mirrors SemanticLayerService._commit_artifact_with_extraction
        but operates through the port interface.
        """
        from app.findings.commit_boundary import validate_for_commit
        from app.findings.registry import default_finding_registry

        registry = default_finding_registry
        extractor = registry.find(artifact_type, artifact_schema_version)

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

        # Mandatory extraction family
        artifact_id = f"art_{uuid4().hex[:12]}"
        from app.evidence_engine.canonical_finding import StepRef

        effective_step_ref = StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type=step_type or artifact_type,
        )
        result = extractor.extract(artifact_id, content, effective_step_ref, session_id)
        validate_for_commit(extractor.family, result)

        # All writes in a single transaction
        with self._metadata.connect() as con:
            self._metadata.execute_sql(
                con,
                """
                INSERT INTO artifacts
                    (artifact_id, session_id, step_id, artifact_type, name,
                     content_json, lifecycle, artifact_schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'staged', ?)
                """,
                [
                    artifact_id,
                    session_id,
                    step_id,
                    artifact_type,
                    name,
                    self._dump(content),
                    artifact_schema_version,
                ],
            )
            for f in result["findings"]:
                self._metadata.execute_sql(
                    con,
                    self._metadata.insert_ignore_sql(
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
                        f["finding_id"],
                        session_id,
                        artifact_id,
                        json.dumps(f["step_ref"]),
                        f["finding_type"],
                        f["provenance"]["canonical_item_key"],
                        json.dumps(f["subject"]),
                        json.dumps(f["observed_window"])
                        if f.get("observed_window") is not None
                        else None,
                        json.dumps(f["quality"]),
                        json.dumps(f["provenance"]),
                        json.dumps(f["payload"]),
                        "v1",
                    ],
                )
            self._metadata.execute_sql(
                con,
                "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
                [artifact_id],
            )
            con.commit()

        # Trigger canonical downstream pipeline for committed findings
        if result["findings"]:
            try:
                self._run_canonical_downstream(session_id, result["findings"])
            except Exception:
                logger.warning(
                    "canonical downstream error for artifact %s (non-fatal)",
                    artifact_id,
                    exc_info=True,
                )

        return ArtifactId(artifact_id)

    def _run_canonical_downstream(self, session_id: str, findings: list[dict[str, Any]]) -> None:
        """Trigger the canonical downstream pipeline for committed findings."""
        try:
            from app.findings.downstream import run_canonical_downstream

            committed_finding_ids = [f["finding_id"] for f in findings]
            if self._svc is None:
                return
            svc = self._svc
            run_canonical_downstream(
                session_id=session_id,
                trigger_finding_ids=committed_finding_ids,
                finding_repo=svc._finding_repo,
                proposition_repo=svc._proposition_repo,
                assessment_repo=svc._assessment_repo,
                gap_repo=svc._gap_repo,
                inference_record_repo=svc._inference_record_repo,
                proposal_repo=svc._proposal_repo,
                metadata_store=self._metadata,
            )
        except ImportError:
            pass

    def resolve_artifact_for_ref(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> dict[str, Any] | None:
        row = self._metadata.query_one(
            """
            SELECT content_json FROM artifacts
            WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [step_id, session_id],
        )
        return json.loads(row["content_json"]) if row else None

    def resolve_artifact_id_for_step(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> ArtifactId | None:
        row = self._metadata.query_one(
            "SELECT artifact_id FROM artifacts "
            "WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at DESC LIMIT 1",
            [step_id, session_id],
        )
        return ArtifactId(str(row["artifact_id"])) if row else None

    def resolve_artifact_with_id(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> tuple[ArtifactId, dict[str, Any]] | None:
        row = self._metadata.query_one(
            "SELECT artifact_id, content_json FROM artifacts "
            "WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at DESC LIMIT 1",
            [step_id, session_id],
        )
        if row is None:
            return None
        return ArtifactId(str(row["artifact_id"])), json.loads(row["content_json"])

    def list_artifacts(
        self,
        session_id: SessionId,
    ) -> list[dict[str, Any]]:
        rows = self._metadata.query(
            "SELECT content_json FROM artifacts "
            "WHERE session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at ASC",
            [session_id],
        )
        return [json.loads(row["content_json"]) for row in rows]


class MetadataStepStoreAdapter:
    """Wraps MetadataStore -> StepStore.

    Delegates step record insertion to the existing MetadataStore.
    """

    def __init__(
        self,
        metadata: Any,  # MetadataStore (late-bound)
        step_metadata_repo: Any = None,
    ) -> None:
        self._metadata = metadata
        self._step_metadata_repo = step_metadata_repo

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def insert_step(
        self,
        step_id: StepId,
        session_id: SessionId,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        semantic_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._metadata.execute(
            """
            INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json, provenance_json)
            VALUES (?, ?, ?, 'succeeded', ?, ?, ?)
            """,
            [
                step_id,
                session_id,
                step_type,
                summary,
                self._dump(result),
                self._dump(provenance or {}),
            ],
        )
        if semantic_metadata is not None and self._step_metadata_repo is not None:
            self._step_metadata_repo.upsert(
                step_id=step_id,
                metadata_kind="typed_semantic_snapshot",
                semantic_snapshot=semantic_metadata,
            )

    def list_steps(self, session_id: SessionId) -> list[Step]:
        # Shallow read: semantic_metadata is stored in a separate
        # step_metadata table and is not joined here.  Callers that
        # need it should query the metadata repo directly.
        rows = self._metadata.query(
            "SELECT step_id, session_id, step_type, summary, result_json, "
            "provenance_json, created_at FROM steps "
            "WHERE session_id = ? ORDER BY created_at ASC",
            [session_id],
        )
        result: list[Step] = []
        for row in rows:
            prov_raw = row["provenance_json"]
            result.append(
                Step(
                    step_id=StepId(str(row["step_id"])),
                    session_id=SessionId(str(row["session_id"])),
                    step_type=str(row["step_type"]),
                    summary=str(row["summary"]),
                    result=json.loads(str(row["result_json"])),
                    provenance=json.loads(str(prov_raw)) if prov_raw else None,
                    created_at=str(row["created_at"]),
                )
            )
        return result
