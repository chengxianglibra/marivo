from pathlib import Path

import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.evidence.types import EvidenceDerivationTrace
from tests.test_analysis_evidence_surface3 import _compare, _session


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()


def test_session_evidence_trace_starts_from_finding(tmp_path: Path) -> None:
    session = _session(tmp_path, name="trace")
    delta = _compare(session)
    finding = session.evidence.findings(artifact_ref=delta.ref).items[0]

    trace = session.evidence.trace(finding.finding_id)

    assert isinstance(trace, EvidenceDerivationTrace)
    assert trace.finding == finding
    assert trace.source_artifact_ref == delta.ref
    assert trace.source_fields == finding.derivation.source_fields
