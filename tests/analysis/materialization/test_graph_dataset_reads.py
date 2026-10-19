"""One-call terminal receipt reads, bounded record paging and cold output reads."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from typing import Literal
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.artifact_data import (
    OutputEvidence,
    OutputFinding,
)
from marivo.analysis.core.frame_audit import FrameAudit
from marivo.analysis.core.frame_data import (
    CompletedFrameResult,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.evidence._dataset_types import AssociationFindingValueV1, NoAssociationLagV1
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.graph_reading import OutputRead, ReadLayout
from marivo.analysis.materialization.store import SessionStore
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.paths import PROJECT_ROOT

_Kind = Literal["numeric", "comparison", "calendar", "top", "empty_top"]


def _capture(case: DslCase, kind: _Kind) -> mv.AnalysisFrame:
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    metric = session.catalog.metrics.get("sales.revenue")
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    query = session.observe({"value": metric}, during=scope, by=members)
    if kind == "comparison":
        baseline = session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-07-01", end="2026-08-01"),
            by=members,
        )
        query = query.compare(baseline, on=tuple(zip(query.axes, baseline.axes, strict=True)))
    elif kind == "calendar":
        query = session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day")),
        )
    elif kind in ("top", "empty_top"):
        query = query.top(0 if kind == "empty_top" else 2, by=query["value"])
    else:
        query = query.select({"value": query["value"], "twice": query["value"] * 2})
    return query.persist()


@pytest.mark.parametrize("kind", ("numeric", "comparison", "calendar", "top", "empty_top"))
def test_projection_reads_each_receipt_once_per_call(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, kind: _Kind
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = _capture(case, kind)
    expected = artifact.to_pandas()
    assert isinstance(artifact._backing, StoredFrameBacking)
    data = artifact._backing.data
    handles = (data.rows, *(part.rows for part in data.attachments))
    wanted = Counter(handle.receipt.project_relative_path for handle in handles)
    opened: list[str] = []
    original = OutputRead._open

    def counted(reader: OutputRead, layout: ReadLayout) -> pq.ParquetFile:
        opened.append(layout.handle.receipt.project_relative_path)
        return original(reader, layout)

    monkeypatch.setattr(OutputRead, "_open", counted)
    before = case.session.runs().items
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    for _ in range(2):
        opened.clear()
        frame = case.session.persisted_frame(artifact.artifact.id).to_pandas()
        pd.testing.assert_frame_equal(frame, expected)
        assert Counter(opened) == wanted
        count = len(opened)
        # Post-export projection is pandas work and performs no second receipt read.
        assert frame["value"].equals(expected["value"])
        assert len(opened) == count
    assert case.session.runs().items == before
    for handle in handles:
        path = (
            case.root
            / handle.receipt.project_relative_path
            / handle.receipt.file_manifest[0].relative_path
        )
        original_bytes = path.read_bytes()
        try:
            path.write_bytes(b"damaged committed output projection")
            with pytest.raises(AnalysisError):
                artifact.to_pandas()
        finally:
            path.write_bytes(original_bytes)
    foreign = mv.session.get_or_create("foreign-reader", report_timezone="UTC")
    opened.clear()
    with pytest.raises(AnalysisError):
        foreign.persisted_frame(artifact.artifact.id)
    assert opened == [] and case.session.runs().items == before


def _finding_output(session: mv.Session) -> mv.Artifact:
    """Seed only the production terminal writer boundary targeted by paging tests."""
    nonce = uuid4().hex
    findings = tuple(
        OutputFinding(
            "finding_" + nonce + "_" + str(index),
            AssociationFindingValueV1(
                method="pearson",
                coefficient=0.5,
                input_observation_count=3,
                null_pair_count=0,
                complete_pair_count=3,
                lag=NoAssociationLagV1(),
            ),
        )
        for index in range(8)
    )
    evidence = (OutputEvidence("check", "Explicit reader fixture", (("result", "passed"),)),)
    frame = session.values(
        by=(), columns={"coefficient": None}, rows=(((), {"coefficient": 0.5}),)
    ).compute()
    assert isinstance(frame._backing, MemoryFrameBacking)
    metadata = replace(
        frame._backing.metadata,
        findings=findings,
        audit=FrameAudit(evidence, frame._backing.metadata.audit.evaluation),
    )
    return (
        AnalysisFrame._from_memory(
            session._runtime.local, CompletedFrameResult(frame._backing.data, metadata)
        )
        .persist()
        .artifact
    )


def test_multiple_findings_read_once_and_keep_owned_paging(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = _finding_output(case.session)
    other = _finding_output(case.session)
    calls = 0
    page_reader = terminal_store.records_page

    def counted(
        store: SessionStore,
        ref: str,
        *,
        kind: Literal["finding", "evidence"],
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[tuple[OutputFinding | OutputEvidence, ...], str | None]:
        nonlocal calls
        calls += 1
        return page_reader(store, ref, kind=kind, limit=limit, cursor=cursor)

    monkeypatch.setattr(terminal_store, "records_page", counted)
    assert artifact.evidence_digest().finding_count == 8
    assert calls == 0
    seen: list[str] = []
    cursor = None
    while True:
        calls = 0
        page = artifact.findings(limit=3, cursor=cursor)
        assert calls == 1 and len(page.items) <= 3
        seen.extend(item.finding_id for item in page.items)
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 8
    first = artifact.findings(limit=3)
    calls = 0
    with pytest.raises(AnalysisError):
        artifact.findings(cursor="invalid")
    assert calls == 1
    calls = 0
    with pytest.raises(AnalysisError):
        other.findings(cursor=first.next_cursor)
    assert calls == 1


@pytest.mark.slow
def test_cold_projection_reads_once_without_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = _capture(case, "top")
    frame = artifact.to_pandas()
    assert frame.iloc[:, 0].tolist() == ["B", "A"]
    assert frame[("value", "value")].tolist() == [120.0, 60.0]
    expected = frame.to_json(orient="split", date_format="iso")
    assert isinstance(artifact._backing, StoredFrameBacking)
    expected_reads = 1 + len(artifact._backing.data.attachments)
    before = case.session.runs().items
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    code = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.graph_reading import OutputRead

def forbidden(*args, **kwargs):
    raise AssertionError('cold committed read touched a source')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1],by='id')
artifact = session.persisted_frame(sys.argv[2])
reads = []
original = OutputRead._open
def counted(reader,layout):
    reads.append(layout.handle)
    return original(reader,layout)
OutputRead._open = counted
before = len(session.runs().items)
for _ in range(2):
    reads.clear()
    assert artifact.to_pandas().to_json(orient='split',date_format='iso') == sys.argv[3]
    assert len(reads) == int(sys.argv[4])
assert len(session.runs().items) == before
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            case.session.id,
            artifact.artifact.id,
            expected,
            str(expected_reads),
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert case.session.runs().items == before
