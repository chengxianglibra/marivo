from __future__ import annotations

from pathlib import Path

from marivo.contracts.ids import ArtifactId, SessionId, StepId
from tests.contracts.contract_cases import ContractCase


def _run_insert_artifact(adapter, _: Path) -> None:
    """Insert a committed artifact and verify an ArtifactId is returned."""
    artifact_id = adapter.insert_artifact(
        session_id=SessionId("sess-art-contract-1"),
        step_id=StepId("step-art-contract-1"),
        artifact_type="finding_set",
        name="test_artifact",
        content={"findings": []},
        lifecycle="committed",
        artifact_schema_version="v1",
    )
    assert artifact_id is not None
    assert isinstance(artifact_id, str)


def _run_resolve_artifact_for_ref(adapter, _: Path) -> None:
    """Insert a committed artifact and resolve it by session/step reference."""
    session_id = SessionId("sess-art-contract-2")
    step_id = StepId("step-art-contract-2")
    adapter.insert_artifact(
        session_id=session_id,
        step_id=step_id,
        artifact_type="finding_set",
        name="test_artifact_resolve",
        content={"findings": [{"id": "f1"}]},
        lifecycle="committed",
        artifact_schema_version="v1",
    )
    result = adapter.resolve_artifact_for_ref(session_id, step_id)
    assert result is not None
    assert result["findings"][0]["id"] == "f1"


def _run_resolve_artifact_for_ref_missing(adapter, _: Path) -> None:
    """Resolving a non-existent artifact returns None."""
    result = adapter.resolve_artifact_for_ref(
        SessionId("sess-missing"),
        StepId("step-missing"),
    )
    assert result is None


def _run_list_artifacts(adapter, _: Path) -> None:
    """Insert an artifact and verify it appears in list_artifacts."""
    session_id = SessionId("sess-art-contract-3")
    adapter.insert_artifact(
        session_id=session_id,
        step_id=StepId("step-art-contract-3"),
        artifact_type="finding_set",
        name="list_test_artifact",
        content={"findings": []},
        lifecycle="committed",
        artifact_schema_version="v1",
    )
    artifacts = adapter.list_artifacts(session_id)
    assert len(artifacts) >= 1


def _run_resolve_artifact_by_id(adapter, _: Path) -> None:
    """Resolve committed artifact content by session-scoped ArtifactId."""
    session_id = SessionId("sess-art-contract-4")
    artifact_id = adapter.insert_artifact(
        session_id=session_id,
        step_id=StepId("step-art-contract-4"),
        artifact_type="finding_set",
        name="artifact_id_lookup",
        content={"lookup": "same-session"},
        lifecycle="committed",
        artifact_schema_version="v1",
    )

    result = adapter.resolve_artifact_by_id(session_id, artifact_id)

    assert result == {"lookup": "same-session"}


def _run_resolve_artifact_by_id_missing(adapter, _: Path) -> None:
    """Resolving a missing ArtifactId returns None."""
    result = adapter.resolve_artifact_by_id(
        SessionId("sess-art-contract-5"),
        ArtifactId("art_missing_contract"),
    )
    assert result is None


def _run_resolve_artifact_with_step_by_id(adapter, _: Path) -> None:
    """Resolve committed artifact step id and content by session-scoped ArtifactId."""
    session_id = SessionId("sess-art-contract-step-lookup")
    step_id = StepId("step-art-contract-step-lookup")
    artifact_id = adapter.insert_artifact(
        session_id=session_id,
        step_id=step_id,
        artifact_type="finding_set",
        name="artifact_step_lookup",
        content={"lookup": "step-and-content"},
        lifecycle="committed",
        artifact_schema_version="v1",
    )

    result = adapter.resolve_artifact_with_step_by_id(session_id, artifact_id)

    assert result == (step_id, {"lookup": "step-and-content"})


def _run_resolve_artifact_by_id_cross_session(adapter, _: Path) -> None:
    """ArtifactId lookup is scoped to the requested session."""
    artifact_id = adapter.insert_artifact(
        session_id=SessionId("sess-art-contract-6"),
        step_id=StepId("step-art-contract-6"),
        artifact_type="finding_set",
        name="artifact_id_cross_session",
        content={"lookup": "wrong-session"},
        lifecycle="committed",
        artifact_schema_version="v1",
    )

    result = adapter.resolve_artifact_by_id(SessionId("sess-art-contract-other"), artifact_id)

    assert result is None


def _run_commit_artifact_with_preallocated_id(adapter, _: Path) -> None:
    """Commit with an explicit ArtifactId and store matching content."""
    session_id = SessionId("sess-art-contract-7")
    step_id = StepId("step-art-contract-7")
    artifact_id = ArtifactId("art_fixed")

    committed_id = adapter.commit_artifact_with_extraction(
        session_id=session_id,
        step_id=step_id,
        artifact_type="unknown_type",
        name="preallocated_id",
        content={"artifact_id": artifact_id, "value": 7},
        artifact_id=artifact_id,
    )

    assert committed_id == artifact_id
    assert adapter.resolve_artifact_id_for_step(session_id, step_id) == artifact_id
    assert adapter.resolve_artifact_by_id(session_id, artifact_id) == {
        "artifact_id": artifact_id,
        "value": 7,
    }


ARTIFACT_STORE_CASES = [
    ContractCase(name="insert_artifact", run=_run_insert_artifact),
    ContractCase(name="resolve_artifact_for_ref", run=_run_resolve_artifact_for_ref),
    ContractCase(
        name="resolve_artifact_for_ref_missing", run=_run_resolve_artifact_for_ref_missing
    ),
    ContractCase(name="list_artifacts", run=_run_list_artifacts),
    ContractCase(name="resolve_artifact_by_id", run=_run_resolve_artifact_by_id),
    ContractCase(
        name="resolve_artifact_with_step_by_id",
        run=_run_resolve_artifact_with_step_by_id,
    ),
    ContractCase(
        name="resolve_artifact_by_id_missing",
        run=_run_resolve_artifact_by_id_missing,
    ),
    ContractCase(
        name="resolve_artifact_by_id_cross_session",
        run=_run_resolve_artifact_by_id_cross_session,
    ),
    ContractCase(
        name="commit_artifact_with_preallocated_id",
        run=_run_commit_artifact_with_preallocated_id,
    ),
]
