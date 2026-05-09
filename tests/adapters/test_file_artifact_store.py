from __future__ import annotations

import json
from pathlib import Path

import pytest

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from marivo.contracts.ids import SessionId, StepId


@pytest.fixture
def store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


def test_insert_and_resolve_for_ref(store: FileArtifactStore) -> None:
    sid = SessionId("s-1")
    step = StepId("step-a")
    store.insert_artifact(
        session_id=sid,
        step_id=step,
        artifact_type="finding",
        name="primary",
        content={"value": 42},
    )
    payload = store.resolve_artifact_for_ref(sid, step)
    assert payload is not None
    assert payload == {"value": 42}


def test_resolve_artifact_id_for_step(store: FileArtifactStore) -> None:
    sid, step = SessionId("s-1"), StepId("step-a")
    aid = store.insert_artifact(sid, step, "finding", "primary", {"x": 1})
    assert store.resolve_artifact_id_for_step(sid, step) == aid


def test_list_artifacts(store: FileArtifactStore) -> None:
    sid = SessionId("s-1")
    store.insert_artifact(sid, StepId("a"), "finding", "n1", {"v": 1})
    store.insert_artifact(sid, StepId("b"), "finding", "n2", {"v": 2})
    rows = store.list_artifacts(sid)
    assert len(rows) == 2
    assert {row["step_id"] for row in rows} == {"a", "b"}


def test_list_filters_by_session(store: FileArtifactStore) -> None:
    store.insert_artifact(SessionId("s-1"), StepId("x"), "finding", "n", {})
    store.insert_artifact(SessionId("s-2"), StepId("y"), "finding", "n", {})
    rows = store.list_artifacts(SessionId("s-1"))
    assert {row["step_id"] for row in rows} == {"x"}


def test_resolve_artifact_for_ref_returns_none_when_missing(
    store: FileArtifactStore,
) -> None:
    result = store.resolve_artifact_for_ref(SessionId("s-nope"), StepId("step-x"))
    assert result is None


def test_resolve_artifact_id_for_step_returns_none_when_missing(
    store: FileArtifactStore,
) -> None:
    result = store.resolve_artifact_id_for_step(SessionId("s-nope"), StepId("step-x"))
    assert result is None


def test_resolve_artifact_with_id(store: FileArtifactStore) -> None:
    sid, step = SessionId("s-1"), StepId("step-a")
    aid = store.insert_artifact(sid, step, "finding", "primary", {"k": "v"})
    result = store.resolve_artifact_with_id(sid, step)
    assert result is not None
    resolved_aid, content = result
    assert resolved_aid == aid
    assert content == {"k": "v"}


def test_resolve_artifact_with_id_returns_none_when_missing(
    store: FileArtifactStore,
) -> None:
    result = store.resolve_artifact_with_id(SessionId("s-nope"), StepId("step-x"))
    assert result is None


def test_staged_artifact_not_visible_via_resolve(store: FileArtifactStore) -> None:
    sid, step = SessionId("s-1"), StepId("step-a")
    store.insert_artifact(sid, step, "finding", "primary", {"x": 1}, lifecycle="staged")
    assert store.resolve_artifact_for_ref(sid, step) is None
    assert store.resolve_artifact_id_for_step(sid, step) is None
    assert store.resolve_artifact_with_id(sid, step) is None


def test_insert_artifact_stores_artifact_schema_version(
    store: FileArtifactStore,
) -> None:
    sid, step = SessionId("s-1"), StepId("step-a")
    aid = store.insert_artifact(
        sid,
        step,
        "finding",
        "primary",
        {"x": 1},
        artifact_schema_version="v2",
    )
    # Verify via resolve_artifact_id_for_step (committed by default)
    assert store.resolve_artifact_id_for_step(sid, step) == aid


def test_commit_artifact_with_extraction_no_extractor(
    store: FileArtifactStore,
) -> None:
    """When no extractor is registered for the artifact_type, commit directly."""
    sid, step = SessionId("s-1"), StepId("step-a")
    aid = store.commit_artifact_with_extraction(
        sid,
        step,
        "unknown_type",
        "primary",
        {"value": 99},
        artifact_schema_version="v1",
    )
    assert aid is not None
    payload = store.resolve_artifact_for_ref(sid, step)
    assert payload == {"value": 99}


