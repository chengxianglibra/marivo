"""One-call terminal receipt reads and self-contained cold output metadata."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import (
    StoredFrameBacking,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_reading import OutputRead, ReadLayout
from tests.shared_fixtures import DslCase, DslCaseFactory, terminal_runs
from tests.support.paths import PROJECT_ROOT

_Kind = Literal["numeric", "comparison", "calendar", "top", "empty_top"]


def _capture(case: DslCase, kind: _Kind) -> mv.AnalysisFrame:
    session = case.session
    members = session.members(session.catalog.get("entity:sales.customer"))
    metric = session.catalog.get("metric:sales.revenue")
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    query = session.observe({"value": metric}, by=mv.product(scope, members))
    if kind == "comparison":
        baseline = session.observe(
            {"value": metric},
            by=mv.product(session.time_scope(start="2026-07-01", end="2026-08-01"), members),
        )
        query = query.compare(
            {
                "base": mv.baseline(
                    baseline,
                    on=tuple(zip(query.axes, baseline.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        )
    elif kind == "calendar":
        query = session.observe(
            {"value": metric},
            by=(session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day")),),
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
    before = terminal_runs(case.session)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    for _ in range(2):
        opened.clear()
        frame = case.session.persisted_frame(artifact.artifact_id).to_pandas()
        pd.testing.assert_frame_equal(frame, expected)
        assert Counter(opened) == wanted
        count = len(opened)
        # Post-export projection is pandas work and performs no second receipt read.
        assert frame["value"].equals(expected["value"])
        assert len(opened) == count
    assert terminal_runs(case.session) == before
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
    foreign = mv.session.get_or_create("foreign-reader", timezone="UTC")
    opened.clear()
    with pytest.raises(AnalysisError):
        foreign.persisted_frame(artifact.artifact_id)
    assert opened == [] and terminal_runs(case.session) == before


@pytest.mark.slow
def test_cold_projection_reads_once_without_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    ancestor = _capture(case, "top")
    artifact = ancestor.where(ancestor["value"] > 0).persist()
    assert artifact.source_reads == ancestor.source_reads
    expected_source_reads = json.dumps(
        [asdict(read) for read in artifact.source_reads], default=str, sort_keys=True
    )
    with case.session._runtime.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            ("unavailable ancestor", ancestor.artifact_id),
        )
    frame = artifact.to_pandas()
    identity = next(axis for axis in artifact.axes if axis.role == "identity")
    assert frame[identity.name.rsplit(".", 1)[-1]].tolist() == ["B", "A"]
    assert frame["value"].tolist() == [120.0, 60.0]
    expected = frame.to_json(orient="split", date_format="iso")
    assert isinstance(artifact._backing, StoredFrameBacking)
    expected_reads = 1 + len(artifact._backing.data.attachments)
    before = terminal_runs(case.session)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    code = """
import json, sys
from dataclasses import asdict
from pathlib import Path
import marivo.analysis as mv
assert Path(mv.__file__).resolve().parents[2] == Path(sys.argv[6])
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.graph_reading import OutputRead

def forbidden(*args, **kwargs):
    raise AssertionError('cold committed read touched a source')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1])
artifact = session.persisted_frame(sys.argv[2])
assert json.dumps([asdict(read) for read in artifact.source_reads], default=str, sort_keys=True) == sys.argv[5]
reads = []
original = OutputRead._open
def counted(reader,layout):
    reads.append(layout.handle)
    return original(reader,layout)
OutputRead._open = counted
before = len(terminal_runs(session))
for _ in range(2):
    reads.clear()
    assert artifact.to_pandas().to_json(orient='split',date_format='iso') == sys.argv[3]
    assert len(reads) == int(sys.argv[4])
assert len(terminal_runs(session)) == before
continued = artifact.select({"twice": artifact["value"] * 2}).persist()
assert continued.source_reads == artifact.source_reads
assert continued.to_pandas()["twice"].tolist() == [240.0, 120.0]
assert len(terminal_runs(session)) == before + 1
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            case.session.id,
            artifact.artifact_id,
            expected,
            str(expected_reads),
            expected_source_reads,
            str(PROJECT_ROOT),
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(terminal_runs(case.session)) == len(before) + 1
