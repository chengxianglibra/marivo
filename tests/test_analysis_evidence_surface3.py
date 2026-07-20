"""Direct digest/finding reads over a real analysis session."""

from pathlib import Path

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import FindingNotFoundError
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    ArtifactDigestPage,
    Finding,
    FindingPage,
)
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref


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
    return session_attach.get_or_create(
        name=name, backends={"warehouse": lambda: con}, use_datasources=False
    )


def _compare(session):
    from marivo.semantic.catalog import SemanticKind

    current = session.observe(
        metric=make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
    )
    baseline = session.observe(
        metric=make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-24", "end": "2026-04-30"},
    )
    return session.compare(current, baseline)


def test_findings_returns_concrete_page_and_new_filters(tmp_path: Path):
    session = _session(tmp_path)
    delta = _compare(session)
    page = session.evidence.findings(
        artifact_ref=delta.ref,
        kind="delta",
        limit=10,
    )
    assert isinstance(page, FindingPage)
    assert page.items
    assert all(isinstance(item, Finding) for item in page.items)
    assert all(item.artifact_id == delta.ref for item in page.items)
    assert all(item.finding_type == "delta" for item in page.items)


def test_findings_filter_by_exact_subject(tmp_path: Path):
    session = _session(tmp_path)
    delta = _compare(session)
    finding = session.evidence.findings(artifact_ref=delta.ref).items[0]
    page = session.evidence.findings(
        subject=finding.subject,
    )
    assert page.items
    assert all(item.subject == finding.subject for item in page.items)


def test_digests_page_and_exact_digest_share_persisted_value(tmp_path: Path):
    session = _session(tmp_path)
    delta = _compare(session)
    page = session.evidence.digests(operator="compare")
    exact = session.evidence.digest(delta.ref)
    assert isinstance(page, ArtifactDigestPage)
    assert isinstance(exact, ArtifactDigest)
    assert exact in page.items
    assert exact == delta.evidence_digest


def test_finding_exact_lookup_and_not_found(tmp_path: Path):
    session = _session(tmp_path)
    delta = _compare(session)
    finding = session.evidence.findings(artifact_ref=delta.ref).items[0]
    assert session.evidence.finding(finding.finding_id) == finding
    with pytest.raises(FindingNotFoundError):
        session.evidence.finding("fnd_does_not_exist")


def test_removed_judgment_reads_are_not_advertised(tmp_path: Path):
    namespace = _session(tmp_path).evidence
    for removed in (
        "propositions",
        "assessments",
        "proposition",
        "latest_assessment",
    ):
        assert not hasattr(namespace, removed)