def test_commit_artifact_with_extraction_with_extractor(
    store: FileArtifactStore,
) -> None:
    """When an extractor is found, run extraction and write sidecar findings."""
    from marivo.runtime.evidence.finding_extractor_registry import (
        FindingExtractor,
        default_finding_registry,
    )

    class FakeExtractor(FindingExtractor):
        artifact_type = "test_artifact_type_fa"
        artifact_schema_version = "v1"
        extractor_name = "fake_extractor_fa"
        extractor_version = "1.0"
        family = "observe"

        def extract(self, artifact_id, artifact_payload, step_ref, session_id):
            return {
                "findings": [
                    {"finding_id": "fnd_abc", "finding_type": "observation", "payload": {}}
                ],
                "extractor_name": self.extractor_name,
                "extractor_version": self.extractor_version,
                "artifact_schema_version": "v1",
                "finding_count": 1,
            }

    extractor = FakeExtractor()
    default_finding_registry.register(extractor, override=True)

    try:
        sid, step = SessionId("s-1"), StepId("step-a")
        aid = store.commit_artifact_with_extraction(
            sid,
            step,
            "test_artifact_type_fa",
            "primary",
            {"value": 42},
            step_type="observe",
            artifact_schema_version="v1",
        )
        assert aid is not None
        payload = store.resolve_artifact_for_ref(sid, step)
        assert payload == {"value": 42}

        # Check sidecar findings file was written
        session_dir = store._session_dir(sid)
        findings_path = session_dir / f"{step}.findings.json"
        assert findings_path.exists()
        findings_data = json.loads(findings_path.read_text())
        assert len(findings_data) == 1
        assert findings_data[0]["finding_id"] == "fnd_abc"
    finally:
        key = (extractor.artifact_type, extractor.artifact_schema_version)
        del default_finding_registry._registry[key]


def test_commit_artifact_with_extraction_zero_findings_allowed(
    store: FileArtifactStore,
) -> None:
    """Extractor that produces zero findings for an observe family (allowed)."""
    from marivo.runtime.evidence.finding_extractor_registry import (
        FindingExtractor,
        default_finding_registry,
    )

    class ZeroFindingExtractor(FindingExtractor):
        artifact_type = "test_zero_type_fa"
        artifact_schema_version = "v1"
        extractor_name = "zero_extractor_fa"
        extractor_version = "1.0"
        family = "observe"

        def extract(self, artifact_id, artifact_payload, step_ref, session_id):
            return {
                "findings": [],
                "extractor_name": self.extractor_name,
                "extractor_version": self.extractor_version,
                "artifact_schema_version": "v1",
                "finding_count": 0,
            }

    extractor = ZeroFindingExtractor()
    default_finding_registry.register(extractor, override=True)

    try:
        sid, step = SessionId("s-2"), StepId("step-b")
        aid = store.commit_artifact_with_extraction(
            sid,
            step,
            "test_zero_type_fa",
            "primary",
            {"value": 0},
            step_type="observe",
            artifact_schema_version="v1",
        )
        assert aid is not None
        # No sidecar file should be written for zero findings
        session_dir = store._session_dir(sid)
        findings_path = session_dir / f"{step}.findings.json"
        assert not findings_path.exists()
    finally:
        key = (extractor.artifact_type, extractor.artifact_schema_version)
        del default_finding_registry._registry[key]


def test_list_artifacts_empty_for_unknown_session(
    store: FileArtifactStore,
) -> None:
    rows = store.list_artifacts(SessionId("nope"))
    assert rows == []


def test_list_artifacts_includes_metadata_and_content(
    store: FileArtifactStore,
) -> None:
    sid = SessionId("s-1")
    store.insert_artifact(sid, StepId("step-a"), "observation_artifact", "primary", {"x": 1})
    rows = store.list_artifacts(sid)
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact_type"] == "observation_artifact"
    assert row["step_id"] == "step-a"
    assert row["content"] == {"x": 1}
