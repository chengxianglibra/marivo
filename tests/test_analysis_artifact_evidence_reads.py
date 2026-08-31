from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    EvidenceLimitError,
    EvidenceStoreUnavailableError,
    FindingNotFoundError,
)
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import (
    bootstrap_sales_project_from_template,
    connect_sales_orders,
    sales_backends,
)


def _materialized_session(tmp_path: Path):
    bootstrap_sales_project_from_template(tmp_path)
    connection = connect_sales_orders()
    session = mv.session.get_or_create(
        name="slice2-artifact-evidence",
        backends=sales_backends(connection),
    )
    metric = session.observe(make_ref("sales.revenue", SemanticKind.METRIC))
    delta = session.compare(metric, metric)
    return connection, session, metric, delta


def test_artifact_findings_page_is_exact_bounded_and_carries_derivation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    connection, session, _, delta = _materialized_session(tmp_path)
    try:
        page = delta.findings(limit=1)

        assert isinstance(page, mv.FindingPage)
        assert len(page.items) == 1
        assert page.limit == 1
        finding = page.items[0]
        assert isinstance(finding, mv.Finding)
        assert finding.artifact_ref == delta.ref
        assert finding.session_id == session.id
        assert finding.source_artifact_ref == delta.ref
        assert finding.source_fields == finding.derivation.source_fields
        assert finding.source_refs
        assert len(finding.render().encode()) <= 8192
    finally:
        connection.disconnect()
        session_attach._reset_process_state()


def test_artifact_finding_count_matches_complete_paged_read(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    connection, session, _, delta = _materialized_session(tmp_path)
    try:
        page = delta.findings(limit=1)
        findings = list(page.items)
        while page.has_more:
            assert page.next_cursor is not None
            page = delta.findings(limit=1, cursor=page.next_cursor)
            findings.extend(page.items)

        assert len(findings) == delta.finding_count
        assert len({finding.finding_id for finding in findings}) == len(findings)
        assert delta.finding(findings[0].finding_id) == findings[0]
    finally:
        connection.disconnect()
        session_attach._reset_process_state()


def test_exact_finding_rejects_cross_artifact_ownership(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    connection, session, metric, delta = _materialized_session(tmp_path)
    try:
        metric_finding = metric.findings().items[0]

        with pytest.raises(FindingNotFoundError) as exc_info:
            delta.finding(metric_finding.finding_id)

        assert exc_info.value.expected == "an exact Finding id owned by this Artifact"
        assert exc_info.value.location == "artifact.finding(finding_id)"
        assert exc_info.value.repair is not None
        assert exc_info.value.repair.help_target.canonical_id == "artifact.findings"
        assert "artifact.findings(limit=20)" in (exc_info.value.repair.snippet or "")
    finally:
        connection.disconnect()
        session_attach._reset_process_state()


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_artifact_finding_page_rejects_out_of_range_limit(tmp_path, monkeypatch, limit) -> None:
    monkeypatch.chdir(tmp_path)
    connection, session, _, delta = _materialized_session(tmp_path)
    try:
        with pytest.raises(EvidenceLimitError) as exc_info:
            delta.findings(limit=limit)
        assert exc_info.value.location == "artifact.findings(...)"
    finally:
        connection.disconnect()
        session_attach._reset_process_state()


def test_missing_evidence_store_does_not_become_empty_page(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    connection, session, _, delta = _materialized_session(tmp_path)
    try:
        session.close()
        judgment_path = tmp_path / ".marivo" / "analysis" / "sessions" / session.id / "judgment.db"
        judgment_path.rename(judgment_path.with_suffix(".unavailable"))

        with pytest.raises(EvidenceStoreUnavailableError):
            delta.findings()
    finally:
        connection.disconnect()
        session_attach._reset_process_state()
