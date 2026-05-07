from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.adapters.local.file_evidence_store import FileEvidenceStore
from app.contracts.errors import IntegrityError, NotFoundError
from app.contracts.evidence import Evidence, Finding, Proposition
from app.contracts.ids import ArtifactId, EvidenceRef, FindingId, PropositionId, SessionId


def _make_file_evidence_store(tmp_path: Path) -> FileEvidenceStore:
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    return FileEvidenceStore(ev_dir)


evidence_store_factories = [
    ("FileEvidenceStore", _make_file_evidence_store),
]


def _sample_session_id() -> SessionId:
    return SessionId(f"sess-{uuid.uuid4().hex[:12]}")


def _sample_evidence() -> Evidence:
    session_id = _sample_session_id()
    return Evidence(
        ref=EvidenceRef("placeholder"),
        findings=[
            Finding(
                finding_id=FindingId(f"find-{uuid.uuid4().hex[:8]}"),
                session_id=session_id,
                artifact_id=ArtifactId(f"art-{uuid.uuid4().hex[:8]}"),
                finding_type="test",
                content={"description": "test finding"},
            )
        ],
        proposition=Proposition(
            proposition_id=PropositionId(f"prop-{uuid.uuid4().hex[:8]}"),
            session_id=session_id,
            identity_key="test_identity",
            description="test proposition",
        ),
    )


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_write_returns_ref(name, factory, tmp_path):
    store = factory(tmp_path)
    evidence = _sample_evidence()
    ref = store.write(evidence)
    assert isinstance(ref, str)
    assert len(ref) == 64  # SHA-256 hex digest


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_read_roundtrip(name, factory, tmp_path):
    store = factory(tmp_path)
    evidence = _sample_evidence()
    ref = store.write(evidence)
    loaded = store.read(ref)
    assert loaded.findings[0].finding_type == "test"


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_read_not_found_raises(name, factory, tmp_path):
    store = factory(tmp_path)
    with pytest.raises(NotFoundError):
        store.read(EvidenceRef("0" * 64))


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_write_is_idempotent(name, factory, tmp_path):
    store = factory(tmp_path)
    evidence = _sample_evidence()
    ref1 = store.write(evidence)
    ref2 = store.write(evidence)
    assert ref1 == ref2
    ev_files = list((tmp_path / "evidence").glob("*.json"))
    assert len(ev_files) == 1


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_hash_determinism(name, factory, tmp_path):
    store = factory(tmp_path)
    evidence = _sample_evidence()
    ref1 = store.write(evidence)
    store2 = FileEvidenceStore(tmp_path / "evidence")
    ref2 = store2.write(evidence)
    assert ref1 == ref2


@pytest.mark.parametrize("name,factory", evidence_store_factories)
def test_read_integrity_error_on_corrupt_file(name, factory, tmp_path):
    store = factory(tmp_path)
    evidence = _sample_evidence()
    ref = store.write(evidence)
    # Corrupt the file by overwriting it
    path = tmp_path / "evidence" / f"{ref}.json"
    path.write_text('{"corrupted": true}', encoding="utf-8")
    with pytest.raises(IntegrityError):
        store.read(ref)
