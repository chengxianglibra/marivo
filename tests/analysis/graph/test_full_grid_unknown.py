"""Explicit business coverage masks consume complete terminal calendar exports."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.pandas_workflows import forecast, receipt_paths
from tests.analysis.statistics.runs_worker import segments
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import encode
from tests.support.paths import PROJECT_ROOT

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _query(session: mv.Session) -> mv.AnalysisFrame:
    period = session.time_scope(start=START, end=START + timedelta(days=3))
    return session.observe(
        {"value": session.catalog.metrics.get("sales.total_0")},
        during=period.by(mv.grain("day")),
        by=session.members(ms.ref.entity("sales.order")),
    )


def _mask(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the original three dates and apply a separate business policy."""
    result = frame.copy(deep=True)
    calendar = next(axis for axis in frame.attrs["axes"] if axis["role"] == "calendar")
    name = calendar["token"]
    descriptor = calendar["descriptor"]
    directory = next(
        part["rows"]
        for part in frame.attrs["frozen_coordinates"]
        if part["name"] == descriptor["frozen"]["directory_name"]
    )
    key = descriptor["columns"][0]["slot_id"]
    uncovered_keys = directory.loc[directory["actual_start"] == START + timedelta(days=1), key]
    uncovered = result[("coordinate", name)].isin(uncovered_keys)
    assert uncovered.sum() == 3
    result.loc[uncovered, ("value", "value")] = pd.NA
    return result


@pytest.mark.slow
def test_public_full_grid_unknown(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True, profile_indices=(0,))
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("business-coverage", report_timezone="UTC")
    artifact = _query(session).persist()
    raw = artifact.to_pandas()
    assert len(raw) == 9 and not raw[("value", "value")].isna().any()
    assert sorted(raw[("value", "value")].tolist()) == [0] * 6 + [1, 2, 7]
    frame = _mask(raw)
    assert frame[("value", "value")].isna().sum() == 3
    assert sorted(frame[("value", "value")].dropna().tolist()) == [0] * 4 + [1, 7]
    artifact.show(n=0)
    text = capsys.readouterr().out
    assert "rows: 9" in text and ".to_pandas()" in text
    # The terminal source result has no external business-completeness claim.
    # The mask belongs to this pandas workflow and never changes saved values.
    assert artifact.to_pandas().equals(raw)
    before = len(session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("business-mask consumer accessed its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    runs = segments(frame, threshold=-1)
    assert runs["count"].tolist() == [1] * 6
    assert runs["duration"].tolist() == [timedelta(days=1)] * 6
    assert sorted(runs["start"].tolist()) == [START] * 3 + [START + timedelta(days=2)] * 3
    assert (
        sorted(runs["end"].tolist())
        == [START + timedelta(days=1)] * 3 + [START + timedelta(days=3)] * 3
    )
    # A raw forecast recipe cannot silently consume a business-masked NA.
    with pytest.raises(ValueError):
        forecast(frame[("value", "value")])
    assert len(forecast(raw[("value", "value")])) == 2
    assert len(session.runs().items) == before
    for path in receipt_paths(case.root, artifact):
        contents = path.read_bytes()
        try:
            path.write_bytes(b"invalid calendar output")
            with pytest.raises(AnalysisError):
                artifact.to_pandas()
        finally:
            path.write_bytes(contents)
    (case.root / "business-mask.json").write_bytes(
        encode(
            {
                "session": session.id,
                "artifact": artifact.artifact.id,
                "snapshot": snapshot(artifact),
            }
        )
    )
    for path in (case.root / "source_files").glob("*.parquet"):
        path.rename(path.with_suffix(".offline"))
    shutil.rmtree(case.root / "models")
    script = """
import json
from pathlib import Path
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.test_full_grid_unknown import _mask
from tests.analysis.statistics.runs_worker import segments
from tests.analysis.materialization.domain_recovery_worker import snapshot

def forbidden(*args, **kwargs):
    raise AssertionError('cold business mask read live source or Semantic')
ms.load = forbidden
SemanticProject.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
state = json.loads(Path('business-mask.json').read_text())
session = mv.session.resume(state['session'], by='id')
before = len(session.runs().items)
output = session.persisted_frame(state['artifact'])
assert snapshot(output) == state['snapshot']
masked = _mask(output.to_pandas())
assert masked[('value','value')].isna().sum() == 3
assert segments(masked, threshold=-1)['count'].tolist() == [1]*6
assert len(session.runs().items) == before
"""
    cold = subprocess.run(
        [sys.executable, "-c", script],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_public_duration_business_coverage(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True, profile_indices=(0,))
    path = case.root / "source_files/order.parquet"
    table = pq.read_table(path)
    index = table.schema.get_field_index("profile_0")
    pq.write_table(
        table.set_column(index, "profile_0", table["profile_0"].cast(pa.duration("us"))), path
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("duration-business-coverage", report_timezone="UTC")
    query = _query(session)
    original = query.persist()
    ratio = query.select({"value": query["value"] / query["value"]}).persist()
    original_values = original.to_pandas()[("value", "value")]
    dtype = original_values.dtype
    assert isinstance(dtype, pd.ArrowDtype) and dtype.pyarrow_dtype == pa.duration("us")
    assert _mask(original.to_pandas())[("value", "value")].isna().sum() == 3
    before = len(session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Duration business mask touched its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    frame = _mask(ratio.to_pandas())
    assert frame[("value", "value")].isna().sum() == 7
    assert frame[("value", "value")].dropna().tolist() == [1.0, 1.0]
    assert len(session.runs().items) == before


def test_business_coverage_source_selection_keeps_original_support(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True, profile_indices=(0,))
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("business-coverage-selection", report_timezone="UTC")
    original = _query(session).persist()
    raw = original.to_pandas()
    masked = _mask(raw)
    selected = masked.loc[masked[("value", "value")].notna()].copy()
    assert len(selected) == 6
    # Selecting represented data keeps the original nominal and actual bounds;
    # a removed middle date must never make the two surviving dates adjacent.
    runs = segments(selected, threshold=-1)
    assert runs["count"].tolist() == [1] * 6
    assert runs["duration"].tolist() == [timedelta(days=1)] * 6
    assert original.to_pandas().equals(raw)
    assert session._runtime.store.resources(session.id) == ()
