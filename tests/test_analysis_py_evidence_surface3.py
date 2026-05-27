from pathlib import Path

import ibis
import pytest

import marivo.analysis_py as ap
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import PropositionNotFoundError
from marivo.analysis_py.evidence.types import Assessment, Finding, Proposition, Subject
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()


def _seed(con: ibis.BaseBackend) -> None:
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-05-01', 100.0, 'us', 1),"
        "(2, DATE '2026-05-02', 120.0, 'us', 2),"
        "(3, DATE '2026-04-24', 90.0, 'us', 1),"
        "(4, DATE '2026-04-25', 80.0, 'us', 2)"
    )


def _session(tmp_path: Path, *, name: str = "t"):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    bootstrap_sales_project(tmp_path)
    return ap.session.attach.create(
        name=name, backends={"warehouse": lambda: con}, use_datasources=False
    )


def _compare(session):
    cur = ap.observe(
        metric=ap.MetricRef("sales.revenue"),
        window={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    bas = ap.observe(
        metric=ap.MetricRef("sales.revenue"),
        window={"start": "2026-04-24", "end": "2026-04-30"},
        session=session,
    )
    return ap.compare(cur, bas, session=session)


def test_session_findings_returns_iterable_of_finding(tmp_path: Path) -> None:
    session = _session(tmp_path)
    delta = _compare(session)

    findings = list(session.findings(artifact=delta.meta.artifact_id))

    assert len(findings) >= 1
    assert all(isinstance(f, Finding) for f in findings)
    assert all(f.artifact_id == delta.meta.artifact_id for f in findings)


def test_session_findings_filter_by_type(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)

    deltas = list(session.findings(finding_type="delta"))

    assert deltas
    assert all(f.finding_type == "delta" for f in deltas)


def test_session_findings_filter_by_subject(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)

    findings = list(
        session.findings(subject=Subject(metric="sales.revenue", slice={}, analysis_axis="change"))
    )

    assert findings
    assert all(f.subject.metric == "sales.revenue" for f in findings)


def test_session_propositions_returns_iterable_of_proposition(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)

    props = list(session.propositions(type="change"))

    assert len(props) >= 1
    assert all(isinstance(p, Proposition) for p in props)


def test_session_propositions_filter_by_status(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)

    props = list(session.propositions(status="validated"))

    assert props
    assert all(isinstance(p, Proposition) for p in props)


def test_session_assessments_latest_only_default(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)

    assessments = list(session.assessments())

    assert assessments
    assert all(isinstance(a, Assessment) for a in assessments)
    assert all(a.is_latest for a in assessments)


def test_session_evidence_proposition_lookup(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)
    props = list(session.propositions(type="change"))

    prop = session.evidence.proposition(props[0].proposition_id)

    assert prop.proposition_id == props[0].proposition_id


def test_session_evidence_proposition_not_found_raises(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(PropositionNotFoundError):
        session.evidence.proposition("prop_does_not_exist")


def test_session_evidence_latest_assessment(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _compare(session)
    props = list(session.propositions(type="change"))

    assessment = session.evidence.latest_assessment(props[0].proposition_id)

    assert assessment is not None
    assert assessment.proposition_id == props[0].proposition_id
    assert assessment.is_latest is True
