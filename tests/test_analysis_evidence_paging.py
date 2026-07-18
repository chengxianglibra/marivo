"""Simple keyset paging for session recap and direct evidence audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import EvidenceStoreUnavailableError
from marivo.analysis.evidence.audit import query_digests, query_findings
from marivo.analysis.evidence.store import open_evidence_store
from marivo.analysis.evidence.types import ArtifactDigestPage, FindingPage
from marivo.analysis.session.core import FrameSummaryPage
from tests.test_analysis_evidence_pipeline import _commit


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _walk(page, fetch_next):
    items = list(page.items)
    while page.has_more:
        assert page.next_cursor is not None
        page = fetch_next(page.next_cursor)
        items.extend(page.items)
    assert page.next_cursor is None
    return items


def test_digest_and_finding_pages_are_bounded_complete_and_duplicate_free(tmp_path: Path):
    for ordinal in range(7):
        _, store = _commit(tmp_path, ordinal=ordinal)
        assert store is not None
        store.close()
    store = open_evidence_store(tmp_path / "judgment.db")
    try:
        digest_page = query_digests(store=store, session_id="sess_1", limit=3)
        finding_page = query_findings(store=store, session_id="sess_1", limit=5)
        assert isinstance(digest_page, ArtifactDigestPage)
        assert isinstance(finding_page, FindingPage)
        digests = _walk(
            digest_page,
            lambda cursor: query_digests(store=store, session_id="sess_1", limit=3, cursor=cursor),
        )
        findings = _walk(
            finding_page,
            lambda cursor: query_findings(store=store, session_id="sess_1", limit=5, cursor=cursor),
        )
        assert len(digests) == 7
        assert len({item.artifact_ref for item in digests}) == 7
        assert len(findings) == 14
        assert len({item.finding_id for item in findings}) == 14
    finally:
        store.close()


def test_commit_between_pages_has_ordinary_non_snapshot_keyset_semantics(tmp_path: Path):
    for ordinal in range(4):
        _, store = _commit(tmp_path, ordinal=ordinal)
        assert store is not None
        store.close()
    store = open_evidence_store(tmp_path / "judgment.db")
    first = query_digests(store=store, session_id="sess_1", limit=2)
    first_refs = {item.artifact_ref for item in first.items}
    assert first.next_cursor is not None
    store.close()

    new_result, new_store = _commit(tmp_path, ordinal=99)
    assert new_store is not None
    second = query_digests(
        store=new_store,
        session_id="sess_1",
        limit=2,
        cursor=first.next_cursor,
    )
    assert first_refs.isdisjoint(item.artifact_ref for item in second.items)
    assert new_result.ref not in {item.artifact_ref for item in second.items}
    new_store.close()


def test_frame_summary_page_filters_without_evidence_db_join(tmp_path: Path):
    session = session_attach.get_or_create(name="demo")
    for index in range(23):
        ref = f"art_{index:03d}"
        relative = Path(".marivo") / "analysis" / "manual" / ref / "meta.json"
        absolute = tmp_path / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(
            json.dumps(
                {
                    "ref": ref,
                    "kind": "metric_frame" if index % 2 == 0 else "delta_frame",
                    "metric_id": f"sales.metric_{index}",
                    "semantic_kind": "scalar",
                    "semantic_model": "sales",
                    "created_at": f"2026-07-18T00:00:{index:02d}+00:00",
                    "row_count": index,
                }
            )
        )
        session._store.record_artifact(
            session_id=session.id,
            artifact_id=ref,
            kind="metric_frame" if index % 2 == 0 else "delta_frame",
            path=str(relative.with_name("data.parquet")),
            meta_path=str(relative),
            content_hash=None,
            produced_by_job=None,
            evidence_status="complete" if index % 3 else "partial",
        )

    first = session.frame_summaries(limit=7)
    assert isinstance(first, FrameSummaryPage)
    all_entries = _walk(
        first,
        lambda cursor: session.frame_summaries(limit=7, cursor=cursor),
    )
    assert len(all_entries) == 23
    assert len({entry.ref for entry in all_entries}) == 23
    filtered = session.frame_summaries(kind="metric_frame", evidence_status="complete", limit=100)
    assert all(entry.kind == "metric_frame" for entry in filtered.items)
    assert all(entry.evidence_status == "complete" for entry in filtered.items)


def test_frame_summary_pages_remain_complete_when_sidecars_are_missing(tmp_path: Path):
    session = session_attach.get_or_create(name="missing-sidecars")
    for index in range(5):
        ref = f"art_{index}"
        relative = Path(".marivo") / "analysis" / "manual" / ref / "meta.json"
        absolute = tmp_path / relative
        if index != 4:
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_text(json.dumps({"ref": ref, "kind": "metric_frame"}))
        session._store.record_artifact(
            session_id=session.id,
            artifact_id=ref,
            kind="metric_frame",
            path=str(relative.with_name("data.parquet")),
            meta_path=str(relative),
            content_hash=None,
            produced_by_job=None,
            evidence_status="complete",
        )

    entries = _walk(
        session.frame_summaries(limit=2),
        lambda cursor: session.frame_summaries(limit=2, cursor=cursor),
    )
    assert len(entries) == 5
    assert len({entry.ref for entry in entries}) == 5
    assert {entry.ref for entry in entries} == {f"art_{index}" for index in range(5)}


@pytest.mark.parametrize("method", ["digests", "findings"])
def test_evidence_collection_reads_fail_typed_when_store_unavailable(method):
    session = session_attach.get_or_create(name="demo")
    session._judgment_store_unavailable = True
    with pytest.raises(EvidenceStoreUnavailableError):
        getattr(session.evidence, method)()


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_all_public_pages_reject_out_of_range_limits(tmp_path: Path, limit: int):
    session = session_attach.get_or_create(name="demo")
    with pytest.raises(ValueError):
        session.frame_summaries(limit=limit)
    store = open_evidence_store(tmp_path / "judgment.db")
    try:
        with pytest.raises(ValueError):
            query_digests(store=store, session_id="sess_1", limit=limit)
        with pytest.raises(ValueError):
            query_findings(store=store, session_id="sess_1", limit=limit)
    finally:
        store.close()
