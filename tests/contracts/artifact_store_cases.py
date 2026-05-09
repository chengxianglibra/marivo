from __future__ import annotations

from pathlib import Path

from app.contracts.ids import SessionId, StepId
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


ARTIFACT_STORE_CASES = [
    ContractCase(name="insert_artifact", run=_run_insert_artifact),
    ContractCase(name="resolve_artifact_for_ref", run=_run_resolve_artifact_for_ref),
    ContractCase(
        name="resolve_artifact_for_ref_missing", run=_run_resolve_artifact_for_ref_missing
    ),
    ContractCase(name="list_artifacts", run=_run_list_artifacts),
]
