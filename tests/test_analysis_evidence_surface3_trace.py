from pathlib import Path

import pytest

import marivo.analysis.session.attach as session_attach
from tests.test_analysis_evidence_surface3 import _compare, _session


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()


def test_session_evidence_trace_assembly(tmp_path: Path) -> None:
    session = _session(tmp_path, name="trace")
    delta = _compare(session)
    props = list(session.evidence.propositions(proposition_type="change"))

    trace = session.evidence.trace(props[0].proposition_id)

    assert trace.proposition.proposition_id == props[0].proposition_id
    assert len(trace.seed_findings) >= 1
    support_ids = {f.finding_id for f in trace.support_findings}
    seed_ids = {f.finding_id for f in trace.seed_findings}
    assert seed_ids.issubset(support_ids)
    assert delta.meta.artifact_id in trace.source_artifacts
