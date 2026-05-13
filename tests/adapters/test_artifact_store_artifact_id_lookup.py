from __future__ import annotations

from pathlib import Path

import pytest

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from marivo.contracts.ids import ArtifactId, SessionId, StepId


@pytest.fixture
def store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


def test_resolve_artifact_by_id_returns_committed_content_from_same_session(
    store: FileArtifactStore,
) -> None:
    session_id = SessionId("s-1")
    artifact_id = store.insert_artifact(
        session_id,
        StepId("step-a"),
        "observation_artifact",
        "primary",
        {"value": 42},
    )

    result = store.resolve_artifact_by_id(session_id, artifact_id)

    assert result == {"value": 42}


def test_resolve_artifact_by_id_returns_none_when_missing(
    store: FileArtifactStore,
) -> None:
    result = store.resolve_artifact_by_id(
        SessionId("s-1"),
        ArtifactId("art_missing"),
    )

    assert result is None


def test_resolve_artifact_by_id_returns_none_for_cross_session_lookup(
    store: FileArtifactStore,
) -> None:
    artifact_id = store.insert_artifact(
        SessionId("s-1"),
        StepId("step-a"),
        "observation_artifact",
        "primary",
        {"value": 42},
    )

    result = store.resolve_artifact_by_id(SessionId("s-2"), artifact_id)

    assert result is None
