from __future__ import annotations

from typing import Any, Protocol

from app.contracts.ids import ArtifactId, SessionId, StepId


class ArtifactStore(Protocol):
    """Port for persisting and retrieving analysis artifacts.

    Encapsulates the metadata DB operations for artifact CRUD,
    including the mandatory-extraction commit boundary.
    """

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
    ) -> ArtifactId: ...

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
    ) -> ArtifactId: ...

    def resolve_artifact_for_ref(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> dict[str, Any] | None: ...

    def resolve_artifact_id_for_step(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> ArtifactId | None: ...

    def resolve_artifact_with_id(
        self,
        session_id: SessionId,
        step_id: StepId,
    ) -> tuple[ArtifactId, dict[str, Any]] | None: ...

    def list_artifacts(
        self,
        session_id: SessionId,
    ) -> list[dict[str, Any]]: ...
